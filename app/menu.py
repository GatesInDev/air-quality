"""Menu de console e despacho das seis operações do protótipo."""

from collections.abc import Callable, Mapping

Action = Callable[[], None]

OPTIONS: tuple[tuple[str, str], ...] = (
    ("1", "Gerar dados"),
    ("2", "Consultar por sensor e intervalo"),
    ("3", "Resumo por sensor"),
    ("4", "Resumo por cidade"),
    ("5", "Atualizar o Redis"),
    ("6", "Ver Hashes do Redis"),
)


def run_menu(
    actions: Mapping[str, Action],
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> None:
    """Exibe o menu até a opção de saída (0) ser selecionada."""
    while True:
        output_fn("\n=== Monitoramento da Qualidade do Ar ===")
        for number, title in OPTIONS:
            output_fn(f"{number}) {title}")
        output_fn("0) Sair")

        choice = input_fn("Escolha uma opção: ").strip()
        if choice == "0":
            output_fn("Encerrando.")
            return
        if choice not in dict(OPTIONS):
            output_fn("Opção inválida. Escolha uma opção de 0 a 6.")
            continue

        action = actions.get(choice)
        if action is None:
            output_fn("Esta operação ainda não foi integrada.")
            continue
        try:
            action()
        except Exception as exc:  # Keep the console alive on an operation error.
            output_fn(f"Falha na operação: {exc}")
