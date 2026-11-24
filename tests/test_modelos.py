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
        if self.model == "lento":
            await asyncio.sleep(5)
        erros = {
            "cota": (errors.ClientError, 429),
            "cheio": (errors.ServerError, 503),
            "ruim": (errors.ClientError, 400),
            "proibido": (errors.ClientError, 403),
        }
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


def test_modelo_lento_passa_a_vez(monkeypatch):
    monkeypatch.setattr(m, "_TEMPO_LIMITE", 0.2)
    assert pedir("lento", "bom") == ["resposta de bom"]
    assert chamados == ["lento", "bom"]


def test_todos_lentos_viram_504(monkeypatch):
    monkeypatch.setattr(m, "_TEMPO_LIMITE", 0.2)
    with pytest.raises(errors.ServerError) as erro:
        pedir("lento")
    assert erro.value.code == 504


def test_lista_de_modelos(monkeypatch):
    monkeypatch.delenv("AURORA_MODELOS", raising=False)
    assert m.modelos_configurados() == list(m.MODELOS_PADRAO)
    monkeypatch.setenv("AURORA_MODELOS", " gemini-a , gemini-b,")
    assert m.modelos_configurados() == ["gemini-a", "gemini-b"]


def test_429_nao_e_repetido_no_mesmo_modelo(monkeypatch):
    """Cota esgotada passa direto para o próximo modelo; só falhas passageiras repetem no mesmo."""
    monkeypatch.setenv("AURORA_MODELOS", "gemini-a,gemini-b")
    cadeia = m.CadeiaDeModelos.da_configuracao()
    for modelo in cadeia.modelos:
        codigos = modelo.retry_options.http_status_codes
        assert 429 not in codigos and 503 in codigos


class Gravador(Gemini):
    """Guarda o histórico que recebeu, sem ir à rede."""

    recebido: list = []

    async def generate_content_async(self, llm_request, stream=False):
        Gravador.recebido = llm_request.contents
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="ok")]))


def historico_com_chamadas():
    sem_assinatura = types.Part(function_call=types.FunctionCall(name="reservar_area", args={"area": "quadra"}))
    com_assinatura = types.Part(function_call=types.FunctionCall(name="listar_minhas_reservas"), thought_signature=b"real")
    return [types.Content(role="model", parts=[sem_assinatura, com_assinatura, types.Part(text="oi")])]


def enviar_historico(nome_modelo, contents):
    cadeia = m.CadeiaDeModelos(model=nome_modelo, modelos=[Gravador(model=nome_modelo)])

    async def rodar():
        return [r async for r in cadeia.generate_content_async(LlmRequest(model=nome_modelo, contents=contents))]

    asyncio.run(rodar())
    return Gravador.recebido


def test_gemini_3_recebe_assinatura_neutra_nas_chamadas_sem_assinatura():
    """O Gemini 3 recusa (400) chamada de tool sem thought_signature, como as geradas por um 2.x."""
    original = historico_com_chamadas()
    [enviado] = enviar_historico("gemini-3.5-flash", original)
    sem, com, texto_ = enviado.parts
    assert sem.thought_signature == m._ASSINATURA_NEUTRA
    assert com.thought_signature == b"real"  # assinatura real é mantida
    assert texto_.thought_signature is None
    assert original[0].parts[0].thought_signature is None  # o histórico original não é alterado


def test_gemini_2_recebe_o_historico_como_esta():
    original = historico_com_chamadas()
    [enviado] = enviar_historico("gemini-2.5-flash", original)
    assert enviado.parts[0].thought_signature is None


def test_403_troca_de_modelo():
    """403: o modelo não está liberado para esta chave; o próximo da cadeia pode estar."""
    assert pedir("proibido", "bom") == ["resposta de bom"]
    assert chamados == ["proibido", "bom"]
