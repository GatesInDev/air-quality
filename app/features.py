"""Pontos de integração entre o menu e os módulos de domínio.

Cada opção do console chama uma função sem argumentos daqui, que delega ao
módulo responsável. Os imports ficam dentro das funções para que abrir o menu
não exija conexão com MongoDB nem Redis.
"""


def generate_data() -> None:
    from app.simulador import executar_console

    executar_console()


def query_sensor_interval() -> None:
    from app.consultas import consultar_console

    consultar_console()


def summarize_sensor() -> None:
    from app.consultas import resumo_sensor_console

    resumo_sensor_console()


def summarize_city() -> None:
    from app.cidades import resumo_cidade_console

    resumo_cidade_console()


def update_redis() -> None:
    from app.redis_store import atualizar_console

    atualizar_console()


def show_redis_hashes() -> None:
    from app.redis_store import ver_hashes_console

    ver_hashes_console()
