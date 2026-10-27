"""Teste de integração do gerador com o MongoDB iniciado via Docker Compose."""

from datetime import timedelta
import os
from uuid import uuid4

import pytest
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, WriteError

from app.db import MEDICOES, SENSORES
from app.sensores import CATALOGO
from app.simulador import INICIO_PADRAO, executar

pytestmark = pytest.mark.e2e


@pytest.fixture
def db():
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
    client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    client.admin.command("ping")
    nome = f"QualidadeAr_e2e_{uuid4().hex[:8]}"
    try:
        yield client[nome]
    finally:
        client.drop_database(nome)
        client.close()


def test_executar_cadastra_sensores_gera_1728_medicoes_e_e_idempotente(db) -> None:
    primeiro = executar(db)

    assert primeiro.sensores_cadastrados == len(CATALOGO)
    assert primeiro.removidas == 0
    assert primeiro.inseridas == 1728
    assert db[SENSORES].count_documents({}) == len(CATALOGO)
    assert db[MEDICOES].count_documents({}) == 1728

    indices = db[MEDICOES].index_information()
    assert indices["sensor_id_1_timestamp_-1"]["key"] == [("sensor_id", 1), ("timestamp", -1)]
    assert indices["sensor_id_1_timestamp_-1"]["unique"] is True

    segundo = executar(db)
    assert segundo.removidas == 1728
    assert segundo.inseridas == 1728
    assert db[MEDICOES].count_documents({}) == 1728


def test_consulta_por_sensor_e_intervalo_retorna_12_por_hora(db) -> None:
    executar(db)
    inicio = INICIO_PADRAO + timedelta(hours=10)
    fim = inicio + timedelta(hours=1)

    docs = list(
        db[MEDICOES]
        .find({"sensor_id": "S001", "timestamp": {"$gte": inicio, "$lt": fim}})
        .sort("timestamp", 1)
    )

    assert len(docs) == 12
    assert docs[0]["timestamp"] == inicio
    assert docs[-1]["timestamp"] == fim - timedelta(minutes=5)


def test_validador_e_indice_unico_rejeitam_dados_invalidos(db) -> None:
    executar(db)
    doc_ok = db[MEDICOES].find_one({"sensor_id": "S001"}, {"_id": 0})

    with pytest.raises(DuplicateKeyError):
        db[MEDICOES].insert_one(doc_ok)

    invalido = {**doc_ok, "timestamp": doc_ok["timestamp"] + timedelta(days=30), "umidade": 150}
    with pytest.raises(WriteError):
        db[MEDICOES].insert_one(invalido)

    incompleto = {k: v for k, v in doc_ok.items() if k != "co2"}
    incompleto["timestamp"] = doc_ok["timestamp"] + timedelta(days=31)
    with pytest.raises(WriteError):
        db[MEDICOES].insert_one(incompleto)
