"""Pontos de integração com as entregas dos demais integrantes.

Substituir cada corpo provisório pelas funções implementadas nos módulos
de domínio. O menu chama estas funções; sua interface pode permanecer sem
argumentos ou adaptar-se aqui para coletar parâmetros no console.
"""


def _pending(owner: str, work: str) -> None:
    print(f"Operação ainda não integrada: {work} (responsável: {owner}).")


def generate_data() -> None:
    _pending("João", "simulador de medições")


def query_sensor_interval() -> None:
    _pending("Lucas", "consulta por sensor e intervalo")


def summarize_sensor() -> None:
    _pending("Lucas", "agregação horária por sensor")


def summarize_city() -> None:
    _pending("Willian", "agregação horária por cidade")


def update_redis() -> None:
    _pending("Willian", "gravação dos resumos no Redis")


def show_redis_hashes() -> None:
    _pending("Willian", "leitura dos Hashes do Redis")
