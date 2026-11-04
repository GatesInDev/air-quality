"""Hashes do Redis com os últimos resumos (opções 5 e 6 do menu).

O MongoDB guarda o histórico completo; o Redis guarda só a visão mais recente,
pronta para leitura rápida. Cada resumo vira um Hash, no padrão da seção 6 do
enunciado:

===========================================  ==========================
chave                                        conteúdo
===========================================  ==========================
``qualidadear:sensor:<ID>``                  último resumo do sensor
``qualidadear:cidade:<UF>:<cidade>``         último resumo da cidade
===========================================  ==========================

A cidade entra normalizada (``Chapecó`` -> ``chapeco``) e a UF em maiúsculas.

``HSET`` sobrescreve campo a campo e não remove os que deixaram de existir, por
isso ``gravar_resumo`` apaga a chave antes de regravar: o Hash passa a refletir
exatamente o último resumo, sem sobras de uma execução anterior. Os dois
comandos vão num ``pipeline`` para que a chave nunca fique vazia para quem
estiver lendo em paralelo.

Pelo mesmo motivo a atualização apaga os Hashes de sensores e cidades que
deixaram de ter medições: o Redis só mostra o que o MongoDB sustenta.

Todos os valores de um Hash são texto — é assim que o Redis funciona. A
conversão fica em ``consultas.para_redis`` e ``cidades.para_redis``, que
formatam datas como ``2026-10-20T10:00:00Z``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import redis
from pymongo.database import Database

from app.cidades import normalizar_cidade, ultimos_resumos_por_cidade
from app.cidades import para_redis as cidade_para_redis
from app.config import Settings
from app.consultas import para_redis as sensor_para_redis
from app.consultas import ultimos_resumos_por_sensor

PREFIXO = "qualidadear"


def get_redis(settings: Settings | None = None) -> redis.Redis:
    """Cliente com respostas já decodificadas em ``str``."""
    settings = settings or Settings.from_env()
    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        decode_responses=True,
    )


def chave_sensor(sensor_id: str) -> str:
    """``"S001"`` -> ``"qualidadear:sensor:S001"``."""
    return f"{PREFIXO}:sensor:{sensor_id.upper()}"


def chave_cidade(uf: str, cidade: str) -> str:
    """``("SC", "Chapecó")`` -> ``"qualidadear:cidade:SC:chapeco"``."""
    return f"{PREFIXO}:cidade:{uf.upper()}:{normalizar_cidade(cidade)}"


def gravar_resumo(cliente: redis.Redis, chave: str, campos: dict[str, str]) -> None:
    """Substitui o Hash ``chave`` pelos ``campos`` (DEL + HSET atômicos)."""
    if not campos:
        return
    with cliente.pipeline(transaction=True) as pipe:
        pipe.delete(chave)
        pipe.hset(chave, mapping=campos)
        pipe.execute()


def _descartar_obsoletos(cliente: redis.Redis, padrao: str, atuais: set[str]) -> int:
    """Apaga as chaves de ``padrao`` que não estão em ``atuais``.

    Sem isso o Redis guardaria o resumo de um sensor ou cidade que não tem mais
    medições no MongoDB, entregando números velhos a quem lesse o Hash.
    """
    obsoletas = [c for c in listar_chaves(cliente, padrao) if c not in atuais]
    return cliente.delete(*obsoletas) if obsoletas else 0


def gravar_resumos_sensores(
    db: Database,
    cliente: redis.Redis,
) -> dict[str, dict[str, Any]]:
    """Grava ``qualidadear:sensor:<ID>`` com o último resumo de cada sensor."""
    resumos = ultimos_resumos_por_sensor(db)
    chaves = set()
    for sensor_id, resumo in resumos.items():
        chave = chave_sensor(sensor_id)
        gravar_resumo(cliente, chave, sensor_para_redis(resumo))
        chaves.add(chave)
    _descartar_obsoletos(cliente, f"{PREFIXO}:sensor:*", chaves)
    return resumos


def gravar_resumos_cidades(
    db: Database,
    cliente: redis.Redis,
) -> dict[str, dict[str, Any]]:
    """Grava ``qualidadear:cidade:<UF>:<cidade>`` com o último resumo de cada cidade."""
    resumos = ultimos_resumos_por_cidade(db)
    chaves = set()
    for resumo in resumos.values():
        chave = chave_cidade(resumo["uf"], resumo["cidade"])
        gravar_resumo(cliente, chave, cidade_para_redis(resumo))
        chaves.add(chave)
    _descartar_obsoletos(cliente, f"{PREFIXO}:cidade:*", chaves)
    return resumos


def atualizar_tudo(
    db: Database,
    cliente: redis.Redis,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Recarrega todos os Hashes a partir do MongoDB. Retorna (sensores, cidades)."""
    return gravar_resumos_sensores(db, cliente), gravar_resumos_cidades(db, cliente)


def listar_chaves(cliente: redis.Redis, padrao: str = f"{PREFIXO}:*") -> list[str]:
    """Chaves do projeto em ordem alfabética.

    Usa ``SCAN`` em vez de ``KEYS`` porque ``KEYS`` percorre o banco inteiro de
    uma vez e trava o servidor em bases grandes.
    """
    return sorted(cliente.scan_iter(match=padrao))


def ler_hash(cliente: redis.Redis, chave: str) -> dict[str, str]:
    """``HGETALL`` da chave; dicionário vazio se ela não existir."""
    return cliente.hgetall(chave)


def ler_hashes(cliente: redis.Redis, padrao: str = f"{PREFIXO}:*") -> dict[str, dict[str, str]]:
    """``HGETALL`` de todas as chaves que casam com ``padrao``."""
    return {chave: ler_hash(cliente, chave) for chave in listar_chaves(cliente, padrao)}


def limpar(cliente: redis.Redis, padrao: str = f"{PREFIXO}:*") -> int:
    """Apaga as chaves do projeto. Retorna quantas foram removidas."""
    chaves = listar_chaves(cliente, padrao)
    return cliente.delete(*chaves) if chaves else 0


# --- Console -----------------------------------------------------------------

OutputFn = Callable[[str], None]

# Ordem de exibição; campos fora desta lista aparecem depois, em ordem alfabética.
ORDEM_CAMPOS = (
    "inicio_periodo",
    "fim_periodo",
    "qt_medicoes",
    "qt_sensores",
    "pm25_media",
    "pm25_max",
    "pm10_media",
    "pm10_max",
    "co2_media",
    "temperatura_media",
    "umidade_media",
)


def _ordenar_campos(campos: dict[str, str]) -> list[tuple[str, str]]:
    conhecidos = [(c, campos[c]) for c in ORDEM_CAMPOS if c in campos]
    extras = sorted((c, v) for c, v in campos.items() if c not in ORDEM_CAMPOS)
    return conhecidos + extras


def imprimir_hash(chave: str, campos: dict[str, str], output_fn: OutputFn = print) -> None:
    output_fn(f"\n{chave}")
    if not campos:
        output_fn("  (vazio)")
        return
    for campo, valor in _ordenar_campos(campos):
        output_fn(f"  {campo:<20} {valor}")


def atualizar_console(
    *,
    db: Database | None = None,
    cliente: redis.Redis | None = None,
    output_fn: OutputFn = print,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Opção 5: recarrega os Hashes do Redis a partir do MongoDB."""
    from app.db import get_database

    db = db if db is not None else get_database()
    cliente = cliente if cliente is not None else get_redis()

    sensores, cidades = atualizar_tudo(db, cliente)
    output_fn(f"Hashes de sensor gravados: {len(sensores)}")
    for sensor_id in sensores:
        output_fn(f"  {chave_sensor(sensor_id)}")
    output_fn(f"Hashes de cidade gravados: {len(cidades)}")
    for resumo in cidades.values():
        output_fn(f"  {chave_cidade(resumo['uf'], resumo['cidade'])}")
    if not sensores and not cidades:
        output_fn("Nenhum resumo disponível. Gere os dados na opção 1.")
    return sensores, cidades


def ver_hashes_console(
    *,
    cliente: redis.Redis | None = None,
    output_fn: OutputFn = print,
) -> dict[str, dict[str, str]]:
    """Opção 6: HGETALL de todos os Hashes do projeto."""
    cliente = cliente if cliente is not None else get_redis()

    hashes = ler_hashes(cliente)
    if not hashes:
        output_fn("Nenhum Hash encontrado no Redis. Use a opção 5 para gravá-los.")
        return {}
    for chave, campos in hashes.items():
        imprimir_hash(chave, campos, output_fn)
    output_fn(f"\n{len(hashes)} Hash(es) no Redis.")
    return hashes
