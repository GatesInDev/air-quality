"""Janelas horárias compartilhadas pelas consultas e agregações.

Convenção do grupo: timestamps sempre em UTC e janelas fechadas no início e
abertas no fim (``inicio_periodo <= timestamp < fim_periodo``).
"""

from datetime import datetime, timedelta, timezone

UMA_HORA = timedelta(hours=1)


def para_utc(valor: datetime) -> datetime:
    """Converte para UTC; datetime sem fuso é interpretado como UTC."""
    if valor.tzinfo is None:
        return valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc)


def janela_horaria(hora: datetime) -> tuple[datetime, datetime]:
    """Recebe qualquer instante e devolve ``(inicio_periodo, fim_periodo)`` da sua hora.

    Ex.: 10:37 UTC -> (10:00, 11:00). O fim é exclusivo.
    """
    inicio = para_utc(hora).replace(minute=0, second=0, microsecond=0)
    return inicio, inicio + UMA_HORA


def parse_data_utc(texto: str) -> datetime:
    """Lê uma data ISO 8601 digitada no console (``Z`` ou sem fuso = UTC)."""
    return para_utc(datetime.fromisoformat(texto.strip().replace("Z", "+00:00")))


def formatar(ts: datetime) -> str:
    return para_utc(ts).strftime("%Y-%m-%dT%H:%MZ")


def formatar_iso(ts: datetime) -> str:
    """Formato usado nos Hashes do enunciado: ``2026-10-20T10:00:00Z``."""
    return para_utc(ts).strftime("%Y-%m-%dT%H:%M:%SZ")
