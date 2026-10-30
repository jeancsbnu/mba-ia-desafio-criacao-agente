"""API HTTP do assistente, no contrato do desafio.

    uv run python -m aurora      # sobe em http://localhost:8000
"""

import logging
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from google.adk.models import BaseLlm
from google.genai import errors
from pydantic import BaseModel, Field

from aurora import banco, condominio, config
from aurora.conversa import Assistente, ConfirmacaoInexistente, Resultado, SessaoInexistente

load_dotenv(config.RAIZ / ".env")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- contrato


class NovaSessao(BaseModel):
    apartamento: str


class SessaoCriada(BaseModel):
    session_id: str


class Mensagem(BaseModel):
    texto: str = Field(min_length=1)


class RespostaDeConfirmacao(BaseModel):
    id: str
    confirmado: bool


class ConfirmacaoPendente(BaseModel):
    id: str
    acao: str
    detalhes: dict[str, Any]


class RespostaDoAssistente(BaseModel):
    resposta: str
    confirmacoes_pendentes: list[ConfirmacaoPendente]

    @classmethod
    def de(cls, resultado: Resultado) -> "RespostaDoAssistente":
        return cls(
            resposta=resultado.resposta,
            confirmacoes_pendentes=[
                ConfirmacaoPendente(id=p.id, acao=p.acao, detalhes=p.detalhes)
                for p in resultado.confirmacoes_pendentes
            ],
        )


class ReservaDoApartamento(BaseModel):
    codigo: str
    area: str
    data: str


class VisitanteDoApartamento(BaseModel):
    nome: str
    data: str


# ---------------------------------------------------------------- rotas

rotas = APIRouter()


def assistente(request: Request) -> Assistente:
    return request.app.state.assistente


@rotas.post("/sessoes", status_code=201)
async def criar_sessao(corpo: NovaSessao, a: Assistente = Depends(assistente)) -> SessaoCriada:
    if not condominio.apartamento_existe(corpo.apartamento):
        raise HTTPException(422, "Apartamento inexistente.")
    return SessaoCriada(session_id=await a.criar_sessao(corpo.apartamento))


@rotas.post("/sessoes/{session_id}/mensagens")
async def enviar_mensagem(
    session_id: str, corpo: Mensagem, a: Assistente = Depends(assistente)
) -> RespostaDoAssistente:
    return RespostaDoAssistente.de(await a.enviar_mensagem(session_id, corpo.texto))


@rotas.post("/sessoes/{session_id}/confirmacoes")
async def responder_confirmacao(
    session_id: str, corpo: RespostaDeConfirmacao, a: Assistente = Depends(assistente)
) -> RespostaDoAssistente:
    return RespostaDoAssistente.de(await a.responder_confirmacao(session_id, corpo.id, corpo.confirmado))


@rotas.get("/sessoes/{session_id}/eventos")
async def eventos(session_id: str, a: Assistente = Depends(assistente)) -> list[dict[str, Any]]:
    return [e.model_dump(mode="json", exclude_none=True) for e in await a.eventos(session_id)]


# Rotas de verificação: leem o banco direto, sem passar pelo modelo.


@rotas.get("/apartamentos/{numero}/reservas")
def reservas_do_apartamento(numero: str) -> list[ReservaDoApartamento]:
    return [ReservaDoApartamento(codigo=r.codigo, area=r.area, data=r.data) for r in condominio.listar_reservas(numero)]


@rotas.get("/apartamentos/{numero}/visitantes")
def visitantes_do_apartamento(numero: str) -> list[VisitanteDoApartamento]:
    return [VisitanteDoApartamento(nome=v.nome, data=v.data) for v in condominio.listar_visitantes(numero)]


# ---------------------------------------------------------------- aplicação


def criar_api(modelos: Mapping[str, BaseLlm] | None = None) -> FastAPI:
    """`modelos` troca o modelo de um agente pelo nome (usado em testes)."""

    @asynccontextmanager
    async def ciclo_de_vida(api: FastAPI):
        banco.inicializar()
        api.state.assistente = Assistente(modelos)
        yield
        await api.state.assistente.fechar()

    api = FastAPI(title="Residencial Aurora", lifespan=ciclo_de_vida)
    api.include_router(rotas)

    @api.exception_handler(SessaoInexistente)
    async def sessao_inexistente(request: Request, e: SessaoInexistente) -> JSONResponse:
        return JSONResponse({"detail": "Sessão não encontrada."}, status_code=404)

    @api.exception_handler(ConfirmacaoInexistente)
    async def confirmacao_inexistente(request: Request, e: ConfirmacaoInexistente) -> JSONResponse:
        return JSONResponse(
            {"detail": "Não existe confirmação pendente com esse id nesta sessão."}, status_code=409
        )

    @api.exception_handler(errors.APIError)
    async def modelo_indisponivel(request: Request, e: errors.APIError) -> JSONResponse:
        # Todos os modelos da cadeia falharam (cota, sobrecarga). A mensagem do
        # morador já ficou gravada na sessão; ele pode tentar de novo.
        logger.error("Falha do modelo: %s", e)
        return JSONResponse(
            {"detail": "O assistente está indisponível no momento. Tente novamente em instantes."},
            status_code=503,
        )

    return api


app = criar_api()
