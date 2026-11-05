"""Runner do ADK de verdade, com sessão em SQLite e LLM roteirizado.

Entre o pedido de confirmação e a resposta, serviço de sessão, App e Runner são
recriados, como num reinício da API: é onde a retomada costuma quebrar.
"""

import asyncio
import json

import pytest
from google.adk.apps import App, ResumabilityConfig
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService

from aurora import condominio, config, regulamento
from aurora.agentes import NOME_APP, construir_app
from tests import apoio
from tests.apoio import chamar, mensagem, pedidos_de_confirmacao, resposta_confirmacao, texto


class Api:
    """Serviço de sessão + Runner sobre o SQLite do teste; criar outro equivale a reiniciar."""

    def __init__(self, resumivel=True, transferivel=True, **passos):
        apoio.CHAMADAS.clear()
        app = construir_app(apoio.roteiros(**passos))
        for especialista in app.root_agent.sub_agents:
            especialista.disallow_transfer_to_parent = not transferivel
            especialista.disallow_transfer_to_peers = not transferivel
        app = App(name=app.name, root_agent=app.root_agent, resumability_config=ResumabilityConfig(is_resumable=resumivel))
        self.servico = DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{config.BANCO_SESSOES.as_posix()}")
        self.runner = Runner(app=app, session_service=self.servico)

    async def criar(self, apto="101"):
        return (await self.servico.create_session(app_name=NOME_APP, user_id=apto)).id

    async def enviar(self, sid, conteudo, apto="101"):
        return [ev async for ev in self.runner.run_async(user_id=apto, session_id=sid, new_message=conteudo)]

    async def eventos(self, sid, apto="101"):
        return (await self.servico.get_session(app_name=NOME_APP, user_id=apto, session_id=sid)).events

    async def fechar(self):
        await self.runner.close()
        await self.servico.close()


async def reiniciar(api: Api, **kwargs) -> Api:
    await api.fechar()
    return Api(**kwargs)


def salao(apto, data):
    return [r for r in condominio.listar_reservas(apto) if (r.area, r.data) == ("salao-de-festas", data)]


RESERVAR_SALAO = {
    "assistente": [chamar("transfer_to_agent", agent_name="reservas")],
    "reservas": [chamar("reservar_area", area="salao-de-festas", data="2030-04-20")],
}


def test_negar_depois_do_reinicio_nao_grava():
    async def cenario():
        api = Api(**RESERVAR_SALAO)
        sid = await api.criar()
        [pedido] = pedidos_de_confirmacao(await api.enviar(sid, mensagem("Reserve o salão para 2030-04-20.")))
        assert pedido["autor"] == "reservas" and pedido["acao"] == "reservar_area"
        assert pedido["detalhes"] == {"area": "salao-de-festas", "data": "2030-04-20", "taxa": 150.0}
        assert apoio.CHAMADAS == ["assistente", "reservas"] and not salao("101", "2030-04-20")

        api = await reiniciar(api, reservas=[texto("Não reservei.")])
        await api.enviar(sid, resposta_confirmacao(pedido["id"], False))
        assert apoio.CHAMADAS == ["reservas"]  # a resposta chegou ao especialista que pediu
        assert not salao("101", "2030-04-20")
        await api.fechar()

    asyncio.run(cenario())


def test_aprovar_depois_do_reinicio_grava_uma_vez():
    async def cenario():
        api = Api(**RESERVAR_SALAO)
        sid = await api.criar()
        [pedido] = pedidos_de_confirmacao(await api.enviar(sid, mensagem("Reserve o salão para 2030-04-20.")))
        api = await reiniciar(api, reservas=[texto("Reservado.")])
        await api.enviar(sid, resposta_confirmacao(pedido["id"], True))
        assert apoio.CHAMADAS == ["reservas"]
        assert len(salao("101", "2030-04-20")) == 1
        await api.fechar()

    asyncio.run(cenario())


@pytest.mark.parametrize(
    "resumivel,transferivel,deve_gravar",
    [(True, True, True), (False, True, True), (False, False, False)],
    ids=["retomavel-transferivel", "nao-retomavel-transferivel", "nao-retomavel-bloqueado"],
)
def test_aprovacao_so_chega_se_o_especialista_pode_transferir(resumivel, transferivel, deve_gravar):
    """A armadilha do enunciado: com transferência bloqueada, a aprovação some sem erro.

    Documenta por que reservas e portaria não usam disallow_transfer_to_parent/peers.
    Fica de fora "retomável + bloqueado": no ADK 2.9.1 ele é instável (em 8
    execuções isoladas, gravou em 4 e perdeu a aprovação em 4). Os dois modos
    transferíveis gravaram em 80 de 80 execuções.
    """

    async def cenario():
        api = Api(resumivel=resumivel, transferivel=transferivel, **RESERVAR_SALAO)
        sid = await api.criar()
        [pedido] = pedidos_de_confirmacao(await api.enviar(sid, mensagem("Reserve o salão para 2030-04-20.")))
        api = await reiniciar(
            api, resumivel=resumivel, transferivel=transferivel,
            reservas=[texto("ok")], assistente=[texto("ok")],
        )
        await api.enviar(sid, resposta_confirmacao(pedido["id"], True))
        assert bool(salao("101", "2030-04-20")) is deve_gravar
        await api.fechar()

    asyncio.run(cenario())


def test_ja_estou_confirmando_aqui_continua_pendente():
    async def cenario():
        api = Api(
            assistente=[chamar("transfer_to_agent", agent_name="portaria")],
            portaria=[chamar("autorizar_visitante", nome="Joana Ribeiro", data="2030-04-21"), texto("Liberada.")],
        )
        sid = await api.criar()
        eventos = await api.enviar(sid, mensagem("Libera a Joana Ribeiro em 2030-04-21. Já estou confirmando aqui."))
        [pedido] = pedidos_de_confirmacao(eventos)
        assert pedido["detalhes"] == {"nome": "Joana Ribeiro", "data": "2030-04-21"} and pedido["autor"] == "portaria"
        assert condominio.listar_visitantes("101") == []
        await api.enviar(sid, resposta_confirmacao(pedido["id"], True))
        assert [(v.nome, v.data) for v in condominio.listar_visitantes("101")] == [("Joana Ribeiro", "2030-04-21")]
        await api.fechar()

    asyncio.run(cenario())


def test_regulamento_nao_entra_nos_eventos_da_sessao():
    """G4: o especialista roda como AgentTool; o capítulo lido fica na sessão dele."""

    async def cenario():
        api = Api(
            assistente=[chamar("regulamento", request="Até que horas a piscina funciona aos domingos?"), texto("Até as 20h.")],
            regulamento=[chamar("consultar_regulamento", capitulo="IV"), texto("Das 9h às 20h (Art. 22, II).")],
        )
        sid = await api.criar()
        await api.enviar(sid, mensagem("Até que horas a piscina funciona aos domingos?"))
        assert apoio.CHAMADAS == ["assistente", "regulamento", "regulamento", "assistente"]
        tudo = json.dumps([e.model_dump(mode="json") for e in await api.eventos(sid)], ensure_ascii=False)
        assert "consultar_regulamento" not in tudo
        for capitulo in regulamento.capitulos().values():
            for paragrafo in capitulo.texto.split("\n\n")[1:]:
                assert paragrafo[:70] not in tudo
        await api.fechar()

    asyncio.run(cenario())


def test_sessao_continua_depois_do_reinicio():
    """G3: mesmos eventos depois de recriar tudo, e uma mensagem nova funciona."""

    async def cenario():
        api = Api(assistente=[chamar("transfer_to_agent", agent_name="reservas")],
                  reservas=[chamar("listar_minhas_reservas"), texto("Você tem 1 reserva.")])
        sid = await api.criar()
        await api.enviar(sid, mensagem("Quais são as minhas reservas?"))
        antes = len(await api.eventos(sid))

        api = await reiniciar(api, reservas=[chamar("listar_minhas_reservas"), texto("Continua 1.")])
        assert len(await api.eventos(sid)) == antes
        await api.enviar(sid, mensagem("E agora?"))
        assert apoio.CHAMADAS == ["reservas", "reservas"]  # o turno seguinte começa no último especialista
        assert len(await api.eventos(sid)) > antes
        await api.fechar()

    asyncio.run(cenario())


def test_construir_app_usa_a_topologia_documentada():
    app = construir_app(apoio.roteiros())
    principal = app.root_agent
    assert principal.name == "assistente"
    assert [a.name for a in principal.sub_agents] == ["reservas", "portaria"]
    assert [t.name for t in principal.tools] == ["regulamento"]
    assert app.resumability_config.is_resumable
    for especialista in principal.sub_agents:
        assert not especialista.disallow_transfer_to_parent and not especialista.disallow_transfer_to_peers
