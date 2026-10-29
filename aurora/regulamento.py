"""Regulamento interno separado por capítulo.

O texto nunca vai inteiro para o modelo: a tool devolve um capítulo por vez,
e o agente principal não recebe nada dele nas instruções (Garantia 4).
"""

import re
from dataclasses import dataclass
from functools import cache

from aurora import config

_CABECALHO = re.compile(r"## Capítulo ([IVXLC]+): (.+)")
_ROMANOS = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


@dataclass(frozen=True)
class Capitulo:
    numero: str  # em romanos, como no arquivo
    titulo: str
    texto: str  # o capítulo inteiro, com o cabeçalho


@cache
def capitulos() -> dict[str, Capitulo]:
    texto = (config.DADOS_INICIAIS / "regulamento.md").read_text(encoding="utf-8")
    resultado = {}
    for parte in re.split(r"(?m)^(?=## Capítulo )", texto)[1:]:
        cabecalho = _CABECALHO.match(parte)
        if cabecalho:
            numero, titulo = cabecalho.groups()
            resultado[numero] = Capitulo(numero, titulo.strip(), parte.strip())
    return resultado


def indice() -> str:
    """Uma linha por capítulo, para as instruções do especialista em regulamento."""
    return "\n".join(f"- Capítulo {c.numero}: {c.titulo}" for c in capitulos().values())


def capitulo(referencia: str) -> Capitulo | None:
    """Encontra o capítulo por número romano ou arábico ("IV", "4", "Capítulo IV")."""
    ref = referencia.strip().upper().removeprefix("CAPÍTULO").removeprefix("CAPITULO").strip()
    if ref.isdigit():
        ref = next((n for n in capitulos() if _romano_para_int(n) == int(ref)), "")
    return capitulos().get(ref)


def _romano_para_int(romano: str) -> int:
    total = 0
    for atual, seguinte in zip(romano, romano[1:] + " "):
        valor = _ROMANOS[atual]
        total += -valor if valor < _ROMANOS.get(seguinte, 0) else valor
    return total
