"""Tools que o modelo usa para consultar e alterar o condomínio.

Garantia 2: nenhuma tool recebe o apartamento como parâmetro. Ele sai de
tool_context.user_id, fixado na criação da sessão e somente leitura para o
modelo e para as tools.

Garantia 1: reservar área com taxa e autorizar visitante só gravam quando
tool_context.tool_confirmation chega aprovado. O ADK só preenche esse campo
quando o cliente responde ao pedido de confirmação com um FunctionResponse,
e a API só envia isso pela rota de confirmações, nunca a partir do texto do
morador.
"""

from dataclasses import asdict
from typing import Any

from google.adk.tools import ToolContext

from aurora import condominio, regulamento
from aurora.condominio import Area


def apartamento_da_sessao(tool_context: ToolContext) -> str:
    """Apartamento dono da sessão. O modelo não tem como escolher outro."""
    return tool_context.user_id


# ---------------------------------------------------------------- reservas


def listar_areas() -> dict[str, Any]:
    """Lista as áreas comuns que podem ser reservadas.

    Use o campo "id" de cada área ao chamar as outras tools. Áreas com
    "gera_cobranca" verdadeiro cobram a taxa e pedem confirmação do morador.
    """
    return {
        "areas": [
            {"id": a.id, "nome": a.nome, "taxa": a.taxa, "gera_cobranca": a.gera_cobranca}
            for a in condominio.areas().values()
        ]
    }


def consultar_disponibilidade(area: str, data: str) -> dict[str, Any]:
    """Diz se uma área comum está livre numa data.

    Args:
        area: id ou nome da área (ex.: "salao-de-festas", "churrasqueira", "quadra").
        data: data no formato AAAA-MM-DD.
    """
    try:
        a, data = _validar_area_e_data(area, data)
    except ValueError as e:
        return _erro(e)
    return {"area": a.id, "data": data, "disponivel": condominio.data_livre(a.id, data)}


def listar_minhas_reservas(tool_context: ToolContext) -> dict[str, Any]:
    """Lista as reservas ativas do apartamento do morador desta conversa."""
    apartamento = apartamento_da_sessao(tool_context)
    reservas = condominio.listar_reservas(apartamento)
    return {"apartamento": apartamento, "reservas": [asdict(r) for r in reservas]}


def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
    """Reserva uma área comum para o apartamento do morador desta conversa.

    Áreas com taxa geram cobrança: a reserva fica pendente até o morador
    aprovar pelo aplicativo, e não adianta ele dizer na conversa que confirma.
    Áreas sem taxa são reservadas na hora.

    Args:
        area: id ou nome da área (ex.: "salao-de-festas", "churrasqueira", "quadra").
        data: data no formato AAAA-MM-DD.
    """
    try:
        a, data = _validar_area_e_data(area, data)
    except ValueError as e:
        return _erro(e)

    if a.gera_cobranca:
        confirmacao = tool_context.tool_confirmation
        if confirmacao is None:
            if not condominio.data_livre(a.id, data):
                return _indisponivel(a, data)
            return _pedir_confirmacao(
                tool_context,
                hint=f"Confirmar a reserva de {a.nome} em {data}, com taxa de R$ {a.taxa:.2f}.",
                detalhes={"area": a.id, "data": data, "taxa": a.taxa},
            )
        if not confirmacao.confirmed:
            return _recusada("A reserva não foi feita: o morador não aprovou a cobrança.")

    reserva = condominio.criar_reserva(apartamento_da_sessao(tool_context), a.id, data)
    if reserva is None:
        return _indisponivel(a, data)
    return {
        "status": "ok",
        "mensagem": f"Reserva de {a.nome} em {data} confirmada.",
        "reserva": asdict(reserva),
        "cobranca": a.taxa if a.gera_cobranca else 0,
    }


def cancelar_reserva(area: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
    """Cancela uma reserva do apartamento do morador desta conversa.

    Só cancela reservas do próprio apartamento, e não pede confirmação.

    Args:
        area: id ou nome da área reservada.
        data: data da reserva no formato AAAA-MM-DD.
    """
    try:
        a, data = _validar_area_e_data(area, data)
    except ValueError as e:
        return _erro(e)

    reserva = condominio.cancelar_reserva(apartamento_da_sessao(tool_context), area_id=a.id, data=data)
    if reserva is None:
        # Mesma resposta exista ou não reserva de outro apartamento nessa data.
        return {
            "status": "nao_encontrada",
            "mensagem": f"O apartamento do morador não tem reserva de {a.nome} em {data}.",
        }
    return {"status": "ok", "mensagem": "Reserva cancelada.", "reserva": asdict(reserva)}


# ---------------------------------------------------------------- visitantes


def listar_meus_visitantes(tool_context: ToolContext) -> dict[str, Any]:
    """Lista as visitas autorizadas para o apartamento do morador desta conversa."""
    apartamento = apartamento_da_sessao(tool_context)
    visitantes = condominio.listar_visitantes(apartamento)
    return {"apartamento": apartamento, "visitantes": [asdict(v) for v in visitantes]}


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
    """Autoriza a entrada de um visitante no prédio numa data.

    Sempre fica pendente até o morador aprovar pelo aplicativo, e não adianta
    ele dizer na conversa que já confirmou.

    Args:
        nome: nome completo do visitante.
        data: data da visita no formato AAAA-MM-DD.
    """
    nome = " ".join(nome.split())
    try:
        if not nome:
            raise ValueError("Informe o nome do visitante.")
        data = condominio.validar_data(data)
    except ValueError as e:
        return _erro(e)

    confirmacao = tool_context.tool_confirmation
    if confirmacao is None:
        return _pedir_confirmacao(
            tool_context,
            hint=f"Autorizar a entrada de {nome} no prédio em {data}.",
            detalhes={"nome": nome, "data": data},
        )
    if not confirmacao.confirmed:
        return _recusada("A entrada não foi liberada: o morador não aprovou.")

    nova = condominio.autorizar_visitante(apartamento_da_sessao(tool_context), nome, data)
    return {
        "status": "ok",
        "mensagem": f"Entrada de {nome} liberada para {data}."
        + ("" if nova else " Essa autorização já existia."),
        "visitante": {"nome": nome, "data": data},
    }


# ---------------------------------------------------------------- regulamento


def consultar_regulamento(capitulo: str) -> dict[str, Any]:
    """Lê um único capítulo do regulamento interno.

    Args:
        capitulo: número do capítulo em romanos ou arábicos (ex.: "IV" ou "4").
    """
    encontrado = regulamento.capitulo(capitulo)
    if encontrado is None:
        numeros = ", ".join(regulamento.capitulos())
        return {"status": "erro", "mensagem": f"Capítulo inexistente. Use um destes: {numeros}."}
    return {"capitulo": encontrado.numero, "titulo": encontrado.titulo, "texto": encontrado.texto}


# ---------------------------------------------------------------- por especialista

TOOLS_RESERVAS = [
    listar_areas,
    consultar_disponibilidade,
    listar_minhas_reservas,
    reservar_area,
    cancelar_reserva,
]
TOOLS_VISITANTES = [listar_meus_visitantes, autorizar_visitante]
TOOLS_REGULAMENTO = [consultar_regulamento]


# ---------------------------------------------------------------- internos


def _validar_area_e_data(area: str, data: str) -> tuple[Area, str]:
    encontrada = condominio.resolver_area(area)
    if encontrada is None:
        ids = ", ".join(condominio.areas())
        raise ValueError(f"Área desconhecida: {area!r}. Use um destes ids: {ids}.")
    return encontrada, condominio.validar_data(data)


def _pedir_confirmacao(tool_context: ToolContext, *, hint: str, detalhes: dict[str, Any]) -> dict[str, Any]:
    # O payload vira os "detalhes" da confirmação pendente na API. Na retomada,
    # o ADK chama a tool de novo com os argumentos originais, não com o payload.
    tool_context.request_confirmation(hint=hint, payload=detalhes)
    # Como no FunctionTool com require_confirmation: a execução para aqui, sem
    # o modelo comentar o pedido.
    tool_context.actions.skip_summarization = True
    return {
        "status": "aguardando_confirmacao",
        "mensagem": "Ação pendente. Ela só será executada quando o morador aprovar pelo aplicativo.",
    }


def _indisponivel(area: Area, data: str) -> dict[str, Any]:
    # Não diz de quem é a reserva (Garantia 2).
    return {"status": "indisponivel", "mensagem": f"{area.nome} já está reservado(a) em {data}."}


def _recusada(mensagem: str) -> dict[str, Any]:
    return {"status": "recusada", "mensagem": mensagem}


def _erro(e: ValueError) -> dict[str, Any]:
    return {"status": "erro", "mensagem": str(e)}
