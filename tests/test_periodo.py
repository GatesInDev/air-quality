from datetime import datetime, timedelta, timezone

import pytest

from app.periodo import formatar, janela_horaria, parse_data_utc

UTC = timezone.utc


def test_janela_trunca_na_hora_e_fim_e_uma_hora_depois() -> None:
    inicio, fim = janela_horaria(datetime(2026, 10, 20, 10, 37, 12, 5, tzinfo=UTC))

    assert inicio == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    assert fim == datetime(2026, 10, 20, 11, 0, tzinfo=UTC)


def test_hora_cheia_pertence_a_propria_janela() -> None:
    hora = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    assert janela_horaria(hora) == (hora, hora + timedelta(hours=1))


def test_virada_do_dia() -> None:
    inicio, fim = janela_horaria(datetime(2026, 10, 20, 23, 55, tzinfo=UTC))
    assert fim == datetime(2026, 10, 21, 0, 0, tzinfo=UTC)
    assert inicio.day == 20


@pytest.mark.parametrize(
    "hora",
    [
        datetime(2026, 10, 20, 10, 37),  # sem fuso = UTC
        datetime(2026, 10, 20, 7, 37, tzinfo=timezone(timedelta(hours=-3))),
    ],
)
def test_converte_para_utc(hora: datetime) -> None:
    inicio, _ = janela_horaria(hora)
    assert inicio == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    assert inicio.tzinfo == UTC


def test_parse_e_formatacao() -> None:
    esperado = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    assert parse_data_utc("2026-10-20T10:00Z") == esperado
    assert parse_data_utc(" 2026-10-20 10:00 ") == esperado
    assert parse_data_utc("2026-10-20T07:00-03:00") == esperado
    assert formatar(esperado) == "2026-10-20T10:00Z"


def test_formato_iso_dos_hashes() -> None:
    from app.periodo import formatar_iso

    assert formatar_iso(datetime(2026, 10, 20, 10, 0, tzinfo=UTC)) == "2026-10-20T10:00:00Z"
    assert formatar_iso(datetime(2026, 10, 20, 7, 0, tzinfo=timezone(timedelta(hours=-3)))) == "2026-10-20T10:00:00Z"
