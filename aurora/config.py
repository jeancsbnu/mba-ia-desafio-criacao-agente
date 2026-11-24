"""Caminhos usados pela aplicação e carregamento do .env."""

import os
from pathlib import Path

from dotenv import dotenv_values

RAIZ = Path(__file__).resolve().parent.parent

# Estado inicial do condomínio (somente leitura).
DADOS_INICIAIS = RAIZ / "dados"

# Onde ficam os bancos gravados em execução. AURORA_VAR_DIR é opcional.
VAR = Path(os.environ.get("AURORA_VAR_DIR") or RAIZ / "var")
BANCO_CONDOMINIO = VAR / "condominio.db"
BANCO_SESSOES = VAR / "sessoes.db"


def carregar_env(arquivo: Path = RAIZ / ".env") -> None:
    """Carrega o .env no ambiente.

    Uma variável já definida no terminal tem prioridade (o script do fluxo passa
    AURORA_PORTA assim), mas uma variável vazia não conta: um `GOOGLE_API_KEY=`
    exportado no shell não pode anular a chave preenchida no .env.
    """
    for nome, valor in dotenv_values(arquivo).items():
        if valor and not os.environ.get(nome):
            os.environ[nome] = valor
