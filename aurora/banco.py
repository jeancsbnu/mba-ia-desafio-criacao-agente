"""Banco SQLite com as reservas e os visitantes do condomínio.

Os arquivos de dados/ são só o estado inicial: o banco é criado a partir deles
na primeira execução e volta a eles com o comando de restauração.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from aurora import config

ESQUEMA = """
CREATE TABLE IF NOT EXISTS reservas (
    codigo      TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL,
    area        TEXT NOT NULL,
    data        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ativa' CHECK (status IN ('ativa', 'cancelada'))
);

-- Garantia 5: no máximo uma reserva ativa por área e data. Quem garante é o
-- próprio SQLite no INSERT, não uma conferência feita antes de gravar.
CREATE UNIQUE INDEX IF NOT EXISTS reservas_ativa_por_area_data
    ON reservas (area, data) WHERE status = 'ativa';

CREATE INDEX IF NOT EXISTS reservas_por_apartamento ON reservas (apartamento);

-- Todo código já emitido. A restauração não apaga esta tabela, então um código
-- nunca se repete, nem depois de cancelar a reserva ou restaurar os dados.
CREATE TABLE IF NOT EXISTS codigos_emitidos (codigo TEXT PRIMARY KEY);

CREATE TABLE IF NOT EXISTS visitantes (
    apartamento TEXT NOT NULL,
    nome        TEXT NOT NULL COLLATE NOCASE,
    data        TEXT NOT NULL,
    PRIMARY KEY (apartamento, nome, data)
);

-- Apartamento dono de cada sessão, gravado uma única vez na criação. A API usa
-- para achar a sessão no ADK, que a guarda com user_id = apartamento.
CREATE TABLE IF NOT EXISTS sessoes (
    session_id  TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
"""


def conectar() -> sqlite3.Connection:
    config.VAR.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: as transações são abertas explicitamente em transacao().
    con = sqlite3.connect(config.BANCO_CONDOMINIO, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


@contextmanager
def leitura() -> Iterator[sqlite3.Connection]:
    con = conectar()
    try:
        yield con
    finally:
        con.close()


@contextmanager
def transacao() -> Iterator[sqlite3.Connection]:
    """Transação de escrita.

    BEGIN IMMEDIATE pega o lock de escrita logo no início, então escritores
    concorrentes esperam um pelo outro (até o timeout) em vez de falhar.
    """
    con = conectar()
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")
    finally:
        con.close()


def inicializar() -> None:
    """Cria o esquema e, se o banco é novo, carrega o estado inicial de dados/."""
    _criar_esquema()
    with transacao() as con:
        if con.execute("SELECT 1 FROM meta WHERE chave = 'carga_inicial'").fetchone() is None:
            _carregar_estado_inicial(con)


def restaurar() -> tuple[int, int]:
    """Volta reservas e visitantes ao estado de dados/. Devolve (reservas, visitantes)."""
    _criar_esquema()
    with transacao() as con:
        return _carregar_estado_inicial(con)


def registrar_sessao(session_id: str, apartamento: str) -> None:
    with transacao() as con:
        con.execute(
            "INSERT INTO sessoes (session_id, apartamento) VALUES (?, ?)", (session_id, apartamento)
        )


def apartamento_da_sessao(session_id: str) -> str | None:
    with leitura() as con:
        linha = con.execute(
            "SELECT apartamento FROM sessoes WHERE session_id = ?", (session_id,)
        ).fetchone()
    return linha["apartamento"] if linha else None


def esquecer_sessoes() -> None:
    _criar_esquema()
    with transacao() as con:
        con.execute("DELETE FROM sessoes")


def _criar_esquema() -> None:
    with leitura() as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(ESQUEMA)


def _carregar_estado_inicial(con: sqlite3.Connection) -> tuple[int, int]:
    reservas = _ler_json("reservas.json")
    visitantes = _ler_json("visitantes.json")

    con.execute("DELETE FROM reservas")
    con.execute("DELETE FROM visitantes")
    con.executemany(
        "INSERT INTO reservas (codigo, apartamento, area, data)"
        " VALUES (:codigo, :apartamento, :area, :data)",
        reservas,
    )
    con.executemany("INSERT OR IGNORE INTO codigos_emitidos (codigo) VALUES (:codigo)", reservas)
    con.executemany(
        "INSERT INTO visitantes (apartamento, nome, data) VALUES (:apartamento, :nome, :data)",
        visitantes,
    )
    con.execute(
        "INSERT INTO meta (chave, valor) VALUES ('carga_inicial', ?)"
        " ON CONFLICT (chave) DO UPDATE SET valor = excluded.valor",
        (datetime.now(timezone.utc).isoformat(),),
    )
    return len(reservas), len(visitantes)


def _ler_json(nome: str) -> list[dict]:
    return json.loads((config.DADOS_INICIAIS / nome).read_text(encoding="utf-8"))
