"""Simulador de medições dos sensores de qualidade do ar.

Gera séries de 24 horas (por padrão) a cada cinco minutos para cada sensor
do catálogo, com ciclo diário e ruído suave, e grava em ``Medicoes``.

Uso fora do menu::

    python -m app.simulador --horas 24 --inicio 2026-10-20T00:00:00Z --seed 42
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math
import random
from typing import Any

from pymongo import InsertOne
from pymongo.database import Database

from app.db import MEDICOES
from app.sensores import CATALOGO, cadastrar_sensores, preparar_colecoes

INICIO_PADRAO = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
HORAS_PADRAO = 24
PASSO_MINUTOS_PADRAO = 5
SEED_PADRAO = 42
FUSO_LOCAL_HORAS = -3  # Brasília; usado só para posicionar os picos do dia.

# Perfil climático e de poluição por cidade (valores ilustrativos).
#   pm25: nível base em µg/m³; co2: acréscimo sobre 420 ppm;
#   temp: média diária em °C e amplitude; umid: umidade média em %.
PERFIS_CIDADE: dict[str, dict[str, float]] = {
    "Chapecó": {"pm25": 14.0, "co2": 40.0, "temp": 21.0, "amp": 6.0, "umid": 70.0},
    "Florianópolis": {"pm25": 10.0, "co2": 30.0, "temp": 23.0, "amp": 4.0, "umid": 78.0},
    "Curitiba": {"pm25": 18.0, "co2": 60.0, "temp": 17.0, "amp": 6.0, "umid": 75.0},
}
PERFIL_GENERICO = {"pm25": 15.0, "co2": 45.0, "temp": 22.0, "amp": 5.0, "umid": 70.0}

# Fator de poluição por tipo de local, deduzido da descrição do sensor.
FATORES_LOCAL: tuple[tuple[str, float], ...] = (
    ("industrial", 1.4),
    ("centro", 1.0),
    ("norte", 0.85),
    ("orla", 0.7),
)

LIMITES = {
    "pm25": (2.0, 150.0),
    "pm10": (4.0, 300.0),
    "co2": (380.0, 1500.0),
    "temperatura": (-5.0, 42.0),
    "umidade": (20.0, 99.0),
}


@dataclass
class ResumoSensor:
    sensor_id: str
    quantidade: int
    primeiro: datetime
    ultimo: datetime


@dataclass
class ResultadoGeracao:
    sensores_cadastrados: int
    inicio: datetime
    fim: datetime
    removidas: int
    inseridas: int
    por_sensor: list[ResumoSensor] = field(default_factory=list)


def _fator_local(descricao: str) -> float:
    texto = descricao.lower()
    for palavra, fator in FATORES_LOCAL:
        if palavra in texto:
            return fator
    return 1.0


def _trafego(hora_local: float) -> float:
    """Dois picos de trânsito, por volta das 8h e das 19h, entre 0 e 1."""
    manha = math.exp(-((hora_local - 8.0) / 1.5) ** 2)
    noite = math.exp(-((hora_local - 19.0) / 1.5) ** 2)
    return min(1.0, manha + noite)


def _noite(hora_local: float) -> float:
    """Curva suave com máximo às 2h e mínimo às 14h, entre 0 e 1."""
    return 0.5 * (1.0 + math.cos((hora_local - 2.0) / 24.0 * 2.0 * math.pi))


def _ciclo_termico(hora_local: float) -> float:
    """Entre -1 e 1, com máximo às 15h e mínimo às 3h."""
    return math.cos((hora_local - 15.0) / 24.0 * 2.0 * math.pi)


def _limitar(nome: str, valor: float) -> float:
    minimo, maximo = LIMITES[nome]
    return max(minimo, min(maximo, valor))


class _RuidoSuave:
    """Ruído autocorrelacionado (AR(1)) para que leituras vizinhas se pareçam."""

    def __init__(self, rng: random.Random, sigma: float, memoria: float = 0.7) -> None:
        self._rng = rng
        self._sigma = sigma
        self._memoria = memoria
        self._valor = 0.0

    def proximo(self) -> float:
        self._valor = self._memoria * self._valor + self._rng.gauss(0.0, self._sigma)
        return self._valor


def _validar_parametros(inicio: datetime, horas: int, passo_minutos: int) -> None:
    if inicio.tzinfo is None or inicio.utcoffset() != timedelta(0):
        raise ValueError("inicio deve ser um datetime em UTC (tzinfo=timezone.utc).")
    if horas <= 0:
        raise ValueError("horas deve ser maior que zero.")
    if passo_minutos <= 0 or 60 % passo_minutos != 0:
        raise ValueError("passo_minutos deve ser um divisor de 60.")
    if inicio.second or inicio.microsecond or inicio.minute % passo_minutos:
        raise ValueError("inicio deve estar alinhado ao passo (minutos múltiplos do passo).")


def gerar_medicoes_sensor(
    sensor: dict[str, Any],
    inicio: datetime = INICIO_PADRAO,
    horas: int = HORAS_PADRAO,
    passo_minutos: int = PASSO_MINUTOS_PADRAO,
    seed: int = SEED_PADRAO,
) -> list[dict[str, Any]]:
    """Gera a série de um sensor. Determinística para o mesmo ``seed``."""
    _validar_parametros(inicio, horas, passo_minutos)
    rng = random.Random(f"{seed}:{sensor['_id']}")
    cidade = sensor["localizacao"]["cidade"]
    perfil = PERFIS_CIDADE.get(cidade, PERFIL_GENERICO)
    fator = _fator_local(sensor.get("descricao", ""))
    razao_pm10 = rng.uniform(1.6, 2.2)

    ruido_pm = _RuidoSuave(rng, sigma=perfil["pm25"] * 0.12)
    ruido_co2 = _RuidoSuave(rng, sigma=12.0)
    ruido_temp = _RuidoSuave(rng, sigma=0.4)
    ruido_umid = _RuidoSuave(rng, sigma=1.5)

    passo = timedelta(minutes=passo_minutos)
    total = horas * 60 // passo_minutos
    docs: list[dict[str, Any]] = []
    for i in range(total):
        ts = inicio + i * passo
        hora_local = ((ts.hour + ts.minute / 60.0) + FUSO_LOCAL_HORAS) % 24.0

        pm25 = perfil["pm25"] * fator * (1.0 + 0.6 * _trafego(hora_local)) + ruido_pm.proximo()
        pm25 = _limitar("pm25", pm25)
        pm10 = _limitar("pm10", pm25 * razao_pm10 + rng.gauss(0.0, 1.0))
        pm10 = max(pm10, pm25 * 1.05)

        co2 = (
            420.0
            + perfil["co2"] * fator
            + 110.0 * _trafego(hora_local)
            + 50.0 * _noite(hora_local)
            + ruido_co2.proximo()
        )
        co2 = _limitar("co2", co2)

        temperatura = perfil["temp"] + perfil["amp"] * _ciclo_termico(hora_local) + ruido_temp.proximo()
        temperatura = _limitar("temperatura", temperatura)

        umidade = perfil["umid"] - 2.5 * (temperatura - perfil["temp"]) + ruido_umid.proximo()
        umidade = _limitar("umidade", umidade)

        docs.append(
            {
                "sensor_id": sensor["_id"],
                "timestamp": ts,
                "pm25": round(pm25, 1),
                "pm10": round(pm10, 1),
                "co2": int(round(co2)),
                "temperatura": round(temperatura, 1),
                "umidade": int(round(umidade)),
            }
        )
    return docs


def gerar_medicoes(
    sensores: Iterable[dict[str, Any]] | None = None,
    inicio: datetime = INICIO_PADRAO,
    horas: int = HORAS_PADRAO,
    passo_minutos: int = PASSO_MINUTOS_PADRAO,
    seed: int = SEED_PADRAO,
) -> list[dict[str, Any]]:
    """Gera as séries de todos os sensores informados (catálogo por padrão)."""
    sensores = list(sensores) if sensores is not None else CATALOGO
    docs: list[dict[str, Any]] = []
    for sensor in sensores:
        docs.extend(gerar_medicoes_sensor(sensor, inicio, horas, passo_minutos, seed))
    return docs


def persistir_medicoes(
    db: Database,
    docs: list[dict[str, Any]],
    lote: int = 500,
) -> tuple[int, int]:
    """Substitui as medições dos mesmos sensores no intervalo coberto por ``docs``.

    Retorna (removidas, inseridas). Apagar antes de inserir torna a geração
    idempotente mesmo com o índice único em (sensor_id, timestamp).
    """
    if not docs:
        return 0, 0
    colecao = db[MEDICOES]
    sensor_ids = sorted({d["sensor_id"] for d in docs})
    inicio = min(d["timestamp"] for d in docs)
    fim = max(d["timestamp"] for d in docs)
    removidas = colecao.delete_many(
        {"sensor_id": {"$in": sensor_ids}, "timestamp": {"$gte": inicio, "$lte": fim}}
    ).deleted_count

    inseridas = 0
    for i in range(0, len(docs), lote):
        resultado = colecao.bulk_write([InsertOne(d) for d in docs[i : i + lote]], ordered=False)
        inseridas += resultado.inserted_count
    return removidas, inseridas


def executar(
    db: Database,
    inicio: datetime = INICIO_PADRAO,
    horas: int = HORAS_PADRAO,
    passo_minutos: int = PASSO_MINUTOS_PADRAO,
    seed: int = SEED_PADRAO,
) -> ResultadoGeracao:
    """Fluxo completo: prepara coleções, cadastra sensores, gera e grava."""
    preparar_colecoes(db)
    cadastrados = cadastrar_sensores(db)
    docs = gerar_medicoes(CATALOGO, inicio, horas, passo_minutos, seed)
    removidas, inseridas = persistir_medicoes(db, docs)

    por_sensor: list[ResumoSensor] = []
    for sensor in CATALOGO:
        serie = [d for d in docs if d["sensor_id"] == sensor["_id"]]
        por_sensor.append(
            ResumoSensor(sensor["_id"], len(serie), serie[0]["timestamp"], serie[-1]["timestamp"])
        )
    return ResultadoGeracao(
        sensores_cadastrados=cadastrados,
        inicio=inicio,
        fim=inicio + timedelta(hours=horas),
        removidas=removidas,
        inseridas=inseridas,
        por_sensor=por_sensor,
    )


def _fmt(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%MZ")


def imprimir_resultado(resultado: ResultadoGeracao, output_fn: Callable[[str], None] = print) -> None:
    output_fn(f"Sensores cadastrados/atualizados: {resultado.sensores_cadastrados}")
    output_fn(f"Janela gerada (UTC): {_fmt(resultado.inicio)} até {_fmt(resultado.fim)} (exclusivo)")
    output_fn(f"Medições removidas do intervalo: {resultado.removidas}")
    output_fn(f"Medições inseridas: {resultado.inseridas}")
    output_fn("")
    output_fn(f"{'sensor':<8}{'qtde':>6}  {'primeira':<18}{'última':<18}")
    for r in resultado.por_sensor:
        output_fn(f"{r.sensor_id:<8}{r.quantidade:>6}  {_fmt(r.primeiro):<18}{_fmt(r.ultimo):<18}")


def parse_inicio(texto: str) -> datetime:
    """Aceita ISO 8601; sem fuso é interpretado como UTC."""
    valor = datetime.fromisoformat(texto.strip().replace("Z", "+00:00"))
    if valor.tzinfo is None:
        return valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc)


def executar_console(
    *,
    db: Database | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> ResultadoGeracao | None:
    """Versão interativa usada pela opção 1 do menu."""
    from app.db import get_database

    horas_txt = input_fn(f"Horas de dados por sensor [{HORAS_PADRAO}]: ").strip()
    inicio_txt = input_fn(f"Início da série em UTC [{_fmt(INICIO_PADRAO)}]: ").strip()
    horas = int(horas_txt) if horas_txt else HORAS_PADRAO
    inicio = parse_inicio(inicio_txt) if inicio_txt else INICIO_PADRAO

    total = len(CATALOGO) * horas * 60 // PASSO_MINUTOS_PADRAO
    output_fn(
        f"Serão geradas {total} medições para {len(CATALOGO)} sensores, "
        f"substituindo as existentes nesse intervalo."
    )
    if input_fn("Confirmar? [s/N]: ").strip().lower() not in {"s", "sim"}:
        output_fn("Geração cancelada.")
        return None

    resultado = executar(db if db is not None else get_database(), inicio=inicio, horas=horas)
    imprimir_resultado(resultado, output_fn)
    return resultado


def main(argv: list[str] | None = None) -> None:
    from app.db import get_database

    parser = argparse.ArgumentParser(description="Gera medições simuladas no MongoDB.")
    parser.add_argument("--horas", type=int, default=HORAS_PADRAO)
    parser.add_argument("--inicio", type=parse_inicio, default=INICIO_PADRAO, help="ISO 8601 em UTC")
    parser.add_argument("--passo", type=int, default=PASSO_MINUTOS_PADRAO, help="minutos entre medições")
    parser.add_argument("--seed", type=int, default=SEED_PADRAO)
    args = parser.parse_args(argv)
    resultado = executar(get_database(), args.inicio, args.horas, args.passo, args.seed)
    imprimir_resultado(resultado)


if __name__ == "__main__":
    main()
