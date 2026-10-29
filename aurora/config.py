"""Caminhos usados pela aplicação."""

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Estado inicial do condomínio (somente leitura).
DADOS_INICIAIS = RAIZ / "dados"

# Onde ficam os bancos gravados em execução. AURORA_VAR_DIR é opcional.
VAR = Path(os.environ.get("AURORA_VAR_DIR") or RAIZ / "var")
BANCO_CONDOMINIO = VAR / "condominio.db"
BANCO_SESSOES = VAR / "sessoes.db"
