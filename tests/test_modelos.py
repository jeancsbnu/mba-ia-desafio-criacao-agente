"""Cadeia de modelos: troca de modelo em 429/503/404, pausa e configuração."""

import asyncio

import pytest
from google.adk.models import Gemini, LlmRequest, LlmResponse
from google.genai import errors, types

from aurora import modelos as m

chamados: list[str] = []


class Falso(Gemini):
    """Gemini que não vai à rede: o nome do modelo diz como ele responde."""

    async def generate_content_async(self, llm_request, stream=False):
        chamados.append(llm_request.model)
        erros = {"cota": (errors.ClientError, 429), "cheio": (errors.ServerError, 503), "ruim": (errors.ClientError, 400)}
        if self.model in erros:
            classe, codigo = erros[self.model]
            raise classe(codigo, {"error": {"code": codigo, "message": self.model, "status": "X"}})
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=f"resposta de {self.model}")]))


def pedir(*nomes: str) -> list[str]:
    cadeia = m.CadeiaDeModelos(model=nomes[0], modelos=[Falso(model=n) for n in nomes])

    async def rodar():
        return [r.content.parts[0].text async for r in cadeia.generate_content_async(LlmRequest(model=nomes[0]))]

    return asyncio.run(rodar())


@pytest.fixture(autouse=True)
def limpar():
    chamados.clear()


def test_troca_de_modelo_em_429_e_503():
    assert pedir("cota", "cheio", "bom") == ["resposta de bom"]
    assert chamados == ["cota", "cheio", "bom"]


def test_modelo_que_falhou_fica_em_pausa():
    pedir("cota", "bom")
    chamados.clear()
    assert pedir("cota", "bom") == ["resposta de bom"]
    assert chamados == ["bom"]


def test_erro_que_nao_e_de_cota_propaga():
    with pytest.raises(errors.ClientError) as erro:
        pedir("ruim", "bom")
    assert erro.value.code == 400 and chamados == ["ruim"]


def test_todos_falham_propaga_o_ultimo():
    with pytest.raises(errors.ServerError):
        pedir("cota", "cheio")


def test_lista_de_modelos(monkeypatch):
    monkeypatch.delenv("AURORA_MODELOS", raising=False)
    assert m.modelos_configurados() == list(m.MODELOS_PADRAO)
    monkeypatch.setenv("AURORA_MODELOS", " gemini-a , gemini-b,")
    assert m.modelos_configurados() == ["gemini-a", "gemini-b"]
