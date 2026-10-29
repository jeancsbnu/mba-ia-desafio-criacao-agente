"""Catálogo do condomínio e operações sobre reservas e visitantes.

As funções que leem ou gravam reservas e visitantes recebem o apartamento como
argumento e filtram por ele no SQL. Quem chama passa o apartamento da sessão
(tools) ou o da URL (rotas de verificação), nunca um valor escolhido pelo modelo.
"""

import json
import re
import secrets
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import date
from functools import cache

from aurora import banco, config

_FORMATO_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")
# Sem 0/O e 1/I, para o código não ser lido errado.
_ALFABETO_CODIGO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass(frozen=True)
class Area:
    id: str
    nome: str
    taxa: float

    @property
    def gera_cobranca(self) -> bool:
        return self.taxa > 0


@dataclass(frozen=True)
class Reserva:
    codigo: str
    area: str
    data: str


@dataclass(frozen=True)
class Visitante:
    nome: str
    data: str


# ---------------------------------------------------------------- catálogo


@cache
def areas() -> dict[str, Area]:
    itens = json.loads((config.DADOS_INICIAIS / "areas.json").read_text(encoding="utf-8"))
    return {a["id"]: Area(a["id"], a["nome"], float(a["taxa"])) for a in itens}


@cache
def apartamentos() -> dict[str, str]:
    """Número do apartamento -> nome do morador."""
    itens = json.loads((config.DADOS_INICIAIS / "apartamentos.json").read_text(encoding="utf-8"))
    return {a["numero"]: a["morador"] for a in itens}


def apartamento_existe(numero: str) -> bool:
    return numero in apartamentos()


def resolver_area(texto: str) -> Area | None:
    """Encontra a área pelo id ou pelo nome, sem diferenciar maiúsculas, acentos e hífens."""
    chave = _normalizar(texto)
    for area in areas().values():
        if chave in (_normalizar(area.id), _normalizar(area.nome)):
            return area
    return None


def validar_data(texto: str) -> str:
    """Devolve a data no formato AAAA-MM-DD ou levanta ValueError."""
    texto = texto.strip()
    if _FORMATO_DATA.fullmatch(texto):
        try:
            date.fromisoformat(texto)
            return texto
        except ValueError:
            pass
    raise ValueError(f"Data inválida: {texto!r}. Use o formato AAAA-MM-DD.")


# ---------------------------------------------------------------- reservas


def listar_reservas(apartamento: str) -> list[Reserva]:
    """Reservas ativas do apartamento."""
    with banco.leitura() as con:
        linhas = con.execute(
            "SELECT codigo, area, data FROM reservas"
            " WHERE apartamento = ? AND status = 'ativa' ORDER BY data, area",
            (apartamento,),
        ).fetchall()
    return [Reserva(**linha) for linha in linhas]


def data_livre(area_id: str, data: str) -> bool:
    """Diz só se a área está livre na data, sem revelar de quem é a reserva."""
    _area(area_id)
    data = validar_data(data)
    with banco.leitura() as con:
        ocupada = con.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND status = 'ativa'",
            (area_id, data),
        ).fetchone()
    return ocupada is None


def criar_reserva(apartamento: str, area_id: str, data: str) -> Reserva | None:
    """Grava a reserva, ou devolve None se a área já está reservada na data.

    A exclusividade vem do índice único reservas_ativa_por_area_data: se outra
    reserva entrou entre uma conferência e esta gravação, o INSERT falha aqui.
    """
    _area(area_id)
    data = validar_data(data)
    with banco.transacao() as con:
        codigo = _novo_codigo(con)
        try:
            con.execute(
                "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (?, ?, ?, ?)",
                (codigo, apartamento, area_id, data),
            )
        except sqlite3.IntegrityError:
            return None
        con.execute("INSERT INTO codigos_emitidos (codigo) VALUES (?)", (codigo,))
    return Reserva(codigo, area_id, data)


def cancelar_reserva(
    apartamento: str,
    *,
    codigo: str | None = None,
    area_id: str | None = None,
    data: str | None = None,
) -> Reserva | None:
    """Cancela uma reserva ativa do apartamento, pelo código ou pela área e data.

    Devolve None quando o apartamento não tem essa reserva, exista ela em outro
    apartamento ou não, para não revelar reservas alheias.
    """
    if codigo:
        filtro, parametros = "codigo = ?", (codigo.strip().upper(),)
    elif area_id and data:
        _area(area_id)
        filtro, parametros = "area = ? AND data = ?", (area_id, validar_data(data))
    else:
        raise ValueError("Informe o código ou a área e a data da reserva.")

    with banco.transacao() as con:
        linha = con.execute(
            "SELECT codigo, area, data FROM reservas"
            f" WHERE apartamento = ? AND status = 'ativa' AND {filtro}",
            (apartamento, *parametros),
        ).fetchone()
        if linha is None:
            return None
        con.execute("UPDATE reservas SET status = 'cancelada' WHERE codigo = ?", (linha["codigo"],))
    return Reserva(**linha)


# ---------------------------------------------------------------- visitantes


def listar_visitantes(apartamento: str) -> list[Visitante]:
    with banco.leitura() as con:
        linhas = con.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, nome",
            (apartamento,),
        ).fetchall()
    return [Visitante(**linha) for linha in linhas]


def autorizar_visitante(apartamento: str, nome: str, data: str) -> bool:
    """Registra a autorização de visita. Devolve False se ela já existia."""
    nome = " ".join(nome.split())
    if not nome:
        raise ValueError("Informe o nome do visitante.")
    data = validar_data(data)
    with banco.transacao() as con:
        cursor = con.execute(
            "INSERT OR IGNORE INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
            (apartamento, nome, data),
        )
        return cursor.rowcount == 1


# ---------------------------------------------------------------- internos


def _area(area_id: str) -> Area:
    area = areas().get(area_id)
    if area is None:
        raise ValueError(f"Área desconhecida: {area_id!r}.")
    return area


def _novo_codigo(con: sqlite3.Connection) -> str:
    # Roda dentro de transacao(): com o lock de escrita, ninguém emite o mesmo código no meio.
    while True:
        codigo = "RSV-" + "".join(secrets.choice(_ALFABETO_CODIGO) for _ in range(6))
        if con.execute("SELECT 1 FROM codigos_emitidos WHERE codigo = ?", (codigo,)).fetchone() is None:
            return codigo


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().replace("-", " ").replace("_", " ").split())
