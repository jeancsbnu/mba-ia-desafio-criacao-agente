"""Cadeia de modelos Gemini com troca automática.

No plano gratuito do AI Studio o limite é por modelo e por projeto (20
requisições por dia em vários modelos), e o fluxo completo do assistente faz
algumas dezenas de chamadas. A cadeia tenta os modelos em ordem: se um responde
429 (cota), 503 (sobrecarga) ou 404 (modelo indisponível para a chave), a mesma
requisição vai para o próximo. As cotas dos modelos se somam.
"""

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
            respondeu = False
            try:
                async for resposta in modelo.generate_content_async(requisicao, stream):
                    respondeu = True
                    yield resposta
                return
            except errors.APIError as e:
                ultimo = posicao == len(candidatos) - 1
                if respondeu or ultimo or e.code not in _CODIGOS_PARA_TROCAR:
                    raise
                _pausado_ate[modelo.model] = time.monotonic() + _PAUSA_APOS_FALHA
                logger.warning(
                    "Modelo %s respondeu %s; tentando %s.", modelo.model, e.code, candidatos[posicao + 1].model
                )
