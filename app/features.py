"""Pontos de integração com as entregas dos demais integrantes.

Substituir cada corpo provisório pelas funções implementadas nos módulos
de domínio. O menu chama estas funções; sua interface pode permanecer sem
argumentos ou adaptar-se aqui para coletar parâmetros no console.
"""


def _pending(owner: str, work: str) -> None:
    print(f"Operação ainda não integrada: {work} (responsável: {owner}).")


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
    _pending("Willian", "agregação horária por cidade")


def update_redis() -> None:
    _pending("Willian", "gravação dos resumos no Redis")


def show_redis_hashes() -> None:
    _pending("Willian", "leitura dos Hashes do Redis")
