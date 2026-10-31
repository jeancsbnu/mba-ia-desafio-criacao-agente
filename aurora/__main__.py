"""Sobe a API em http://localhost:8000:  uv run python -m aurora"""

import os

import uvicorn


def main() -> None:
    # AURORA_PORTA é opcional; o script do fluxo do avaliador usa para não disputar a 8000.
    uvicorn.run("aurora.api:app", host="127.0.0.1", port=int(os.environ.get("AURORA_PORTA") or 8000))


if __name__ == "__main__":
    main()
