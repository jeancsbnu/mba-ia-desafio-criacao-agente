"""Sobe a API em http://localhost:8000:  uv run python -m aurora"""

import uvicorn


def main() -> None:
    uvicorn.run("aurora.api:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
