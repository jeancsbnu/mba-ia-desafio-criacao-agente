"""Comando de restauração: volta reservas e visitantes ao estado de dados/.

    uv run python -m aurora.restaurar             # reservas e visitantes; mantém as sessões
    uv run python -m aurora.restaurar --sessoes   # também apaga as sessões (pare a API antes)
"""

import argparse

from aurora import banco, config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restaura reservas e visitantes ao estado inicial de dados/."
    )
    parser.add_argument(
        "--sessoes",
        action="store_true",
        help="também apaga as sessões gravadas (pare a API antes)",
    )
    args = parser.parse_args()

    reservas, visitantes = banco.restaurar()
    print(f"Restaurado de {config.DADOS_INICIAIS}: {reservas} reservas e {visitantes} visitantes.")

    if args.sessoes:
        _apagar_sessoes()


def _apagar_sessoes() -> None:
    arquivos = [
        config.BANCO_SESSOES.with_name(config.BANCO_SESSOES.name + sufixo)
        for sufixo in ("", "-wal", "-shm", "-journal")
    ]
    try:
        for arquivo in arquivos:
            arquivo.unlink(missing_ok=True)
    except PermissionError:
        raise SystemExit(f"Não foi possível apagar {config.BANCO_SESSOES}: pare a API e rode de novo.")
    print(f"Sessões apagadas ({config.BANCO_SESSOES}).")


if __name__ == "__main__":
    main()
