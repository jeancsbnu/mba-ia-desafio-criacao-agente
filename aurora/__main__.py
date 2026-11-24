"""Sobe a API em http://localhost:8000:  uv run python -m aurora"""

import os

import uvicorn
from dotenv import load_dotenv

from aurora import config


def main() -> None:
    # Carrega o .env antes de ler host e porta; variáveis já definidas no ambiente têm prioridade.
    load_dotenv(config.RAIZ / ".env")
    # Ambos opcionais. AURORA_PORTA: o script do fluxo usa para não disputar a 8000.
    # AURORA_HOST: 0.0.0.0 para acessar de fora da máquina (WSL, contêiner).
    uvicorn.run(
        "aurora.api:app",
        host=os.environ.get("AURORA_HOST") or "127.0.0.1",
        port=int(os.environ.get("AURORA_PORTA") or 8000),
    )


if __name__ == "__main__":
    main()
