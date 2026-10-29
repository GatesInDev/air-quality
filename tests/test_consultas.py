"""Testes das consultas por sensor, sem acesso ao banco."""

from datetime import datetime, timezone

import pytest

from app.consultas import (
    CAMPOS_RESUMO,
    FILTRO_MEDICAO_VALIDA,
    imprimir_medicoes,
    medicao_valida,
    para_redis,
    pipeline_resumo_horario,
)

UTC = timezone.utc
VALIDA = {
    "sensor_id": "S001",
    "timestamp": datetime(2026, 10, 20, 10, 0, tzinfo=UTC),
    "pm25": 12.5,
    "pm10": 25.0,
    "co2": 450,
    "temperatura": 21.3,
    "umidade": 70,
}


def test_medicao_do_enunciado_e_valida() -> None:
    assert medicao_valida(VALIDA)
    assert medicao_valida({**VALIDA, "temperatura": -3.0, "pm25": 0})


@pytest.mark.parametrize(
    "alteracao",
    [
        {"pm25": None},
        {"pm10": -1.0},
        {"co2": "450"},
        {"temperatura": float("nan")},
        {"temperatura": 80.0},
        {"umidade": 101},
        {"umidade": True},
    ],
)
def test_medicao_invalida(alteracao: dict) -> None:
    assert not medicao_valida({**VALIDA, **alteracao})


def test_medicao_sem_campo_e_invalida() -> None:
    assert not medicao_valida({k: v for k, v in VALIDA.items() if k != "co2"})


def test_filtro_cobre_todas_as_grandezas() -> None:
    assert set(FILTRO_MEDICAO_VALIDA) == {"pm25", "pm10", "co2", "temperatura", "umidade"}
    assert all(c["$type"] == "number" for c in FILTRO_MEDICAO_VALIDA.values())
    assert FILTRO_MEDICAO_VALIDA["umidade"]["$lte"] == 100


def test_pipeline_tem_estagios_e_campos_do_enunciado() -> None:
    inicio = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    fim = datetime(2026, 10, 20, 11, 0, tzinfo=UTC)

    pipeline = pipeline_resumo_horario("S001", inicio, fim)

    assert [next(iter(e)) for e in pipeline] == ["$match", "$group", "$sort", "$project"]
    match = pipeline[0]["$match"]
    assert match["sensor_id"] == "S001"
    assert match["timestamp"] == {"$gte": inicio, "$lt": fim}
    assert set(FILTRO_MEDICAO_VALIDA) <= set(match)
    grupo = pipeline[1]["$group"]
    assert grupo["pm25_media"] == {"$avg": "$pm25"}
    assert grupo["pm25_max"] == {"$max": "$pm25"}
    assert grupo["pm10_max"] == {"$max": "$pm10"}
    projecao = pipeline[3]["$project"]
    assert set(CAMPOS_RESUMO) <= set(projecao)


def test_pipeline_sem_sensor_resume_todos() -> None:
    inicio = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    pipeline = pipeline_resumo_horario(None, inicio, inicio)
    assert "sensor_id" not in pipeline[0]["$match"]


def test_impressao_marca_medicoes_invalidas() -> None:
    saida: list[str] = []
    imprimir_medicoes([VALIDA, {**VALIDA, "pm25": -4.0}], saida.append)

    assert saida[1].startswith("  ")
    assert saida[2].startswith("* ")
    assert "2 medição(ões) encontrada(s)." in saida
    assert "* 1 medição(ões) inválida(s), ignorada(s) nos resumos." in saida


def test_para_redis_usa_formato_do_enunciado() -> None:
    resumo = {
        "sensor_id": "S001",
        "inicio_periodo": datetime(2026, 10, 20, 10, 0, tzinfo=UTC),
        "fim_periodo": datetime(2026, 10, 20, 11, 0, tzinfo=UTC),
        "qt_medicoes": 2,
        "pm25_media": 19.9,
        "pm10_media": 39.5,
        "co2_media": 495.0,
        "temperatura_media": 24.55,
        "umidade_media": 66.5,
        "pm25_max": 21.3,
        "pm10_max": 42.8,
    }

    campos = para_redis(resumo)

    assert "sensor_id" not in campos
    assert campos["inicio_periodo"] == "2026-10-20T10:00:00Z"
    assert campos["fim_periodo"] == "2026-10-20T11:00:00Z"
    assert campos["qt_medicoes"] == "2"
    assert campos["pm25_media"] == "19.9"
    assert all(isinstance(v, str) for v in campos.values())
