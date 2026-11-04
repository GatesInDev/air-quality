"""Resumo horário por cidade (opção 4 do menu).

A diferença para ``app/consultas.py`` é a origem da cidade: ela não está em
``Medicoes``, e sim em ``Sensores.localizacao``. O pipeline parte de ``Medicoes``
e usa ``$lookup`` por ``sensor_id`` para trazer o sensor junto de cada medição.

Média global, não média das médias
----------------------------------
O ``$group`` agrupa as **medições** da cidade, não os resumos dos sensores. Com
isso ``pm25_media`` é a média de todas as medições válidas da cidade no período.

Isso importa porque sensores podem ter quantidades diferentes de registros: um
sensor com 12 medições e outro com 3 não pesam igual na média da cidade. Fazer a
média simples das médias dos sensores daria outro número — ``comparar_medias``
calcula os dois para a seção de análise do relatório.

Medições cujo ``sensor_id`` não existe em ``Sensores`` ficam de fora: sem sensor
cadastrado não há cidade a que atribuí-las. É o efeito do ``$unwind`` sem
``preserveNullAndEmptyArrays``.

Os resumos são dicionários Python com os campos de ``CAMPOS_RESUMO_CIDADE``;
``para_redis`` os converte nos campos de texto do Hash (seção 6.1 do enunciado).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
import unicodedata

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database

from app.consultas import (
    CASAS_DECIMAIS,
    FILTRO_MEDICAO_VALIDA,
    GRANDEZAS,
    _numero,
    resumos_horarios,
)
from app.db import MEDICOES, SENSORES
from app.periodo import UMA_HORA, formatar, formatar_iso, janela_horaria, parse_data_utc, para_utc

CAMPOS_RESUMO_CIDADE = (
    "cidade",
    "uf",
    "inicio_periodo",
    "fim_periodo",
    "qt_medicoes",
    "qt_sensores",
    "pm25_media",
    "pm10_media",
    "co2_media",
    "temperatura_media",
    "umidade_media",
    "pm25_max",
    "pm10_max",
)


def normalizar_cidade(nome: str) -> str:
    """``"Chapecó"`` -> ``"chapeco"``: sem acento, em minúsculas, sem espaços.

    Compõe a chave do Hash no Redis, que precisa ser estável e ASCII. Espaços
    viram ``_`` para manter a chave legível (``"São Paulo"`` -> ``"sao_paulo"``).
    """
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    return "_".join(sem_acento.lower().split())


def cidades_cadastradas(db: Database) -> list[dict[str, str]]:
    """``[{"cidade": ..., "uf": ...}]`` distintos dos sensores, em ordem alfabética."""
    pipeline = [
        {
            "$group": {
                "_id": {"cidade": "$localizacao.cidade", "uf": "$localizacao.uf"},
                "qt_sensores": {"$sum": 1},
            }
        },
        {"$sort": {"_id.uf": ASCENDING, "_id.cidade": ASCENDING}},
        {"$project": {"_id": 0, "cidade": "$_id.cidade", "uf": "$_id.uf", "qt_sensores": 1}},
    ]
    return list(db[SENSORES].aggregate(pipeline))


def pipeline_resumo_horario_cidade(
    uf: str | None,
    cidade: str | None,
    inicio: datetime,
    fim: datetime,
) -> list[dict[str, Any]]:
    """Pipeline com um resumo por cidade e hora cheia dentro de ``[inicio, fim)``.

    ``uf=None`` e ``cidade=None`` resumem todas as cidades.
    """
    match: dict[str, Any] = {"timestamp": {"$gte": para_utc(inicio), "$lt": para_utc(fim)}}
    match.update(FILTRO_MEDICAO_VALIDA)

    etapas: list[dict[str, Any]] = [
        # Filtra as medições antes do $lookup: menos documentos para juntar.
        {"$match": match},
        {
            "$lookup": {
                "from": SENSORES,
                "localField": "sensor_id",
                "foreignField": "_id",
                "as": "sensor",
            }
        },
        # _id de Sensores é único, então o array tem 0 ou 1 item.
        {"$unwind": "$sensor"},
    ]

    local: dict[str, Any] = {}
    if uf is not None:
        local["sensor.localizacao.uf"] = uf
    if cidade is not None:
        local["sensor.localizacao.cidade"] = cidade
    if local:
        etapas.append({"$match": local})

    hora = {"$dateTrunc": {"date": "$timestamp", "unit": "hour", "timezone": "UTC"}}
    grupo: dict[str, Any] = {
        "_id": {
            "uf": "$sensor.localizacao.uf",
            "cidade": "$sensor.localizacao.cidade",
            "inicio_periodo": hora,
        },
        "qt_medicoes": {"$sum": 1},
        "sensores": {"$addToSet": "$sensor_id"},
    }
    for campo in GRANDEZAS:
        grupo[f"{campo}_media"] = {"$avg": f"${campo}"}
    grupo["pm25_max"] = {"$max": "$pm25"}
    grupo["pm10_max"] = {"$max": "$pm10"}

    projecao: dict[str, Any] = {
        "_id": 0,
        "cidade": "$_id.cidade",
        "uf": "$_id.uf",
        "inicio_periodo": "$_id.inicio_periodo",
        "fim_periodo": {
            "$dateAdd": {"startDate": "$_id.inicio_periodo", "unit": "hour", "amount": 1}
        },
        "qt_medicoes": 1,
        "qt_sensores": {"$size": "$sensores"},
    }
    for campo in GRANDEZAS:
        projecao[f"{campo}_media"] = {"$round": [f"${campo}_media", CASAS_DECIMAIS]}
    projecao["pm25_max"] = 1
    projecao["pm10_max"] = 1

    etapas += [
        {"$group": grupo},
        {"$sort": {"_id.uf": ASCENDING, "_id.cidade": ASCENDING, "_id.inicio_periodo": ASCENDING}},
        {"$project": projecao},
    ]
    return etapas


def _ordenar(resumo: dict[str, Any]) -> dict[str, Any]:
    return {campo: resumo[campo] for campo in CAMPOS_RESUMO_CIDADE}


def resumos_horarios_cidade(
    db: Database,
    uf: str | None,
    cidade: str | None,
    inicio: datetime,
    fim: datetime,
) -> list[dict[str, Any]]:
    """Resumos de cada cidade e hora com medições válidas, como dicionários."""
    resultado = db[MEDICOES].aggregate(pipeline_resumo_horario_cidade(uf, cidade, inicio, fim))
    return [_ordenar(r) for r in resultado]


def resumo_horario_cidade(
    db: Database,
    uf: str,
    cidade: str,
    hora: datetime,
) -> dict[str, Any] | None:
    """Resumo da hora que contém ``hora`` (ex.: 10:37 -> 10:00 a 11:00), ou None."""
    inicio, fim = janela_horaria(hora)
    resumos = resumos_horarios_cidade(db, uf, cidade, inicio, fim)
    return resumos[0] if resumos else None


def _ultima_medicao_cidade(db: Database, uf: str, cidade: str) -> datetime | None:
    """Timestamp da medição válida mais recente entre os sensores da cidade."""
    ids = [
        s["_id"]
        for s in db[SENSORES].find(
            {"localizacao.uf": uf, "localizacao.cidade": cidade}, {"_id": 1}
        )
    ]
    if not ids:
        return None
    doc = db[MEDICOES].find_one(
        {"sensor_id": {"$in": ids}, **FILTRO_MEDICAO_VALIDA},
        {"timestamp": 1},
        sort=[("timestamp", DESCENDING)],
    )
    return doc["timestamp"] if doc else None


def ultimo_resumo_cidade(db: Database, uf: str, cidade: str) -> dict[str, Any] | None:
    """Resumo da hora da medição válida mais recente da cidade, ou None.

    Com o simulador em modo contínuo essa hora pode estar incompleta.
    """
    mais_recente = _ultima_medicao_cidade(db, uf, cidade)
    if mais_recente is None:
        return None
    return resumo_horario_cidade(db, uf, cidade, mais_recente)


def ultimos_resumos_por_cidade(db: Database) -> dict[str, dict[str, Any]]:
    """Último resumo de cada cidade cadastrada, indexado por ``"<UF>:<cidade>"``.

    A chave usa a cidade normalizada, igual ao sufixo do Hash no Redis.
    Cidades sem medição válida ficam de fora.
    """
    resumos: dict[str, dict[str, Any]] = {}
    for local in cidades_cadastradas(db):
        resumo = ultimo_resumo_cidade(db, local["uf"], local["cidade"])
        if resumo is not None:
            resumos[f"{local['uf'].upper()}:{normalizar_cidade(local['cidade'])}"] = resumo
    return resumos


def para_redis(resumo: dict[str, Any]) -> dict[str, str]:
    """Campos do Hash ``qualidadear:cidade:<UF>:<cidade>`` como texto, para o HSET.

    ``cidade`` e ``uf`` ficam de fora porque já fazem parte da chave.
    """
    campos: dict[str, str] = {}
    for campo in CAMPOS_RESUMO_CIDADE:
        if campo in {"cidade", "uf"}:
            continue
        valor = resumo[campo]
        campos[campo] = formatar_iso(valor) if isinstance(valor, datetime) else str(valor)
    return campos


def comparar_medias(
    db: Database,
    uf: str,
    cidade: str,
    hora: datetime,
    campo: str = "pm25_media",
) -> dict[str, Any] | None:
    """Média global da cidade contra a média simples das médias dos sensores.

    O enunciado destaca que as duas não coincidem quando os sensores têm
    quantidades diferentes de medições. Serve à análise do relatório.
    """
    resumo = resumo_horario_cidade(db, uf, cidade, hora)
    if resumo is None:
        return None

    inicio, fim = janela_horaria(hora)
    ids = [
        s["_id"]
        for s in db[SENSORES].find(
            {"localizacao.uf": uf, "localizacao.cidade": cidade}, {"_id": 1}
        )
    ]
    por_sensor = [r for r in resumos_horarios(db, None, inicio, fim) if r["sensor_id"] in ids]
    if not por_sensor:
        return None

    global_ = resumo[campo]
    das_medias = sum(r[campo] for r in por_sensor) / len(por_sensor)
    return {
        "cidade": cidade,
        "uf": uf,
        "inicio_periodo": resumo["inicio_periodo"],
        "campo": campo,
        "media_global": global_,
        "media_das_medias": round(das_medias, CASAS_DECIMAIS),
        "diferenca": round(global_ - das_medias, CASAS_DECIMAIS),
        "qt_medicoes_por_sensor": {r["sensor_id"]: r["qt_medicoes"] for r in por_sensor},
    }


# --- Console -----------------------------------------------------------------

InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]


def _ler_cidade(db: Database, input_fn: InputFn, output_fn: OutputFn) -> tuple[str, str]:
    """Lê a cidade no console aceitando o nome com ou sem acento, em qualquer caixa."""
    locais = cidades_cadastradas(db)
    if not locais:
        raise ValueError("Nenhum sensor cadastrado. Gere os dados na opção 1.")
    output_fn("Cidades cadastradas: " + ", ".join(f"{l['cidade']}/{l['uf']}" for l in locais))

    digitado = input_fn("Cidade: ").strip()
    if not digitado:
        raise ValueError("Informe a cidade.")
    alvo = normalizar_cidade(digitado)
    for local in locais:
        if normalizar_cidade(local["cidade"]) == alvo:
            return local["uf"], local["cidade"]
    raise ValueError(f"Cidade {digitado} não tem sensores cadastrados.")


def _limites_cidade(db: Database, uf: str, cidade: str) -> tuple[datetime, datetime]:
    """Hora cheia da primeira medição da cidade e fim da hora da última."""
    ids = [
        s["_id"]
        for s in db[SENSORES].find(
            {"localizacao.uf": uf, "localizacao.cidade": cidade}, {"_id": 1}
        )
    ]
    colecao = db[MEDICOES]
    primeira = colecao.find_one({"sensor_id": {"$in": ids}}, sort=[("timestamp", ASCENDING)])
    ultima = colecao.find_one({"sensor_id": {"$in": ids}}, sort=[("timestamp", DESCENDING)])
    if primeira is None or ultima is None:
        raise ValueError(f"{cidade} não tem medições. Gere os dados na opção 1.")
    return janela_horaria(primeira["timestamp"])[0], janela_horaria(ultima["timestamp"])[1]


def imprimir_resumos_cidade(resumos: list[dict[str, Any]], output_fn: OutputFn = print) -> None:
    output_fn(
        f"{'cidade':<16}{'uf':<4}{'início (UTC)':<18}{'fim (UTC)':<18}"
        f"{'qtde':>5}{'sens':>5}"
        f"{'pm25 méd':>10}{'pm25 máx':>10}{'pm10 méd':>10}{'pm10 máx':>10}"
        f"{'co2 méd':>9}{'temp méd':>10}{'umid méd':>10}"
    )
    for r in resumos:
        output_fn(
            f"{r['cidade']:<16}{r['uf']:<4}"
            f"{formatar(r['inicio_periodo']):<18}{formatar(r['fim_periodo']):<18}"
            f"{r['qt_medicoes']:>5}{r['qt_sensores']:>5}"
            f"{_numero(r['pm25_media'], 10, 2)}{_numero(r['pm25_max'], 10)}"
            f"{_numero(r['pm10_media'], 10, 2)}{_numero(r['pm10_max'], 10)}"
            f"{_numero(r['co2_media'], 9, 1)}{_numero(r['temperatura_media'], 10, 2)}"
            f"{_numero(r['umidade_media'], 10, 1)}"
        )
    output_fn(f"{len(resumos)} resumo(s) horário(s) por cidade.")


def resumo_cidade_console(
    *,
    db: Database | None = None,
    input_fn: InputFn = input,
    output_fn: OutputFn = print,
) -> list[dict[str, Any]]:
    """Opção 4: resumo horário de uma cidade (padrão: todas as horas com dados)."""
    from app.db import get_database

    db = db if db is not None else get_database()
    uf, cidade = _ler_cidade(db, input_fn, output_fn)
    inicio_padrao, fim_padrao = _limites_cidade(db, uf, cidade)

    inicio_txt = input_fn(f"Início em UTC, inclusivo [{formatar(inicio_padrao)}]: ").strip()
    inicio = parse_data_utc(inicio_txt) if inicio_txt else inicio_padrao
    padrao_fim = max(fim_padrao, inicio + UMA_HORA)
    fim_txt = input_fn(f"Fim em UTC, exclusivo [{formatar(padrao_fim)}]: ").strip()
    fim = parse_data_utc(fim_txt) if fim_txt else padrao_fim
    if fim <= inicio:
        raise ValueError("O fim do intervalo deve ser posterior ao início.")

    resumos = resumos_horarios_cidade(db, uf, cidade, inicio, fim)
    output_fn(
        f"\nResumo horário de {cidade}/{uf} de {formatar(inicio)} "
        f"até {formatar(fim)} (exclusivo):"
    )
    imprimir_resumos_cidade(resumos, output_fn)

    comparacao = comparar_medias(db, uf, cidade, inicio)
    if comparacao is not None:
        output_fn(
            f"\nPrimeira hora, PM2.5: média global {comparacao['media_global']:.2f} µg/m³ "
            f"contra média das médias dos sensores {comparacao['media_das_medias']:.2f} µg/m³ "
            f"(diferença {comparacao['diferenca']:+.2f})."
        )
        output_fn(f"Medições por sensor: {comparacao['qt_medicoes_por_sensor']}")
    return resumos
