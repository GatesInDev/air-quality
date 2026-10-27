"""Acesso compartilhado ao MongoDB e nomes das coleções do projeto."""

from pymongo import MongoClient
from pymongo.database import Database

from app.config import Settings

SENSORES = "Sensores"
MEDICOES = "Medicoes"


def get_client(settings: Settings | None = None) -> MongoClient:
    """Cria um cliente com datetimes cientes de fuso (UTC) na leitura."""
    settings = settings or Settings.from_env()
    return MongoClient(
        settings.mongodb_uri,
        tz_aware=True,
        serverSelectionTimeoutMS=5000,
    )


def get_database(settings: Settings | None = None) -> Database:
    settings = settings or Settings.from_env()
    return get_client(settings)[settings.mongodb_database]
