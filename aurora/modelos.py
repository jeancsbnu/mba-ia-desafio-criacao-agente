"""Cadeia de modelos Gemini com troca automática.

No plano gratuito do AI Studio o limite é por modelo e por projeto (20
requisições por dia em vários modelos), e o fluxo completo do assistente faz
algumas dezenas de chamadas. A cadeia tenta os modelos em ordem: se um responde
429 (cota), 503 (sobrecarga) ou 404 (modelo indisponível para a chave), a mesma
requisição vai para o próximo. As cotas dos modelos se somam. Um modelo que
demora mais que o tempo limite também passa a vez ao próximo.
"""

import asyncio
import logging
import os
import time
from collections.abc import AsyncGenerator

from google.adk.models import BaseLlm, Gemini, LlmRequest, LlmResponse
from google.genai import errors, types
from pydantic import Field

logger = logging.getLogger(__name__)

# Do melhor para o mais simples. AURORA_MODELOS troca a lista (separada por vírgula).
MODELOS_PADRAO = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
)
_CODIGOS_PARA_TROCAR = {404, 429, 500, 503, 504}
_PAUSA_APOS_FALHA = 120  # segundos sem tentar um modelo que falhou
_TEMPO_LIMITE = 60  # segundos por chamada; depois disso conta como 504 e passa ao próximo
_CHAMADA_LENTA = 20  # chamadas acima disso aparecem no log

# Compartilhado entre os agentes: se a cota de um modelo acabou para um, acabou para todos.
_pausado_ate: dict[str, float] = {}


def modelos_configurados() -> list[str]:
    valor = os.environ.get("AURORA_MODELOS", "")
    nomes = [n.strip() for n in valor.split(",") if n.strip()]
    return nomes or list(MODELOS_PADRAO)


class CadeiaDeModelos(BaseLlm):
    """BaseLlm que delega a uma lista de Gemini, passando ao próximo em caso de falha."""

    modelos: list[Gemini] = Field(default_factory=list)

    @classmethod
    def da_configuracao(cls) -> "CadeiaDeModelos":
        modelos = [
            # Uma nova tentativa no mesmo modelo cobre falhas passageiras; cota
            # esgotada passa direto para o próximo da cadeia.
            Gemini(model=nome, retry_options=types.HttpRetryOptions(attempts=2, initial_delay=1))
            for nome in modelos_configurados()
        ]
        return cls(model=modelos[0].model, modelos=modelos)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        agora = time.monotonic()
        disponiveis = [m for m in self.modelos if _pausado_ate.get(m.model, 0) <= agora]
        # Se todos estão em pausa, tenta todos mesmo assim em vez de falhar sem tentar.
        candidatos = disponiveis or self.modelos

        for posicao, modelo in enumerate(candidatos):
            requisicao = llm_request.model_copy(update={"model": modelo.model})
            inicio = time.monotonic()
            try:
                # A resposta inteira chega dentro do tempo limite e só depois é
                # repassada: o limite não pode cobrir o código do ADK que consome
                # cada resposta (gravar eventos etc.). A API não usa streaming.
                respostas = await _com_tempo_limite(modelo, requisicao, stream)
            except errors.APIError as e:
                ultimo = posicao == len(candidatos) - 1
                if ultimo or e.code not in _CODIGOS_PARA_TROCAR:
                    raise
                _pausado_ate[modelo.model] = time.monotonic() + _PAUSA_APOS_FALHA
                logger.warning(
                    "Modelo %s respondeu %s; tentando %s.", modelo.model, e.code, candidatos[posicao + 1].model
                )
                continue
            duracao = time.monotonic() - inicio
            if duracao > _CHAMADA_LENTA:
                logger.warning("Modelo %s levou %.0f s para responder.", modelo.model, duracao)
            for resposta in respostas:
                yield resposta
            return


async def _com_tempo_limite(modelo: Gemini, requisicao: LlmRequest, stream: bool) -> list[LlmResponse]:
    try:
        async with asyncio.timeout(_TEMPO_LIMITE):
            return [r async for r in modelo.generate_content_async(requisicao, stream)]
    except TimeoutError:
        mensagem = f"{modelo.model} não respondeu em {_TEMPO_LIMITE} s"
        raise errors.ServerError(
            504, {"error": {"code": 504, "message": mensagem, "status": "DEADLINE_EXCEEDED"}}
        ) from None
