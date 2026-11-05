"""Apoio dos testes: LLM roteirizado e atalhos para montar mensagens e confirmações.

Nenhum teste chama o Gemini: todos os agentes recebem um Roteiro, que devolve
respostas combinadas na ordem e falha se for chamado fora dela.
"""

from google.adk.events import Event
from google.adk.models import BaseLlm, LlmResponse
from google.genai import errors, types
from pydantic import Field

AGENTES = ("assistente", "reservas", "portaria", "regulamento")

# Ordem em que os LLMs foram chamados, para conferir o roteamento entre agentes.
CHAMADAS: list[str] = []


class Roteiro(BaseLlm):
    passos: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        assert self.passos, f"LLM de {self.model} chamado fora do roteiro"
        CHAMADAS.append(self.model)
        yield LlmResponse(content=types.Content(role="model", parts=self.passos.pop(0)))


class SemCota(BaseLlm):
    """Modelo que sempre responde 429, como quando todas as cotas acabaram."""

    async def generate_content_async(self, llm_request, stream=False):
        raise errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
        yield  # pragma: no cover


def roteiros(**passos: list) -> dict[str, BaseLlm]:
    """Um Roteiro para cada agente; agente sem passos falha se for chamado."""
    return {nome: Roteiro(model=nome, passos=list(passos.get(nome, []))) for nome in AGENTES}


def chamar(_funcao: str, **args) -> list[types.Part]:
    return [types.Part(function_call=types.FunctionCall(name=_funcao, args=args))]


def texto(t: str) -> list[types.Part]:
    return [types.Part(text=t)]


def mensagem(t: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=t)])


def resposta_confirmacao(conf_id: str, aprovado: bool) -> types.Content:
    resposta = types.FunctionResponse(name="adk_request_confirmation", id=conf_id, response={"confirmed": aprovado})
    return types.Content(role="user", parts=[types.Part(function_response=resposta)])


def pedidos_de_confirmacao(eventos: list[Event]) -> list[dict]:
    return [
        {
            "id": fc.id,
            "acao": fc.args["originalFunctionCall"]["name"],
            "detalhes": fc.args["toolConfirmation"].get("payload"),
            "autor": ev.author,
        }
        for ev in eventos
        for fc in ev.get_function_calls()
        if fc.name == "adk_request_confirmation"
    ]
