"""Teste de integração real com os serviços iniciados via Docker Compose."""

from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest
import redis
from pymongo import MongoClient

pytestmark = pytest.mark.e2e


@pytest.fixture
def clients():
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
    redis_client = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=int(os.getenv("REDIS_DB", "0")),
        decode_responses=True,
        socket_connect_timeout=2,
    )
    mongo_client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    try:
        yield mongo_client, redis_client
    finally:
        redis_client.close()
        mongo_client.close()


def test_mongodb_measurement_and_redis_summary_round_trip(clients) -> None:
    mongo_client, redis_client = clients
    suffix = uuid4().hex
    database = mongo_client["QualidadeAr_e2e"]
    measurements = database["Medicoes"]
    key = f"e2e:qualidadear:sensor:{suffix}"
    measurement = {
        "sensor_id": f"E2E-{suffix}",
        "timestamp": datetime(2026, 10, 22, 10, 5, tzinfo=timezone.utc),
        "pm25": 18.5,
        "pm10": 36.2,
        "co2": 480.0,
        "temperatura": 24.3,
        "umidade": 68.0,
    }
    summary = {
        "qt_medicoes": "1",
        "pm25_media": str(measurement["pm25"]),
        "pm10_media": str(measurement["pm10"]),
        "co2_media": str(measurement["co2"]),
        "temperatura_media": str(measurement["temperatura"]),
        "umidade_media": str(measurement["umidade"]),
    }

    try:
        mongo_client.admin.command("ping")
        redis_client.ping()

        measurements.insert_one(measurement)
        redis_client.hset(key, mapping=summary)

        stored_measurement = measurements.find_one(
            {"sensor_id": measurement["sensor_id"]},
            {"_id": 0},
        )
        stored_summary = redis_client.hgetall(key)

        assert stored_measurement is not None
        assert stored_measurement["sensor_id"] == measurement["sensor_id"]
        assert stored_measurement["timestamp"] == measurement["timestamp"]
        assert stored_summary == summary
        assert float(stored_summary["pm25_media"]) == stored_measurement["pm25"]
    finally:
        measurements.delete_one({"sensor_id": measurement["sensor_id"]})
        redis_client.delete(key)
