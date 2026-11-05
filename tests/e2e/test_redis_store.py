"""Hashes do Redis contra os serviços do Docker Compose.

Os testes usam um banco Redis separado (``REDIS_TEST_DB``, 15 por padrão) para
não mexer nos Hashes que o menu grava no banco de trabalho.
"""

import os
from uuid import uuid4

import pytest
import redis
from pymongo import MongoClient

from app.cidades import ultimos_resumos_por_cidade
from app.consultas import ultimos_resumos_por_sensor
from app.db import MEDICOES
from app.redis_store import (
    atualizar_console,
    atualizar_tudo,
    chave_cidade,
    chave_sensor,
    gravar_resumo,
    gravar_resumos_cidades,
    gravar_resumos_sensores,
    ler_hash,
    ler_hashes,
    limpar,
    listar_chaves,
    ver_hashes_console,
)
from app.simulador import executar

pytestmark = pytest.mark.e2e


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


@pytest.fixture
def cliente():
    conexao = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=int(os.getenv("REDIS_TEST_DB", "15")),
        decode_responses=True,
        socket_connect_timeout=2,
    )
    conexao.ping()
    try:
        limpar(conexao)
        yield conexao
    finally:
        limpar(conexao)
        conexao.close()


def test_chaves_seguem_o_padrao_do_enunciado() -> None:
    assert chave_sensor("S001") == "qualidadear:sensor:S001"
    assert chave_sensor("s001") == "qualidadear:sensor:S001"
    assert chave_cidade("SC", "Chapecó") == "qualidadear:cidade:SC:chapeco"
    assert chave_cidade("sc", "CHAPECÓ") == "qualidadear:cidade:SC:chapeco"
    assert chave_cidade("SC", "Florianópolis") == "qualidadear:cidade:SC:florianopolis"


def test_grava_um_hash_por_sensor(db, cliente) -> None:
    resumos = gravar_resumos_sensores(db, cliente)

    assert set(resumos) == {"S001", "S002", "S003", "S004", "S005", "S006"}
    for sensor_id, resumo in resumos.items():
        campos = ler_hash(cliente, chave_sensor(sensor_id))
        assert cliente.type(chave_sensor(sensor_id)) == "hash"
        assert campos["qt_medicoes"] == str(resumo["qt_medicoes"])
        assert campos["pm25_media"] == str(resumo["pm25_media"])
        assert "sensor_id" not in campos  # já está na chave


def test_grava_um_hash_por_cidade(db, cliente) -> None:
    resumos = gravar_resumos_cidades(db, cliente)

    assert set(resumos) == {"SC:chapeco", "SC:florianopolis", "PR:curitiba"}
    for resumo in resumos.values():
        campos = ler_hash(cliente, chave_cidade(resumo["uf"], resumo["cidade"]))
        assert campos["qt_medicoes"] == str(resumo["qt_medicoes"])
        assert campos["qt_sensores"] == "2"
        assert "cidade" not in campos and "uf" not in campos


def test_valores_do_redis_batem_com_os_do_mongodb(db, cliente) -> None:
    atualizar_tudo(db, cliente)

    for sensor_id, resumo in ultimos_resumos_por_sensor(db).items():
        campos = ler_hash(cliente, chave_sensor(sensor_id))
        assert float(campos["pm25_media"]) == resumo["pm25_media"]
        assert float(campos["pm10_max"]) == resumo["pm10_max"]
    for resumo in ultimos_resumos_por_cidade(db).values():
        campos = ler_hash(cliente, chave_cidade(resumo["uf"], resumo["cidade"]))
        assert float(campos["co2_media"]) == resumo["co2_media"]
        assert int(campos["qt_medicoes"]) == resumo["qt_medicoes"]


def test_datas_no_formato_iso_do_enunciado(db, cliente) -> None:
    atualizar_tudo(db, cliente)

    campos = ler_hash(cliente, chave_cidade("SC", "Chapecó"))
    assert campos["inicio_periodo"] == "2026-10-20T23:00:00Z"
    assert campos["fim_periodo"] == "2026-10-21T00:00:00Z"


def test_regravar_nao_deixa_campo_de_execucao_anterior(db, cliente) -> None:
    chave = chave_sensor("S001")
    gravar_resumo(cliente, chave, {"qt_medicoes": "1", "campo_antigo": "sobra"})

    gravar_resumos_sensores(db, cliente)

    campos = ler_hash(cliente, chave)
    assert "campo_antigo" not in campos
    assert campos["qt_medicoes"] == "12"


def test_atualizar_e_idempotente(db, cliente) -> None:
    atualizar_tudo(db, cliente)
    primeira = ler_hashes(cliente)

    atualizar_tudo(db, cliente)

    assert ler_hashes(cliente) == primeira


def test_listar_e_limpar_so_tocam_nas_chaves_do_projeto(db, cliente) -> None:
    cliente.set("outro:sistema:chave", "preservar")
    atualizar_tudo(db, cliente)

    assert len(listar_chaves(cliente)) == 9  # 6 sensores + 3 cidades
    assert listar_chaves(cliente) == sorted(listar_chaves(cliente))

    removidas = limpar(cliente)

    assert removidas == 9
    assert cliente.get("outro:sistema:chave") == "preservar"
    cliente.delete("outro:sistema:chave")


def test_ler_hash_inexistente_devolve_vazio(cliente) -> None:
    assert ler_hash(cliente, "qualidadear:sensor:NAO_EXISTE") == {}


def test_hash_some_quando_a_fonte_perde_as_medicoes(db, cliente) -> None:
    """Sem resumo no MongoDB não pode sobrar Hash antigo no Redis.

    O Redis é uma visão do MongoDB: um Hash que a fonte não sustenta mais
    passaria números velhos para quem lê.
    """
    atualizar_tudo(db, cliente)
    assert ler_hash(cliente, chave_cidade("SC", "Chapecó"))

    db[MEDICOES].delete_many({"sensor_id": {"$in": ["S001", "S002"]}})
    atualizar_tudo(db, cliente)

    assert ler_hash(cliente, chave_cidade("SC", "Chapecó")) == {}
    assert ler_hash(cliente, chave_sensor("S001")) == {}
    # As demais cidades continuam intactas.
    assert ler_hash(cliente, chave_cidade("PR", "Curitiba"))
    assert len(listar_chaves(cliente)) == 4 + 2  # 4 sensores + 2 cidades


def test_console_de_atualizacao_lista_as_chaves(db, cliente) -> None:
    linhas: list[str] = []

    sensores, cidades = atualizar_console(db=db, cliente=cliente, output_fn=linhas.append)

    assert len(sensores) == 6 and len(cidades) == 3
    texto = "\n".join(linhas)
    assert "qualidadear:sensor:S001" in texto
    assert "qualidadear:cidade:SC:chapeco" in texto


def test_console_de_leitura_mostra_os_campos(db, cliente) -> None:
    atualizar_tudo(db, cliente)
    linhas: list[str] = []

    hashes = ver_hashes_console(cliente=cliente, output_fn=linhas.append)

    assert len(hashes) == 9
    texto = "\n".join(linhas)
    assert "qualidadear:cidade:PR:curitiba" in texto
    assert "pm25_media" in texto
    assert "9 Hash(es)" in linhas[-1]


def test_console_de_leitura_sem_dados_orienta_o_usuario(cliente) -> None:
    linhas: list[str] = []

    assert ver_hashes_console(cliente=cliente, output_fn=linhas.append) == {}
    assert "opção 5" in linhas[0]


def test_todos_os_campos_conferem_com_historico_independente(db, cliente):
    """Confere todas as estatísticas sem reutilizar os serializadores/pipelines."""
    from datetime import timedelta
    from statistics import mean
    from app.simulador import INICIO_PADRAO

    atualizar_tudo(db, cliente)
    inicio = INICIO_PADRAO + timedelta(hours=23)
    fim = inicio + timedelta(hours=1)
    sensores = list(db['Sensores'].find({}))
    grupos = {chave_sensor(s['_id']): {s['_id']} for s in sensores}
    for sensor in sensores:
        local = sensor['localizacao']
        grupos.setdefault(chave_cidade(local['uf'], local['cidade']), set()).add(sensor['_id'])
    assert set(ler_hashes(cliente)) == set(grupos)
    for chave, ids in grupos.items():
        docs = list(db[MEDICOES].find({'sensor_id': {'$in': list(ids)},
                                     'timestamp': {'$gte': inicio, '$lt': fim}}))
        esperado = {'inicio_periodo': '2026-10-20T23:00:00Z',
                    'fim_periodo': '2026-10-21T00:00:00Z', 'qt_medicoes': str(len(docs))}
        if ':cidade:' in chave:
            esperado['qt_sensores'] = str(len(ids))
        for campo in ('pm25', 'pm10', 'co2', 'temperatura', 'umidade'):
            esperado[f'{campo}_media'] = str(round(mean(d[campo] for d in docs), 2))
        for campo in ('pm25', 'pm10'):
            esperado[f'{campo}_max'] = str(max(d[campo] for d in docs))
        atual = ler_hash(cliente, chave)
        assert set(atual) == set(esperado)
        for campo, valor in esperado.items():
            if campo.endswith('_media'):
                assert float(atual[campo]) == pytest.approx(float(valor), abs=0.01)
            else:
                assert atual[campo] == valor


def test_nova_hora_substitui_apenas_resumos_afetados(db, cliente):
    from datetime import timedelta
    from app.simulador import INICIO_PADRAO

    atualizar_tudo(db, cliente)
    anterior = ler_hashes(cliente)
    db[MEDICOES].insert_one({
        'sensor_id': 'S001', 'timestamp': INICIO_PADRAO + timedelta(days=1),
        'pm25': 90.0, 'pm10': 110.0, 'co2': 600, 'temperatura': 20.0, 'umidade': 70,
    })
    atualizar_tudo(db, cliente)
    atual = ler_hashes(cliente)
    afetados = {chave_sensor('S001'), chave_cidade('SC', 'Chapecó')}
    assert set(atual) == set(anterior)
    for chave in afetados:
        assert atual[chave]['inicio_periodo'] == '2026-10-21T00:00:00Z'
        assert atual[chave]['qt_medicoes'] == '1'
        assert float(atual[chave]['pm25_media']) == 90.0
    for chave in set(atual) - afetados:
        assert atual[chave] == anterior[chave]
