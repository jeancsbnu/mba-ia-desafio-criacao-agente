"""Conversa com o assistente sobre o Runner do ADK: sessões, mensagens e confirmações.

Garantia 1: a única forma de aprovar uma ação é responder_confirmacao(), que só
aceita o id de uma confirmação pendente na própria sessão e o devolve ao Runner
como FunctionResponse. O texto do morador sempre entra como texto, nunca como
FunctionResponse. Uma confirmação já respondida deixa de estar pendente (a
resposta fica gravada nos eventos), então reenviá-la é recusado: o ADK, sozinho,
só descarta respostas repetidas dentro do mesmo turno.

Garantia 3: sessões e eventos ficam no DatabaseSessionService (SQLite), e as
pendências são calculadas a partir dos eventos gravados, não de memória.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from google.adk.events import Event
from google.adk.flows.llm_flows.functions import REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
from google.adk.models import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService, Session
from google.genai import errors, types

from aurora import banco, config
from aurora.agentes import NOME_APP, construir_app

logger = logging.getLogger(__name__)

# Tools internas do ADK: a resposta delas não serve de mensagem para o morador.
_TOOLS_INTERNAS = {"transfer_to_agent", REQUEST_CONFIRMATION_FUNCTION_CALL_NAME}


class SessaoInexistente(Exception):
    pass


class ConfirmacaoInexistente(Exception):
    pass


@dataclass(frozen=True)
class Pendencia:
    id: str
    acao: str
    detalhes: dict[str, Any]
    descricao: str


@dataclass(frozen=True)
class Resultado:
    resposta: str
    confirmacoes_pendentes: list[Pendencia]


class Assistente:
    def __init__(self, modelos: Mapping[str, BaseLlm] | None = None):
        url = f"sqlite+aiosqlite:///{config.BANCO_SESSOES.as_posix()}"
        self.sessoes = DatabaseSessionService(db_url=url, connect_args={"timeout": 30})  # espera o lock do SQLite
        self.runner = Runner(app=construir_app(modelos), session_service=self.sessoes)
        # Uma requisição por vez em cada sessão; sessões diferentes rodam em paralelo.
        self._travas: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def fechar(self) -> None:
        await self.runner.close()
        # O Runner só descarrega o serviço de sessões; as conexões com o SQLite fecham aqui.
        await self.sessoes.close()

    async def criar_sessao(self, apartamento: str) -> str:
        # O apartamento vira o user_id da sessão: é dele que as tools leem (Garantia 2).
        sessao = await self.sessoes.create_session(app_name=NOME_APP, user_id=apartamento)
        banco.registrar_sessao(sessao.id, apartamento)
        return sessao.id

    async def eventos(self, session_id: str) -> list[Event]:
        return (await self._sessao(session_id)).events

    async def enviar_mensagem(self, session_id: str, texto: str) -> Resultado:
        async with self._trava(session_id):
            sessao = await self._sessao(session_id)
            conteudo = types.Content(role="user", parts=[types.Part(text=texto)])
            return await self._rodar(sessao, conteudo)

    async def responder_confirmacao(self, session_id: str, confirmacao_id: str, confirmado: bool) -> Resultado:
        async with self._trava(session_id):
            sessao = await self._sessao(session_id)
            if confirmacao_id not in {p.id for p in confirmacoes_pendentes(sessao.events)}:
                raise ConfirmacaoInexistente(confirmacao_id)
            resposta = types.FunctionResponse(
                name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
                id=confirmacao_id,
                response={"confirmed": confirmado},
            )
            conteudo = types.Content(role="user", parts=[types.Part(function_response=resposta)])
            return await self._rodar(sessao, conteudo)

    def _trava(self, session_id: str) -> asyncio.Lock:
        # Só sessões que existem ganham trava: um id inventado dá 404 sem ocupar memória.
        if banco.apartamento_da_sessao(session_id) is None:
            raise SessaoInexistente(session_id)
        return self._travas[session_id]

    async def _sessao(self, session_id: str) -> Session:
        apartamento = banco.apartamento_da_sessao(session_id)
        sessao = None
        if apartamento is not None:
            sessao = await self.sessoes.get_session(
                app_name=NOME_APP, user_id=apartamento, session_id=session_id
            )
        if sessao is None:
            raise SessaoInexistente(session_id)
        return sessao

    async def _rodar(self, sessao: Session, conteudo: types.Content) -> Resultado:
        eventos_antes = len(sessao.events)
        textos = []
        try:
            async for evento in self.runner.run_async(
                user_id=sessao.user_id, session_id=sessao.id, new_message=conteudo
            ):
                textos.extend(_textos(evento))
        except errors.APIError:
            # O modelo falhou depois de uma tool já ter executado (ex.: a aprovação
            # gravou a reserva e só o resumo final não veio). A ação vale, então a
            # resposta sai da própria tool, em vez de um 503 seguido de 409 no reenvio.
            novos = (await self._sessao(sessao.id)).events[eventos_antes:]
            mensagem = _mensagem_da_ultima_tool(novos)
            if mensagem is None:
                raise
            logger.warning("O modelo falhou depois de uma tool executar; respondendo com a mensagem da tool.")
            textos.append(mensagem)
        eventos = (await self._sessao(sessao.id)).events
        pendentes = confirmacoes_pendentes(eventos)

        resposta = "\n\n".join(textos).strip()
        if not resposta and pendentes:
            # A execução parou no pedido de confirmação, sem texto do modelo.
            resposta = "Aguardando sua aprovação no aplicativo: " + " ".join(p.descricao for p in pendentes)
        elif not resposta:
            # O modelo terminou sem texto (acontece às vezes depois de uma tool):
            # a mensagem da própria tool vale como resposta.
            resposta = _mensagem_da_ultima_tool(eventos[eventos_antes:]) or ""
        return Resultado(resposta, pendentes)


def confirmacoes_pendentes(eventos: list[Event]) -> list[Pendencia]:
    """Pedidos de confirmação do ADK que ainda não têm resposta do morador."""
    respondidas = {
        resposta.id
        for evento in eventos
        if evento.author == "user"
        for resposta in evento.get_function_responses()
        if resposta.name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
    }
    pendentes = []
    for evento in eventos:
        for chamada in evento.get_function_calls():
            # Pedidos gerados pelo ADK são marcados como long-running; uma chamada
            # com esse nome inventada pelo modelo não seria.
            if (
                chamada.name != REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
                or chamada.id in respondidas
                or chamada.id not in (evento.long_running_tool_ids or set())
            ):
                continue
            args = chamada.args or {}
            original = args.get("originalFunctionCall") or {}
            pedido = args.get("toolConfirmation") or {}
            pendentes.append(
                Pendencia(
                    id=chamada.id,
                    acao=original.get("name", ""),
                    detalhes=pedido.get("payload") or original.get("args") or {},
                    descricao=pedido.get("hint", ""),
                )
            )
    return pendentes


def _mensagem_da_ultima_tool(eventos: list[Event]) -> str | None:
    """Mensagem da última tool do projeto que respondeu nestes eventos, se houver."""
    for evento in reversed(eventos):
        if evento.author == "user":
            continue
        for resposta in reversed(evento.get_function_responses()):
            dados = resposta.response
            if resposta.name in _TOOLS_INTERNAS or not isinstance(dados, dict):
                continue
            mensagem = dados.get("mensagem") or dados.get("result")  # "result": resposta de um AgentTool
            if isinstance(mensagem, str) and mensagem.strip():
                return mensagem.strip()
    return None


def _textos(evento: Event) -> list[str]:
    if evento.author == "user" or evento.partial or not evento.content or not evento.content.parts:
        return []
    return [p.text.strip() for p in evento.content.parts if p.text and not p.thought and p.text.strip()]
