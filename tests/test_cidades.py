"""Testes da agregação por cidade, sem acesso ao banco."""

from datetime import datetime, timezone

import pytest

from app.cidades import (
    CAMPOS_RESUMO_CIDADE,
    imprimir_resumos_cidade,
    normalizar_cidade,
    para_redis,
    pipeline_resumo_horario_cidade,
)

UTC = timezone.utc
INICIO = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
FIM = datetime(2026, 10, 20, 11, 0, tzinfo=UTC)

RESUMO = {
    "cidade": "Chapecó",
    "uf": "SC",
    "inicio_periodo": INICIO,
    "fim_periodo": FIM,
    "qt_medicoes": 24,
    "qt_sensores": 2,
    "pm25_media": 22.4,
    "pm10_media": 43.7,
    "co2_media": 530.8,
    "temperatura_media": 24.6,
    "umidade_media": 65.2,
    "pm25_max": 38.2,
    "pm10_max": 71.5,
}


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("Chapecó", "chapeco"),
        ("CHAPECÓ", "chapeco"),
        ("chapeco", "chapeco"),
        ("Florianópolis", "florianopolis"),
        ("Curitiba", "curitiba"),
        ("São Paulo", "sao_paulo"),
        ("  Chapecó  ", "chapeco"),
        ("Santa Cruz do Sul", "santa_cruz_do_sul"),
    ],
)
def test_normalizacao_do_enunciado(entrada: str, esperado: str) -> None:
    assert normalizar_cidade(entrada) == esperado


def test_normalizacao_e_idempotente() -> None:
    uma_vez = normalizar_cidade("Chapecó")
    assert normalizar_cidade(uma_vez) == uma_vez


def test_pipeline_filtra_antes_de_juntar() -> None:
    etapas = pipeline_resumo_horario_cidade(None, None, INICIO, FIM)
    nomes = [next(iter(e)) for e in etapas]

    # O $match do intervalo vem antes do $lookup: junta menos documentos.
    assert nomes.index("$match") < nomes.index("$lookup")
    assert nomes[:3] == ["$match", "$lookup", "$unwind"]
    assert {"$group", "$sort", "$project"} <= set(nomes)


def test_lookup_liga_medicoes_a_sensores_por_sensor_id() -> None:
    etapas = pipeline_resumo_horario_cidade(None, None, INICIO, FIM)
    lookup = next(e["$lookup"] for e in etapas if "$lookup" in e)

    assert lookup == {
        "from": "Sensores",
        "localField": "sensor_id",
        "foreignField": "_id",
        "as": "sensor",
    }


def test_group_agrupa_medicoes_e_nao_resumos_de_sensores() -> None:
    etapas = pipeline_resumo_horario_cidade(None, None, INICIO, FIM)
    grupo = next(e["$group"] for e in etapas if "$group" in e)

    # A chave do grupo é cidade + hora, sem sensor_id: as médias saem de todas
    # as medições da cidade, não de uma média por sensor.
    assert set(grupo["_id"]) == {"uf", "cidade", "inicio_periodo"}
    assert grupo["qt_medicoes"] == {"$sum": 1}
    assert grupo["pm25_media"] == {"$avg": "$pm25"}
    assert grupo["pm25_max"] == {"$max": "$pm25"}
    assert grupo["pm10_max"] == {"$max": "$pm10"}


def test_pipeline_projeta_os_campos_do_enunciado() -> None:
    etapas = pipeline_resumo_horario_cidade(None, None, INICIO, FIM)
    projecao = next(e["$project"] for e in etapas if "$project" in e)

    assert set(CAMPOS_RESUMO_CIDADE) <= set(projecao)
    assert projecao["_id"] == 0


def test_filtro_por_cidade_entra_depois_do_lookup() -> None:
    etapas = pipeline_resumo_horario_cidade("SC", "Chapecó", INICIO, FIM)
    nomes = [next(iter(e)) for e in etapas]
    posicao = nomes.index("$lookup")

    local = etapas[posicao + 2]["$match"]
    assert local == {
        "sensor.localizacao.uf": "SC",
        "sensor.localizacao.cidade": "Chapecó",
    }


def test_sem_cidade_nao_adiciona_match_extra() -> None:
    todas = pipeline_resumo_horario_cidade(None, None, INICIO, FIM)
    uma = pipeline_resumo_horario_cidade("SC", "Chapecó", INICIO, FIM)

    assert len(uma) == len(todas) + 1


def test_para_redis_usa_formato_do_enunciado() -> None:
    campos = para_redis(RESUMO)

    assert campos["inicio_periodo"] == "2026-10-20T10:00:00Z"
    assert campos["fim_periodo"] == "2026-10-20T11:00:00Z"
    assert campos["qt_medicoes"] == "24"
    assert campos["pm25_media"] == "22.4"
    assert all(isinstance(v, str) for v in campos.values())


def test_para_redis_omite_o_que_ja_esta_na_chave() -> None:
    campos = para_redis(RESUMO)

    assert "cidade" not in campos
    assert "uf" not in campos


def test_imprimir_resumos_mostra_cidade_e_contagens() -> None:
    linhas: list[str] = []
    imprimir_resumos_cidade([RESUMO], linhas.append)

    assert "Chapecó" in linhas[1]
    assert "SC" in linhas[1]
    assert "1 resumo(s)" in linhas[-1]
