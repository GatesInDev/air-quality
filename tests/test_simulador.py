"""Testes do gerador de medições, sem acesso ao banco."""

from datetime import datetime, timedelta, timezone

import pytest

from app.sensores import CATALOGO, GRANDEZAS
from app.simulador import (
    INICIO_PADRAO,
    executar_console,
    gerar_medicoes,
    gerar_medicoes_sensor,
    parse_inicio,
)

CAMPOS = {"sensor_id", "timestamp", "pm25", "pm10", "co2", "temperatura", "umidade"}


def test_catalogo_atende_requisitos_minimos() -> None:
    assert len(CATALOGO) >= 6
    cidades = {s["localizacao"]["cidade"] for s in CATALOGO}
    assert len(cidades) >= 3
    assert len({s["_id"] for s in CATALOGO}) == len(CATALOGO)
    for sensor in CATALOGO:
        assert sensor["ativo"] is True
        assert sensor["grandezas"] == GRANDEZAS
        assert set(sensor["localizacao"]) == {"cidade", "uf", "latitude", "longitude"}


def test_gera_24h_a_cada_5_minutos_para_todos_os_sensores() -> None:
    docs = gerar_medicoes()

    assert len(docs) == 1728
    por_sensor = {s["_id"]: [d for d in docs if d["sensor_id"] == s["_id"]] for s in CATALOGO}
    for serie in por_sensor.values():
        assert len(serie) == 288
        assert serie[0]["timestamp"] == INICIO_PADRAO
        assert serie[-1]["timestamp"] == INICIO_PADRAO + timedelta(hours=24) - timedelta(minutes=5)
        intervalos = {b["timestamp"] - a["timestamp"] for a, b in zip(serie, serie[1:])}
        assert intervalos == {timedelta(minutes=5)}


def test_documentos_tem_campos_tipos_e_faixas_validos() -> None:
    for doc in gerar_medicoes():
        assert set(doc) == CAMPOS
        assert isinstance(doc["timestamp"], datetime)
        assert doc["timestamp"].tzinfo == timezone.utc
        assert isinstance(doc["pm25"], float) and 0 < doc["pm25"] <= 150
        assert isinstance(doc["pm10"], float) and doc["pm10"] > doc["pm25"]
        assert isinstance(doc["co2"], int) and 380 <= doc["co2"] <= 1500
        assert isinstance(doc["temperatura"], float) and -5 <= doc["temperatura"] <= 42
        assert isinstance(doc["umidade"], int) and 20 <= doc["umidade"] <= 99


def test_mesma_seed_gera_mesmos_dados_e_seed_diferente_muda() -> None:
    sensor = CATALOGO[0]
    a = gerar_medicoes_sensor(sensor, seed=1)
    b = gerar_medicoes_sensor(sensor, seed=1)
    c = gerar_medicoes_sensor(sensor, seed=2)

    assert a == b
    assert a != c


def test_sensores_distintos_geram_series_distintas() -> None:
    a = gerar_medicoes_sensor(CATALOGO[0])
    b = gerar_medicoes_sensor(CATALOGO[1])
    assert [d["pm25"] for d in a] != [d["pm25"] for d in b]


def test_cada_hora_cheia_tem_12_medicoes_por_sensor() -> None:
    docs = gerar_medicoes_sensor(CATALOGO[0])
    janela = [d for d in docs if INICIO_PADRAO + timedelta(hours=10) <= d["timestamp"] < INICIO_PADRAO + timedelta(hours=11)]
    assert len(janela) == 12


def test_parametros_invalidos() -> None:
    sensor = CATALOGO[0]
    with pytest.raises(ValueError):
        gerar_medicoes_sensor(sensor, inicio=datetime(2026, 10, 20))
    with pytest.raises(ValueError):
        gerar_medicoes_sensor(sensor, horas=0)
    with pytest.raises(ValueError):
        gerar_medicoes_sensor(sensor, passo_minutos=7)
    with pytest.raises(ValueError):
        gerar_medicoes_sensor(sensor, inicio=INICIO_PADRAO + timedelta(minutes=3))


def test_parse_inicio_aceita_z_e_sem_fuso() -> None:
    assert parse_inicio("2026-10-20T00:00:00Z") == INICIO_PADRAO
    assert parse_inicio("2026-10-20T00:00") == INICIO_PADRAO
    assert parse_inicio("2026-10-19T21:00-03:00") == INICIO_PADRAO


def test_console_cancela_sem_tocar_no_banco() -> None:
    respostas = iter(["", "", "n"])
    saida: list[str] = []

    resultado = executar_console(db=None, input_fn=lambda _p: next(respostas), output_fn=saida.append)

    assert resultado is None
    assert saida[-1] == "Geração cancelada."
    assert any("1728 medições" in linha for linha in saida)
