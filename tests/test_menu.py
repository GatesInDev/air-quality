from app.menu import run_menu


def test_menu_dispatches_selected_operation_and_exits() -> None:
    selected: list[str] = []
    choices = iter(["3", "0"])
    output: list[str] = []

    run_menu(
        {"3": lambda: selected.append("sensor-summary")},
        input_fn=lambda _prompt: next(choices),
        output_fn=output.append,
    )

    assert selected == ["sensor-summary"]
    assert any("3) Resumo por sensor" in line for line in output)
    assert output[-1] == "Encerrando."


def test_menu_reports_invalid_selection_and_continues() -> None:
    choices = iter(["x", "0"])
    output: list[str] = []

    run_menu({}, input_fn=lambda _prompt: next(choices), output_fn=output.append)

    assert "Opção inválida. Escolha uma opção de 0 a 6." in output


def test_menu_reports_unintegrated_action() -> None:
    choices = iter(["1", "0"])
    output: list[str] = []

    run_menu({}, input_fn=lambda _prompt: next(choices), output_fn=output.append)

    assert "Esta operação ainda não foi integrada." in output
