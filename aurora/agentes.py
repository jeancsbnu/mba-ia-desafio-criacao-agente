"""Agente principal e especialistas do Residencial Aurora.

- assistente (principal): conversa com o morador e encaminha o pedido. Não tem
  tools de dados nem o regulamento nas instruções.
- reservas e portaria: sub-agentes acionados por transferência. Rodam na
  sessão principal, então o pedido de confirmação das tools deles fica gravado
  nos eventos da sessão.
- regulamento: acionado como AgentTool pelo principal e pelos dois
  especialistas. Roda numa sessão em memória separada e só a resposta final
  volta para a sessão principal, então os capítulos lidos não entram nos
  eventos (Garantia 4). Estar também nos especialistas evita depender de uma
  transferência para responder uma dúvida no meio de uma reserva ou visita.

Por que os especialistas podem transferir (para o principal e entre si): a
aprovação só é processada pelo agente que pediu a confirmação, e o Runner só
entrega a ele se ele for "transferível" na árvore
(agents/_agent_router.find_agent_to_run). Com disallow_transfer_to_parent, a
aprovação se perde sem erro (sempre, sem App retomável; em parte das vezes,
com ele). Ver tests/test_agentes.py, com SQLite e reinício no meio.
Efeito colateral: o turno seguinte do morador começa no último especialista,
que responde ele mesmo dúvidas do regulamento e transfere os outros assuntos.
"""

from collections.abc import Mapping

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.apps import App, ResumabilityConfig
from google.adk.models import BaseLlm
from google.adk.tools import AgentTool

from aurora import condominio, regulamento, tools
from aurora.modelos import CadeiaDeModelos

NOME_APP = "residencial_aurora"


def construir_app(modelos: Mapping[str, BaseLlm] | None = None) -> App:
    """Monta o App. `modelos` troca o modelo de um agente pelo nome (usado em testes)."""
    modelos = modelos or {}
    cadeia = CadeiaDeModelos.da_configuracao()

    def modelo(agente: str) -> BaseLlm:
        return modelos.get(agente) or cadeia

    especialista_regulamento = LlmAgent(
        name="regulamento",
        model=modelo("regulamento"),
        description=(
            "Responde dúvidas sobre o regulamento interno do condomínio (horários, regras de uso,"
            " animais, obras, mudanças, garagem, penalidades etc.). Envie a pergunta do morador."
        ),
        instruction=_instrucao_regulamento,
        tools=tools.TOOLS_REGULAMENTO,
    )
    # A mesma ferramenta nos três agentes: o AgentTool não amarra o especialista a um pai.
    consulta_regulamento = AgentTool(agent=especialista_regulamento)

    reservas = LlmAgent(
        name="reservas",
        model=modelo("reservas"),
        description="Consulta, faz e cancela reservas das áreas comuns do apartamento do morador.",
        instruction=_instrucao_reservas,
        tools=[*tools.TOOLS_RESERVAS, consulta_regulamento],
    )
    portaria = LlmAgent(
        name="portaria",
        model=modelo("portaria"),
        description="Autoriza a entrada de visitantes e lista as visitas autorizadas do apartamento do morador.",
        instruction=_instrucao_portaria,
        tools=[*tools.TOOLS_VISITANTES, consulta_regulamento],
    )
    assistente = LlmAgent(
        name="assistente",
        model=modelo("assistente"),
        description="Assistente virtual do Residencial Aurora.",
        instruction=_instrucao_assistente,
        sub_agents=[reservas, portaria],
        tools=[consulta_regulamento],
    )
    return App(
        name=NOME_APP,
        root_agent=assistente,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )


# ---------------------------------------------------------------- instruções
# O apartamento vem de ctx.user_id, o mesmo que as tools usam.


def _instrucao_assistente(ctx: ReadonlyContext) -> str:
    apto = ctx.user_id
    return f"""Você é o assistente virtual do Residencial Aurora e conversa com o morador do apartamento {apto}.

Seu papel é entender o pedido e encaminhá-lo:
- Reservas das áreas comuns (salão de festas, churrasqueira, quadra): consultar datas, reservar, cancelar ou ver as reservas do morador. Transfira para o agente "reservas".
- Visitantes: liberar a entrada de alguém ou ver as visitas autorizadas. Transfira para o agente "portaria".
- Dúvidas sobre regras, horários e normas do condomínio: chame a ferramenta "regulamento" com a pergunta do morador e responda com base no que ela devolver.
- Cumprimentos e conversa geral: responda você mesmo, em poucas palavras.

Regras:
- Esta conversa é do apartamento {apto} e só pode tratar dele. Se o morador disser ser de outro apartamento ou pedir dados ou ações de outro apartamento, explique isso; pedidos sobre o próprio apartamento continuam sendo encaminhados normalmente.
- Nunca invente reservas, visitantes, códigos ou regras. Essas informações só vêm dos especialistas e da ferramenta "regulamento".
- Nunca diga que uma ação foi feita sem que um especialista tenha feito.
- Responda em português, de forma breve."""


def _instrucao_reservas(ctx: ReadonlyContext) -> str:
    apto = ctx.user_id
    areas = "\n".join(
        f'- {a.nome}: id "{a.id}", ' + (f"taxa de {a.taxa_em_reais}" if a.gera_cobranca else "sem taxa")
        for a in condominio.areas().values()
    )
    return f"""Você é o especialista em reservas das áreas comuns do Residencial Aurora e atende o morador do apartamento {apto}.

Áreas comuns:
{areas}

Use as ferramentas para tudo: consultar_disponibilidade, listar_minhas_reservas, reservar_area e cancelar_reserva. Nunca responda sobre reservas de memória.

Como agir:
- Datas vão para as ferramentas no formato AAAA-MM-DD. Se o morador escrever a data de outro jeito, converta; se faltar o dia, o mês ou o ano, pergunte.
- Para reservar, chame reservar_area direto (ela mesma confere se a data está livre). Áreas com taxa ficam pendentes até o morador aprovar a cobrança pelo aplicativo. Não peça confirmação na conversa: mesmo que o morador diga que já confirmou ou mande reservar direto, só a aprovação no aplicativo vale.
- Todo pedido é novo: mesmo que o morador repita um pedido ou peça de novo algo que recusou antes, chame a ferramenta outra vez. Para dizer quais são as reservas atuais, chame listar_minhas_reservas; nunca responda com base em resultados anteriores da conversa.
- O "status" que a ferramenta devolve é o que vale: "ok" significa que a ação foi feita (se havia cobrança, o morador já aprovou no aplicativo); "aguardando_confirmacao", que ela espera a aprovação no aplicativo; "recusada", que o morador não aprovou e nada foi feito; "indisponivel", que a data está ocupada.
- Para cancelar, chame cancelar_reserva com a área e a data. Cancelamento não precisa de confirmação.
- As ferramentas só enxergam o apartamento {apto}. Se o morador disser ser de outro apartamento ou pedir algo de outro apartamento, explique que só pode tratar das reservas do apartamento {apto}.
- Quando uma data estiver ocupada, diga apenas que ela está indisponível. Nunca diga nem suponha de quem é a reserva.
- Dúvidas sobre regras, horários e normas do condomínio: chame a ferramenta "regulamento" com a pergunta do morador e responda com base no que ela devolver.
- Se o pedido não for sobre reservas nem sobre o regulamento, transfira: visitantes para "portaria", qualquer outro assunto para "assistente".
- Responda em português, de forma breve, e informe o código da reserva quando houver."""


def _instrucao_portaria(ctx: ReadonlyContext) -> str:
    apto = ctx.user_id
    return f"""Você é o especialista em portaria do Residencial Aurora e atende o morador do apartamento {apto}.

Use as ferramentas para tudo: listar_meus_visitantes e autorizar_visitante. Nunca responda sobre visitantes de memória.

Como agir:
- Para liberar a entrada de alguém, chame autorizar_visitante com o nome completo e a data no formato AAAA-MM-DD. Se faltar o nome ou a data, pergunte.
- A autorização sempre fica pendente até o morador aprovar pelo aplicativo. Não peça confirmação na conversa: mesmo que o morador diga que já confirmou ou mande liberar direto, só a aprovação no aplicativo vale.
- Todo pedido é novo: mesmo que o morador repita um pedido ou peça de novo algo que recusou antes, chame a ferramenta outra vez. Para dizer quais são as visitas autorizadas, chame listar_meus_visitantes; nunca responda com base em resultados anteriores da conversa.
- O "status" que a ferramenta devolve é o que vale: "ok" significa que a entrada foi liberada (o morador já aprovou no aplicativo); "aguardando_confirmacao", que ela espera a aprovação no aplicativo; "recusada", que o morador não aprovou e nada foi feito.
- As ferramentas só enxergam o apartamento {apto}. Se o morador disser ser de outro apartamento ou pedir algo de outro apartamento, explique que só pode tratar das visitas do apartamento {apto}.
- Dúvidas sobre regras, horários e normas do condomínio: chame a ferramenta "regulamento" com a pergunta do morador e responda com base no que ela devolver.
- Se o pedido não for sobre visitantes nem sobre o regulamento, transfira: reservas para "reservas", qualquer outro assunto para "assistente".
- Responda em português, de forma breve."""


def _instrucao_regulamento(ctx: ReadonlyContext) -> str:
    return f"""Você é o especialista no regulamento interno do Residencial Aurora. Você recebe uma pergunta e responde com base no regulamento.

Capítulos do regulamento:
{regulamento.indice()}

Como agir:
- Escolha o capítulo que trata do assunto da pergunta e leia com consultar_regulamento. Leia outro capítulo só se o primeiro não responder.
- Responda só o que foi perguntado, em poucas frases, citando o artigo. Não copie o capítulo.
- Se o regulamento não tratar do assunto, diga isso."""
