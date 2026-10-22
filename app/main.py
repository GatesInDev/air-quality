"""Ponto de entrada do programa integrado."""

from app.features import (
    generate_data,
    query_sensor_interval,
    show_redis_hashes,
    summarize_city,
    summarize_sensor,
    update_redis,
)
from app.menu import run_menu


def main() -> None:
    actions = {
        "1": generate_data,
        "2": query_sensor_interval,
        "3": summarize_sensor,
        "4": summarize_city,
        "5": update_redis,
        "6": show_redis_hashes,
    }
    run_menu(actions)


if __name__ == "__main__":
    main()
