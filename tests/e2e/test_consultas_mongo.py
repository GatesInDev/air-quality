"""Consultas e resumo horário por sensor contra o MongoDB do Docker Compose."""

from datetime import timedelta
import os
from statistics import mean
from uuid import uuid4

import pytest
from pymongo import MongoClient

from app.consultas import (
    CAMPOS_RESUMO,
    consultar_console,
    consultar_medicoes,
    resumo_horario_sensor,
    resumo_sensor_console,
    resumos_horarios,
    ultimo_resumo_sensor,
    ultimos_resumos_por_sensor,
)
from app.db import MEDICOES
from app.sensores import CATALOGO
from app.simulador import INICIO_PADRAO, executar

pytestmark = pytest.mark.e2e

HORA_10 = INICIO_PADRAO + timedelta(hours=10)


@pytest.fixture
def db():
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
    client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    client.admin.command("ping")
    nome = f"QualidadeAr_e2e_{uuid4().hex[:8]}"
    try:
        banco = client[nome]
        executar(banco)
        yield banco
    finally:
        client.drop_database(nome)
        client.close()


def test_consulta_respeita_intervalo_fechado_aberto(db) -> None:
    docs = consultar_medicoes(db, "S001", HORA_10, HORA_10 + timedelta(hours=1))

    assert len(docs) == 12
    assert docs[0]["timestamp"] == HORA_10
    assert docs[-1]["timestamp"] == HORA_10 + timedelta(minutes=55)
    assert all(d["sensor_id"] == "S001" and "_id" not in d for d in docs)


def test_resumo_bate_com_calculo_em_python(db) -> None:
    docs = consultar_medicoes(db, "S003", HORA_10, HORA_10 + timedelta(hours=1))

    resumo = resumo_horario_sensor(db, "S003", HORA_10 + timedelta(minutes=37))

    assert resumo is not None
    assert tuple(resumo) == CAMPOS_RESUMO
    assert resumo["sensor_id"] == "S003"
    assert resumo["inicio_periodo"] == HORA_10
    assert resumo["fim_periodo"] == HORA_10 + timedelta(hours=1)
    assert resumo["qt_medicoes"] == 12
    for campo in ("pm25", "pm10", "co2", "temperatura", "umidade"):
        assert resumo[f"{campo}_media"] == pytest.approx(mean(d[campo] for d in docs), abs=0.005)
    assert resumo["pm25_max"] == max(d["pm25"] for d in docs)
    assert resumo["pm10_max"] == max(d["pm10"] for d in docs)


def test_resumos_do_dia_inteiro(db) -> None:
    resumos = resumos_horarios(db, "S001", INICIO_PADRAO, INICIO_PADRAO + timedelta(hours=24))

    assert len(resumos) == 24
    assert all(r["qt_medicoes"] == 12 for r in resumos)
    assert [r["inicio_periodo"] for r in resumos] == [
        INICIO_PADRAO + timedelta(hours=h) for h in range(24)
    ]


def test_medicoes_invalidas_ficam_fora_do_resumo(db) -> None:
    base = consultar_medicoes(db, "S001", HORA_10, HORA_10 + timedelta(minutes=5))[0]
    invalidas = [
        {**base, "timestamp": HORA_10 + timedelta(minutes=1), "pm25": None},
        {**base, "timestamp": HORA_10 + timedelta(minutes=2), "pm10": -10.0},
        {**base, "timestamp": HORA_10 + timedelta(minutes=3), "umidade": 140},
        {**base, "timestamp": HORA_10 + timedelta(minutes=4), "co2": float("nan")},
        {k: v for k, v in {**base, "timestamp": HORA_10 + timedelta(minutes=6)}.items() if k != "temperatura"},
    ]
    # Simula dados gravados sem passar pelo validador da coleção.
    db[MEDICOES].insert_many(invalidas, bypass_document_validation=True)

    assert len(consultar_medicoes(db, "S001", HORA_10, HORA_10 + timedelta(hours=1))) == 17
    assert len(consultar_medicoes(db, "S001", HORA_10, HORA_10 + timedelta(hours=1), apenas_validas=True)) == 12
    resumo = resumo_horario_sensor(db, "S001", HORA_10)
    assert resumo is not None
    assert resumo["qt_medicoes"] == 12
    assert resumo["pm10_max"] > 0


def test_hora_sem_medicoes_retorna_none(db) -> None:
    assert resumo_horario_sensor(db, "S001", INICIO_PADRAO + timedelta(days=3)) is None
    assert resumo_horario_sensor(db, "S999", HORA_10) is None


def test_ultimo_resumo_e_a_ultima_hora_gerada(db) -> None:
    ultima_hora = INICIO_PADRAO + timedelta(hours=23)

    resumo = ultimo_resumo_sensor(db, "S002")
    todos = ultimos_resumos_por_sensor(db)

    assert resumo is not None and resumo["inicio_periodo"] == ultima_hora
    assert list(todos) == [s["_id"] for s in CATALOGO]
    assert all(r["inicio_periodo"] == ultima_hora for r in todos.values())


def test_console_opcoes_2_e_3_com_padroes(db) -> None:
    saida: list[str] = []

    respostas = iter(["s001", "", ""])
    medicoes = consultar_console(db=db, input_fn=lambda _p: next(respostas), output_fn=saida.append)
    assert len(medicoes) == 12
    assert "12 medição(ões) encontrada(s)." in saida

    respostas = iter(["S001", "2026-10-20T10:00Z", "2026-10-20T13:00Z"])
    resumos = resumo_sensor_console(db=db, input_fn=lambda _p: next(respostas), output_fn=saida.append)
    assert [r["inicio_periodo"] for r in resumos] == [HORA_10 + timedelta(hours=h) for h in range(3)]
    assert saida[-1] == "3 resumo(s) horário(s)."


def test_console_rejeita_sensor_inexistente_e_intervalo_invertido(db) -> None:
    with pytest.raises(ValueError, match="não está cadastrado"):
        consultar_console(db=db, input_fn=lambda _p: "S999", output_fn=lambda _l: None)

    respostas = iter(["S001", "2026-10-20T10:00Z", "2026-10-20T09:00Z"])
    with pytest.raises(ValueError, match="posterior ao início"):
        resumo_sensor_console(db=db, input_fn=lambda _p: next(respostas), output_fn=lambda _l: None)


def test_exemplos_do_enunciado() -> None:
    """Documentos da seção 4 do enunciado, inseridos como lá (sem o simulador)."""
    from datetime import datetime, timezone

    from app.consultas import para_redis

    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
    client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    nome = f"QualidadeAr_e2e_{uuid4().hex[:8]}"
    banco = client[nome]

    def ts(minuto: int) -> datetime:
        return datetime(2026, 10, 20, 10, minuto, tzinfo=timezone.utc)

    try:
        banco["Sensores"].insert_many([{**s} for s in CATALOGO[:2]])
        banco[MEDICOES].insert_many([
            {"sensor_id": "S001", "timestamp": ts(0), "pm25": 18.5, "pm10": 36.2, "co2": 480, "temperatura": 24.3, "umidade": 68},
            {"sensor_id": "S001", "timestamp": ts(5), "pm25": 21.3, "pm10": 42.8, "co2": 510, "temperatura": 24.8, "umidade": 65},
            {"sensor_id": "S002", "timestamp": ts(5), "pm25": 32.6, "pm10": 58.4, "co2": 590, "temperatura": 25.2, "umidade": 63},
        ])
        banco[MEDICOES].create_index([("sensor_id", 1), ("timestamp", -1)])

        assert len(consultar_medicoes(banco, "S001", ts(0), ts(0) + timedelta(hours=1))) == 2

        s001 = resumo_horario_sensor(banco, "S001", ts(0))
        assert s001 is not None
        assert s001["qt_medicoes"] == 2
        assert s001["pm25_media"] == pytest.approx(19.9)
        assert s001["pm10_media"] == pytest.approx(39.5)
        assert s001["co2_media"] == pytest.approx(495.0)
        assert s001["temperatura_media"] == pytest.approx(24.55)
        assert s001["umidade_media"] == pytest.approx(66.5)
        assert s001["pm25_max"] == 21.3
        assert s001["pm10_max"] == 42.8

        s002 = resumo_horario_sensor(banco, "S002", ts(0))
        assert s002 is not None and s002["qt_medicoes"] == 1 and s002["pm25_max"] == 32.6

        assert para_redis(s001)["inicio_periodo"] == "2026-10-20T10:00:00Z"
        assert para_redis(s001)["fim_periodo"] == "2026-10-20T11:00:00Z"
        assert set(ultimos_resumos_por_sensor(banco)) == {"S001", "S002"}
    finally:
        client.drop_database(nome)
        client.close()
