"""Consultas e resumo horário por sensor (opções 2 e 3 do menu).

Medição válida
--------------
Só entram nos resumos as medições em que todas as grandezas existem, são
numéricas (não nulas, não texto, não NaN) e estão numa faixa física plausível:

=============  =================  ==========================================
campo          faixa aceita       motivo
=============  =================  ==========================================
pm25, pm10     >= 0 µg/m³         concentração não pode ser negativa
co2            >= 0 ppm           idem
temperatura    -50 a 60 °C        fora disso é falha de leitura do sensor
umidade        0 a 100 %          umidade relativa é percentual
=============  =================  ==========================================

O validador ``$jsonSchema`` de ``Medicoes`` (``app/sensores.py``) já recusa a
maior parte desses casos na inserção. O filtro é repetido no ``$match`` porque
documentos gravados antes do validador, ou com ``bypass_document_validation``,
não passaram por ele. A consulta por intervalo (opção 2) mostra todas as
medições e marca as inválidas com ``*``.

Os resumos são dicionários Python com os campos de ``CAMPOS_RESUMO``;
``para_redis`` os converte nos campos de texto do Hash (seção 6 do enunciado).
Horas sem nenhuma medição válida não geram resumo.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import math
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database

from app.db import MEDICOES, SENSORES
from app.periodo import UMA_HORA, formatar, formatar_iso, janela_horaria, parse_data_utc, para_utc

GRANDEZAS = ("pm25", "pm10", "co2", "temperatura", "umidade")

# (mínimo, máximo) inclusivos; None = sem limite.
FAIXAS_VALIDAS: dict[str, tuple[float | None, float | None]] = {
    "pm25": (0, None),
    "pm10": (0, None),
    "co2": (0, None),
    "temperatura": (-50, 60),
    "umidade": (0, 100),
}

CAMPOS_RESUMO = (
    "sensor_id",
    "inicio_periodo",
    "fim_periodo",
    "qt_medicoes",
    "pm25_media",
    "pm10_media",
    "co2_media",
    "temperatura_media",
    "umidade_media",
    "pm25_max",
    "pm10_max",
)

CASAS_DECIMAIS = 2


def _filtro_validade() -> dict[str, Any]:
    filtro: dict[str, Any] = {}
    for campo, (minimo, maximo) in FAIXAS_VALIDAS.items():
        # $type "number" descarta ausentes, null e texto; a faixa descarta NaN,
        # que no MongoDB é menor que qualquer número.
        condicao: dict[str, Any] = {"$type": "number"}
        condicao["$gte"] = minimo if minimo is not None else -math.inf
        if maximo is not None:
            condicao["$lte"] = maximo
        filtro[campo] = condicao
    return filtro


FILTRO_MEDICAO_VALIDA = _filtro_validade()


def medicao_valida(doc: dict[str, Any]) -> bool:
    """Mesma regra de ``FILTRO_MEDICAO_VALIDA``, aplicada em Python."""
    for campo, (minimo, maximo) in FAIXAS_VALIDAS.items():
        valor = doc.get(campo)
        if isinstance(valor, bool) or not isinstance(valor, (int, float)) or math.isnan(valor):
            return False
        if minimo is not None and valor < minimo:
            return False
        if maximo is not None and valor > maximo:
            return False
    return True


def consultar_medicoes(
    db: Database,
    sensor_id: str,
    inicio: datetime,
    fim: datetime,
    *,
    apenas_validas: bool = False,
) -> list[dict[str, Any]]:
    """Medições do sensor com ``inicio <= timestamp < fim``, em ordem cronológica."""
    filtro: dict[str, Any] = {
        "sensor_id": sensor_id,
        "timestamp": {"$gte": para_utc(inicio), "$lt": para_utc(fim)},
    }
    if apenas_validas:
        filtro.update(FILTRO_MEDICAO_VALIDA)
    cursor = db[MEDICOES].find(filtro, {"_id": 0}).sort("timestamp", ASCENDING)
    return list(cursor)


def pipeline_resumo_horario(
    sensor_id: str | None,
    inicio: datetime,
    fim: datetime,
) -> list[dict[str, Any]]:
    """Pipeline com um resumo por sensor e hora cheia dentro de ``[inicio, fim)``.

    ``sensor_id=None`` resume todos os sensores.
    """
    match: dict[str, Any] = {"timestamp": {"$gte": para_utc(inicio), "$lt": para_utc(fim)}}
    if sensor_id is not None:
        match["sensor_id"] = sensor_id
    match.update(FILTRO_MEDICAO_VALIDA)

    hora = {"$dateTrunc": {"date": "$timestamp", "unit": "hour", "timezone": "UTC"}}
    grupo: dict[str, Any] = {
        "_id": {"sensor_id": "$sensor_id", "inicio_periodo": hora},
        "qt_medicoes": {"$sum": 1},
    }
    for campo in GRANDEZAS:
        grupo[f"{campo}_media"] = {"$avg": f"${campo}"}
    grupo["pm25_max"] = {"$max": "$pm25"}
    grupo["pm10_max"] = {"$max": "$pm10"}

    projecao: dict[str, Any] = {
        "_id": 0,
        "sensor_id": "$_id.sensor_id",
        "inicio_periodo": "$_id.inicio_periodo",
        "fim_periodo": {"$dateAdd": {"startDate": "$_id.inicio_periodo", "unit": "hour", "amount": 1}},
        "qt_medicoes": 1,
    }
    for campo in GRANDEZAS:
        projecao[f"{campo}_media"] = {"$round": [f"${campo}_media", CASAS_DECIMAIS]}
    projecao["pm25_max"] = 1
    projecao["pm10_max"] = 1

    return [
        {"$match": match},
        {"$group": grupo},
        {"$sort": {"_id.sensor_id": 1, "_id.inicio_periodo": 1}},
        {"$project": projecao},
    ]


def _ordenar(resumo: dict[str, Any]) -> dict[str, Any]:
    return {campo: resumo[campo] for campo in CAMPOS_RESUMO}


def resumos_horarios(
    db: Database,
    sensor_id: str | None,
    inicio: datetime,
    fim: datetime,
) -> list[dict[str, Any]]:
    """Resumos de cada hora com medições válidas no intervalo, como dicionários."""
    resultado = db[MEDICOES].aggregate(pipeline_resumo_horario(sensor_id, inicio, fim))
    return [_ordenar(r) for r in resultado]


def resumo_horario_sensor(db: Database, sensor_id: str, hora: datetime) -> dict[str, Any] | None:
    """Resumo da hora que contém ``hora`` (ex.: 10:37 -> 10:00 a 11:00), ou None."""
    inicio, fim = janela_horaria(hora)
    resumos = resumos_horarios(db, sensor_id, inicio, fim)
    return resumos[0] if resumos else None


def ultimo_resumo_sensor(db: Database, sensor_id: str) -> dict[str, Any] | None:
    """Resumo da hora da medição válida mais recente do sensor, ou None.

    Com o simulador em modo contínuo essa hora pode estar incompleta
    (``qt_medicoes`` menor que 12).
    """
    mais_recente = db[MEDICOES].find_one(
        {"sensor_id": sensor_id, **FILTRO_MEDICAO_VALIDA},
        {"timestamp": 1},
        sort=[("timestamp", DESCENDING)],
    )
    if mais_recente is None:
        return None
    return resumo_horario_sensor(db, sensor_id, mais_recente["timestamp"])


def ultimos_resumos_por_sensor(db: Database) -> dict[str, dict[str, Any]]:
    """Último resumo de cada sensor cadastrado, indexado por ``sensor_id``.

    Sensores sem medição válida ficam de fora.
    """
    resumos: dict[str, dict[str, Any]] = {}
    for sensor in db[SENSORES].find({}, {"_id": 1}).sort("_id", ASCENDING):
        resumo = ultimo_resumo_sensor(db, sensor["_id"])
        if resumo is not None:
            resumos[sensor["_id"]] = resumo
    return resumos


def para_redis(resumo: dict[str, Any]) -> dict[str, str]:
    """Campos do Hash ``qualidadear:sensor:<ID>`` como texto, prontos para o HSET.

    ``sensor_id`` fica de fora porque já faz parte da chave.
    """
    campos: dict[str, str] = {}
    for campo in CAMPOS_RESUMO:
        if campo == "sensor_id":
            continue
        valor = resumo[campo]
        campos[campo] = formatar_iso(valor) if isinstance(valor, datetime) else str(valor)
    return campos


# --- Console -----------------------------------------------------------------

InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]


def _ler_sensor(db: Database, input_fn: InputFn, output_fn: OutputFn) -> str:
    ids = [s["_id"] for s in db[SENSORES].find({}, {"_id": 1}).sort("_id", ASCENDING)]
    if ids:
        output_fn(f"Sensores cadastrados: {', '.join(ids)}")
    sensor_id = input_fn("Sensor: ").strip().upper()
    if not sensor_id:
        raise ValueError("Informe o identificador do sensor.")
    if sensor_id not in ids:
        raise ValueError(f"Sensor {sensor_id} não está cadastrado.")
    return sensor_id


def _limites_sensor(db: Database, sensor_id: str) -> tuple[datetime, datetime]:
    """Hora cheia da primeira medição e fim da hora da última."""
    colecao = db[MEDICOES]
    primeira = colecao.find_one({"sensor_id": sensor_id}, sort=[("timestamp", ASCENDING)])
    ultima = colecao.find_one({"sensor_id": sensor_id}, sort=[("timestamp", DESCENDING)])
    if primeira is None or ultima is None:
        raise ValueError(f"Sensor {sensor_id} não tem medições. Gere os dados na opção 1.")
    return janela_horaria(primeira["timestamp"])[0], janela_horaria(ultima["timestamp"])[1]


def _ler_intervalo(
    input_fn: InputFn,
    inicio_padrao: datetime,
    fim_padrao: datetime,
) -> tuple[datetime, datetime]:
    inicio_txt = input_fn(f"Início em UTC, inclusivo [{formatar(inicio_padrao)}]: ").strip()
    inicio = parse_data_utc(inicio_txt) if inicio_txt else inicio_padrao
    fim_txt = input_fn(f"Fim em UTC, exclusivo [{formatar(max(fim_padrao, inicio + UMA_HORA))}]: ").strip()
    fim = parse_data_utc(fim_txt) if fim_txt else max(fim_padrao, inicio + UMA_HORA)
    if fim <= inicio:
        raise ValueError("O fim do intervalo deve ser posterior ao início.")
    return inicio, fim


def _numero(valor: Any, largura: int, casas: int = 1) -> str:
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return f"{valor:>{largura}.{casas}f}"
    return f"{str(valor):>{largura}}"


def imprimir_medicoes(medicoes: list[dict[str, Any]], output_fn: OutputFn = print) -> None:
    output_fn(f"  {'timestamp (UTC)':<18}{'pm25':>8}{'pm10':>8}{'co2':>8}{'temp':>8}{'umid':>8}")
    invalidas = 0
    for m in medicoes:
        marca = " " if medicao_valida(m) else "*"
        invalidas += marca == "*"
        output_fn(
            f"{marca} {formatar(m['timestamp']):<18}"
            f"{_numero(m.get('pm25'), 8)}{_numero(m.get('pm10'), 8)}{_numero(m.get('co2'), 8, 0)}"
            f"{_numero(m.get('temperatura'), 8)}{_numero(m.get('umidade'), 8, 0)}"
        )
    output_fn(f"{len(medicoes)} medição(ões) encontrada(s).")
    if invalidas:
        output_fn(f"* {invalidas} medição(ões) inválida(s), ignorada(s) nos resumos.")


def imprimir_resumos(resumos: list[dict[str, Any]], output_fn: OutputFn = print) -> None:
    output_fn(
        f"{'sensor':<7}{'início (UTC)':<18}{'fim (UTC)':<18}{'qtde':>5}"
        f"{'pm25 méd':>10}{'pm25 máx':>10}{'pm10 méd':>10}{'pm10 máx':>10}"
        f"{'co2 méd':>9}{'temp méd':>10}{'umid méd':>10}"
    )
    for r in resumos:
        output_fn(
            f"{r['sensor_id']:<7}{formatar(r['inicio_periodo']):<18}{formatar(r['fim_periodo']):<18}"
            f"{r['qt_medicoes']:>5}"
            f"{_numero(r['pm25_media'], 10, 2)}{_numero(r['pm25_max'], 10)}"
            f"{_numero(r['pm10_media'], 10, 2)}{_numero(r['pm10_max'], 10)}"
            f"{_numero(r['co2_media'], 9, 1)}{_numero(r['temperatura_media'], 10, 2)}"
            f"{_numero(r['umidade_media'], 10, 1)}"
        )
    output_fn(f"{len(resumos)} resumo(s) horário(s).")


def consultar_console(
    *,
    db: Database | None = None,
    input_fn: InputFn = input,
    output_fn: OutputFn = print,
) -> list[dict[str, Any]]:
    """Opção 2: medições de um sensor num intervalo (padrão: a primeira hora)."""
    from app.db import get_database

    db = db if db is not None else get_database()
    sensor_id = _ler_sensor(db, input_fn, output_fn)
    primeira_hora, _ = _limites_sensor(db, sensor_id)
    inicio, fim = _ler_intervalo(input_fn, primeira_hora, primeira_hora + UMA_HORA)
    medicoes = consultar_medicoes(db, sensor_id, inicio, fim)
    output_fn(f"\nMedições de {sensor_id} de {formatar(inicio)} até {formatar(fim)} (exclusivo):")
    imprimir_medicoes(medicoes, output_fn)
    return medicoes


def resumo_sensor_console(
    *,
    db: Database | None = None,
    input_fn: InputFn = input,
    output_fn: OutputFn = print,
) -> list[dict[str, Any]]:
    """Opção 3: resumo horário de um sensor (padrão: todas as horas com dados)."""
    from app.db import get_database

    db = db if db is not None else get_database()
    sensor_id = _ler_sensor(db, input_fn, output_fn)
    inicio_padrao, fim_padrao = _limites_sensor(db, sensor_id)
    inicio, fim = _ler_intervalo(input_fn, inicio_padrao, fim_padrao)
    resumos = resumos_horarios(db, sensor_id, inicio, fim)
    output_fn(f"\nResumo horário de {sensor_id} de {formatar(inicio)} até {formatar(fim)} (exclusivo):")
    imprimir_resumos(resumos, output_fn)
    return resumos
