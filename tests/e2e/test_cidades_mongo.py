"""Agregação por cidade contra o MongoDB do Docker Compose."""

from datetime import timedelta
import os
from statistics import mean
from uuid import uuid4

import pytest
from pymongo import MongoClient

from app.cidades import (
    CAMPOS_RESUMO_CIDADE,
    cidades_cadastradas,
    comparar_medias,
    resumo_cidade_console,
    resumo_horario_cidade,
    resumos_horarios_cidade,
    ultimo_resumo_cidade,
    ultimos_resumos_por_cidade,
)
from app.consultas import consultar_medicoes, resumos_horarios
from app.db import MEDICOES, SENSORES
from app.periodo import parse_data_utc
from app.simulador import INICIO_PADRAO, executar

pytestmark = pytest.mark.e2e

HORA_10 = INICIO_PADRAO + timedelta(hours=10)
UMA_HORA = timedelta(hours=1)


def _client() -> MongoClient:
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
    client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    client.admin.command("ping")
    return client


@pytest.fixture
def db():
    """Base completa do simulador: 6 sensores, 3 cidades, 24 h de medições."""
    client = _client()
    nome = f"QualidadeAr_e2e_{uuid4().hex[:8]}"
    try:
        banco = client[nome]
        executar(banco)
        yield banco
    finally:
        client.drop_database(nome)
        client.close()


@pytest.fixture
def db_desbalanceado():
    """Os três documentos de exemplo do enunciado: S001 com 2 medições, S002 com 1."""
    client = _client()
    nome = f"QualidadeAr_e2e_{uuid4().hex[:8]}"
    try:
        banco = client[nome]
        banco[SENSORES].insert_many(
            [
                {
                    "_id": "S001",
                    "modelo": "AirMonitor-200",
                    "descricao": "Sensor ambiental do centro",
                    "localizacao": {
                        "cidade": "Chapecó",
                        "uf": "SC",
                        "latitude": -27.1009,
                        "longitude": -52.6157,
                    },
                    "ativo": True,
                    "grandezas": ["PM2.5"],
                },
                {
                    "_id": "S002",
                    "modelo": "AirMonitor-200",
                    "descricao": "Sensor ambiental da zona norte",
                    "localizacao": {
                        "cidade": "Chapecó",
                        "uf": "SC",
                        "latitude": -27.0748,
                        "longitude": -52.6240,
                    },
                    "ativo": True,
                    "grandezas": ["PM2.5"],
                },
            ]
        )
        banco[MEDICOES].insert_many(
            [
                {
                    "sensor_id": "S001",
                    "timestamp": parse_data_utc("2026-10-20T10:00:00Z"),
                    "pm25": 18.5,
                    "pm10": 36.2,
                    "co2": 480,
                    "temperatura": 24.3,
                    "umidade": 68,
                },
                {
                    "sensor_id": "S001",
                    "timestamp": parse_data_utc("2026-10-20T10:05:00Z"),
                    "pm25": 21.3,
                    "pm10": 42.8,
                    "co2": 510,
                    "temperatura": 24.8,
                    "umidade": 65,
                },
                {
                    "sensor_id": "S002",
                    "timestamp": parse_data_utc("2026-10-20T10:05:00Z"),
                    "pm25": 32.6,
                    "pm10": 58.4,
                    "co2": 590,
                    "temperatura": 25.2,
                    "umidade": 63,
                },
            ]
        )
        yield banco
    finally:
        client.drop_database(nome)
        client.close()


def test_cidades_do_catalogo(db) -> None:
    locais = cidades_cadastradas(db)

    assert [(l["cidade"], l["uf"]) for l in locais] == [
        ("Curitiba", "PR"),
        ("Chapecó", "SC"),
        ("Florianópolis", "SC"),
    ]
    assert all(l["qt_sensores"] == 2 for l in locais)


def test_resumo_bate_com_calculo_em_python(db) -> None:
    sensores = [
        s["_id"]
        for s in db[SENSORES].find({"localizacao.cidade": "Chapecó"}, {"_id": 1})
    ]
    medicoes = [
        m
        for sid in sensores
        for m in consultar_medicoes(db, sid, HORA_10, HORA_10 + UMA_HORA)
    ]

    resumo = resumo_horario_cidade(db, "SC", "Chapecó", HORA_10 + timedelta(minutes=37))

    assert resumo is not None
    assert tuple(resumo) == CAMPOS_RESUMO_CIDADE
    assert resumo["qt_medicoes"] == len(medicoes) == 24
    assert resumo["qt_sensores"] == 2
    assert resumo["pm25_media"] == pytest.approx(mean(m["pm25"] for m in medicoes), abs=0.01)
    assert resumo["co2_media"] == pytest.approx(mean(m["co2"] for m in medicoes), abs=0.01)
    assert resumo["pm25_max"] == max(m["pm25"] for m in medicoes)
    assert resumo["pm10_max"] == max(m["pm10"] for m in medicoes)


def test_qt_medicoes_da_cidade_e_a_soma_dos_sensores(db) -> None:
    cidade = resumo_horario_cidade(db, "SC", "Chapecó", HORA_10)
    por_sensor = [
        r
        for r in resumos_horarios(db, None, HORA_10, HORA_10 + UMA_HORA)
        if r["sensor_id"] in {"S001", "S002"}
    ]

    assert cidade is not None
    assert cidade["qt_medicoes"] == sum(r["qt_medicoes"] for r in por_sensor)


def test_janela_fechada_no_inicio_e_aberta_no_fim(db) -> None:
    resumos = resumos_horarios_cidade(db, "SC", "Chapecó", HORA_10, HORA_10 + UMA_HORA)

    assert len(resumos) == 1
    assert resumos[0]["inicio_periodo"] == HORA_10
    assert resumos[0]["fim_periodo"] == HORA_10 + UMA_HORA


def test_todas_as_cidades_de_uma_vez(db) -> None:
    resumos = resumos_horarios_cidade(db, None, None, HORA_10, HORA_10 + UMA_HORA)

    assert len(resumos) == 3
    assert {r["cidade"] for r in resumos} == {"Chapecó", "Florianópolis", "Curitiba"}
    assert sum(r["qt_medicoes"] for r in resumos) == 72  # 6 sensores x 12


def test_medicao_de_sensor_nao_cadastrado_fica_de_fora(db) -> None:
    antes = resumo_horario_cidade(db, "SC", "Chapecó", HORA_10)
    db[MEDICOES].insert_one(
        {
            "sensor_id": "S999",
            "timestamp": HORA_10,
            "pm25": 999.0,
            "pm10": 999.0,
            "co2": 9990,
            "temperatura": 30.0,
            "umidade": 50,
        }
    )

    depois = resumos_horarios_cidade(db, None, None, HORA_10, HORA_10 + UMA_HORA)

    assert antes is not None
    # Sem sensor cadastrado não há cidade a que atribuir a medição.
    assert sum(r["qt_medicoes"] for r in depois) == 72
    assert all(r["pm25_max"] < 999.0 for r in depois)


def test_ultimo_resumo_de_cada_cidade(db) -> None:
    resumos = ultimos_resumos_por_cidade(db)

    assert set(resumos) == {"SC:chapeco", "SC:florianopolis", "PR:curitiba"}
    for resumo in resumos.values():
        assert resumo["fim_periodo"] == INICIO_PADRAO + timedelta(hours=24)
        assert resumo["qt_medicoes"] == 24


def test_cidade_sem_medicao_nao_gera_resumo(db) -> None:
    db[MEDICOES].delete_many({"sensor_id": {"$in": ["S001", "S002"]}})

    assert ultimo_resumo_cidade(db, "SC", "Chapecó") is None
    assert "SC:chapeco" not in ultimos_resumos_por_cidade(db)


def test_media_global_difere_da_media_das_medias(db_desbalanceado) -> None:
    """O ponto destacado no enunciado, com os dados de exemplo dele.

    S001 tem 2 medições (18.5 e 21.3) e S002 tem 1 (32.6).
    Média global      = (18.5 + 21.3 + 32.6) / 3 = 24.13
    Média das médias  = (19.9 + 32.6) / 2        = 26.25
    """
    hora = parse_data_utc("2026-10-20T10:00:00Z")

    comparacao = comparar_medias(db_desbalanceado, "SC", "Chapecó", hora)

    assert comparacao is not None
    assert comparacao["qt_medicoes_por_sensor"] == {"S001": 2, "S002": 1}
    assert comparacao["media_global"] == pytest.approx(24.13, abs=0.01)
    assert comparacao["media_das_medias"] == pytest.approx(26.25, abs=0.01)
    assert comparacao["diferenca"] == pytest.approx(-2.12, abs=0.01)


def test_medias_coincidem_com_sensores_equilibrados(db) -> None:
    """Com 12 medições em cada sensor os pesos são iguais e a diferença some."""
    comparacao = comparar_medias(db, "SC", "Chapecó", HORA_10)

    assert comparacao is not None
    assert set(comparacao["qt_medicoes_por_sensor"].values()) == {12}
    assert comparacao["diferenca"] == pytest.approx(0.0, abs=0.01)


def test_console_aceita_cidade_sem_acento(db) -> None:
    linhas: list[str] = []
    respostas = iter(["chapeco", "", ""])

    resumos = resumo_cidade_console(
        db=db, input_fn=lambda _: next(respostas), output_fn=linhas.append
    )

    assert len(resumos) == 24
    assert any("Chapecó/SC" in l for l in linhas)
    assert any("média global" in l for l in linhas)


def test_console_recusa_cidade_desconhecida(db) -> None:
    respostas = iter(["Joinville"])

    with pytest.raises(ValueError, match="não tem sensores"):
        resumo_cidade_console(
            db=db, input_fn=lambda _: next(respostas), output_fn=lambda _: None
        )
