"""O README aponta arquivos e trechos que existem (passo 15 do avaliador).

A seção Garantias cita código com link para as linhas (#Lx-Ly). Se o código
mudar de lugar, este teste aponta qual trecho do README ficou desatualizado.
"""

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
README = (RAIZ / "README.md").read_text(encoding="utf-8")

TRECHOS = re.findall(r"\[`[^`]+`\]\(([^)#]+)#L(\d+)-L(\d+)\)\n```\w*\n(.*?)```", README, re.S)
LINKS = sorted(set(re.findall(r"\]\(((?:aurora|dados|scripts|tests)/[^)#\s]+)", README)))


def test_readme_tem_as_tres_secoes():
    for secao in ("## Arquitetura", "## Garantias", "## Como rodar"):
        assert f"\n{secao}\n" in README


def test_garantias_citam_trechos():
    assert len(TRECHOS) >= 10


@pytest.mark.parametrize("caminho,inicio,fim,bloco", TRECHOS, ids=[f"{t[0]}#L{t[1]}-L{t[2]}" for t in TRECHOS])
def test_trecho_bate_com_as_linhas_do_arquivo(caminho, inicio, fim, bloco):
    linhas = (RAIZ / caminho).read_text(encoding="utf-8").splitlines()[int(inicio) - 1 : int(fim)]
    assert [l.rstrip() for l in bloco.rstrip("\n").splitlines()] == [l.rstrip() for l in linhas]


@pytest.mark.parametrize("caminho", LINKS)
def test_link_aponta_para_arquivo_existente(caminho):
    assert (RAIZ / caminho).exists()
