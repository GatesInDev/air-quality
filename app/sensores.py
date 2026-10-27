"""Catálogo de sensores e preparação das coleções no MongoDB.

Os documentos seguem o modelo da seção 4 do enunciado: coleções
``Sensores`` e ``Medicoes`` no banco ``QualidadeAr``.
"""

from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database

from app.db import MEDICOES, SENSORES

GRANDEZAS = ["PM2.5", "PM10", "CO2", "temperatura", "umidade"]

# Seis sensores em três cidades. Coordenadas meramente ilustrativas.
CATALOGO: list[dict[str, Any]] = [
    {
        "_id": "S001",
        "modelo": "AirMonitor-200",
        "descricao": "Sensor ambiental do centro",
        "localizacao": {"cidade": "Chapecó", "uf": "SC", "latitude": -27.1009, "longitude": -52.6157},
        "ativo": True,
        "grandezas": GRANDEZAS,
    },
    {
        "_id": "S002",
        "modelo": "AirMonitor-200",
        "descricao": "Sensor ambiental da zona norte",
        "localizacao": {"cidade": "Chapecó", "uf": "SC", "latitude": -27.0748, "longitude": -52.6240},
        "ativo": True,
        "grandezas": GRANDEZAS,
    },
    {
        "_id": "S003",
        "modelo": "AirMonitor-200",
        "descricao": "Sensor ambiental do centro",
        "localizacao": {"cidade": "Florianópolis", "uf": "SC", "latitude": -27.5954, "longitude": -48.5480},
        "ativo": True,
        "grandezas": GRANDEZAS,
    },
    {
        "_id": "S004",
        "modelo": "AirMonitor-300",
        "descricao": "Sensor ambiental da orla da Lagoa",
        "localizacao": {"cidade": "Florianópolis", "uf": "SC", "latitude": -27.6000, "longitude": -48.4700},
        "ativo": True,
        "grandezas": GRANDEZAS,
    },
    {
        "_id": "S005",
        "modelo": "AirMonitor-300",
        "descricao": "Sensor ambiental do centro",
        "localizacao": {"cidade": "Curitiba", "uf": "PR", "latitude": -25.4284, "longitude": -49.2733},
        "ativo": True,
        "grandezas": GRANDEZAS,
    },
    {
        "_id": "S006",
        "modelo": "AirMonitor-300",
        "descricao": "Sensor ambiental da zona industrial",
        "localizacao": {"cidade": "Curitiba", "uf": "PR", "latitude": -25.4950, "longitude": -49.3350},
        "ativo": True,
        "grandezas": GRANDEZAS,
    },
]

_CAMPOS_NUMERICOS = ("pm25", "pm10", "co2", "temperatura", "umidade")

VALIDADOR_SENSORES = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "modelo", "descricao", "localizacao", "ativo", "grandezas"],
        "properties": {
            "_id": {"bsonType": "string"},
            "modelo": {"bsonType": "string"},
            "descricao": {"bsonType": "string"},
            "localizacao": {
                "bsonType": "object",
                "required": ["cidade", "uf", "latitude", "longitude"],
                "properties": {
                    "cidade": {"bsonType": "string"},
                    "uf": {"bsonType": "string", "minLength": 2, "maxLength": 2},
                    "latitude": {"bsonType": "number", "minimum": -90, "maximum": 90},
                    "longitude": {"bsonType": "number", "minimum": -180, "maximum": 180},
                },
            },
            "ativo": {"bsonType": "bool"},
            "grandezas": {"bsonType": "array", "items": {"bsonType": "string"}},
        },
    }
}

VALIDADOR_MEDICOES = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["sensor_id", "timestamp", *_CAMPOS_NUMERICOS],
        "properties": {
            "sensor_id": {"bsonType": "string"},
            "timestamp": {"bsonType": "date"},
            "pm25": {"bsonType": "number", "minimum": 0},
            "pm10": {"bsonType": "number", "minimum": 0},
            "co2": {"bsonType": "number", "minimum": 0},
            "temperatura": {"bsonType": "number"},
            "umidade": {"bsonType": "number", "minimum": 0, "maximum": 100},
        },
    }
}


def _aplicar_validador(db: Database, nome: str, validador: dict[str, Any]) -> None:
    if nome in db.list_collection_names():
        db.command("collMod", nome, validator=validador, validationLevel="strict")
    else:
        db.create_collection(nome, validator=validador)


def preparar_colecoes(db: Database) -> None:
    """Cria (ou atualiza) as coleções com validação de esquema e índices."""
    _aplicar_validador(db, SENSORES, VALIDADOR_SENSORES)
    _aplicar_validador(db, MEDICOES, VALIDADOR_MEDICOES)
    medicoes = db[MEDICOES]
    # Índice do enunciado, único para impedir medições duplicadas.
    medicoes.create_index(
        [("sensor_id", ASCENDING), ("timestamp", DESCENDING)],
        name="sensor_id_1_timestamp_-1",
        unique=True,
    )
    # Apoio à agregação por cidade, que filtra só pela janela de tempo.
    medicoes.create_index([("timestamp", ASCENDING)], name="timestamp_1")


def cadastrar_sensores(db: Database, sensores: list[dict[str, Any]] | None = None) -> int:
    """Insere ou atualiza os sensores do catálogo. Retorna a quantidade enviada."""
    sensores = sensores if sensores is not None else CATALOGO
    colecao = db[SENSORES]
    for sensor in sensores:
        colecao.replace_one({"_id": sensor["_id"]}, sensor, upsert=True)
    return len(sensores)


def listar_sensores(db: Database, apenas_ativos: bool = False) -> list[dict[str, Any]]:
    filtro = {"ativo": True} if apenas_ativos else {}
    return list(db[SENSORES].find(filtro).sort("_id", ASCENDING))
