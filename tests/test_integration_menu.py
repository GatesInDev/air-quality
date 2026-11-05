"""Contrato do menu completo e recuperação após falha de uma operação."""
import pytest
from app import features, main
from app.menu import run_menu


@pytest.mark.parametrize(('wrapper', 'module', 'target'), [
    ('generate_data', 'app.simulador', 'executar_console'),
    ('query_sensor_interval', 'app.consultas', 'consultar_console'),
    ('summarize_sensor', 'app.consultas', 'resumo_sensor_console'),
    ('summarize_city', 'app.cidades', 'resumo_cidade_console'),
    ('update_redis', 'app.redis_store', 'atualizar_console'),
    ('show_redis_hashes', 'app.redis_store', 'ver_hashes_console'),
])
def test_feature_delega_ao_console_correto(monkeypatch, wrapper, module, target):
    calls = []
    monkeypatch.setattr(f'{module}.{target}', lambda: calls.append(target))
    getattr(features, wrapper)()
    assert calls == [target]


def test_main_liga_as_seis_opcoes(monkeypatch):
    actions = {}
    monkeypatch.setattr(main, 'run_menu', lambda mapping: actions.update(mapping))
    main.main()
    assert list(actions.values()) == [
        features.generate_data, features.query_sensor_interval,
        features.summarize_sensor, features.summarize_city,
        features.update_redis, features.show_redis_hashes,
    ]
    assert list(actions) == ['1', '2', '3', '4', '5', '6']


def test_menu_continua_apos_falha():
    choices = iter(['5', '6', '0'])
    output, calls = [], []
    def fail():
        raise ConnectionError('Redis indisponível')
    run_menu({'5': fail, '6': lambda: calls.append('leitura')},
             input_fn=lambda _: next(choices), output_fn=output.append)
    assert 'Falha na operação: Redis indisponível' in output
    assert calls == ['leitura']
    assert output[-1] == 'Encerrando.'
