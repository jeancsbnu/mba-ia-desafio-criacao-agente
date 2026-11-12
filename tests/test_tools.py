"""Tools com um ToolContext simulado: apartamento da sessão (G2), confirmação (G1) e regulamento (G4)."""

import json
from types import SimpleNamespace

import pytest
from google.adk.tools import FunctionTool
from google.adk.tools.tool_confirmation import ToolConfirmation

from aurora import condominio, regulamento
from aurora import tools as t

PROIBIDOS_NO_101 = ("RSV-4821", "Marina Duarte", "302", "Rafael")


class Contexto:
    """Só o que as tools usam do ToolContext."""

    def __init__(self, apartamento="101", confirmacao: ToolConfirmation | None = None):
        self.user_id = apartamento
        self.tool_confirmation = confirmacao
        self.actions = SimpleNamespace(skip_summarization=False)
        self.pedidos = []

    def request_confirmation(self, *, hint=None, payload=None):
        self.pedidos.append({"hint": hint, "payload": payload})


def aprovado(ok=True):
    return Contexto(confirmacao=ToolConfirmation(confirmed=ok))


def sem_vazamento(*resultados):
    texto = json.dumps(resultados, ensure_ascii=False)
    for proibido in PROIBIDOS_NO_101:
        assert proibido not in texto


@pytest.mark.parametrize("funcao", t.TOOLS_RESERVAS + t.TOOLS_VISITANTES + t.TOOLS_REGULAMENTO, ids=lambda f: f.__name__)
def test_nenhuma_tool_recebe_apartamento(funcao):
    """G2: o modelo não tem como escolher o apartamento."""
    declaracao = FunctionTool(funcao)._get_declaration()
    parametros = set((declaracao.parameters_json_schema or {}).get("properties", {}))
    assert not parametros & {"apartamento", "apto", "tool_context"}


def test_listagens_mostram_so_o_proprio_apartamento():
    reservas = t.listar_minhas_reservas(Contexto())
    visitantes = t.listar_meus_visitantes(Contexto())
    assert reservas["reservas"] == [{"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}]
    assert visitantes["visitantes"] == []
    sem_vazamento(reservas, visitantes)


def test_cancelar_reserva_de_outro_nao_altera_nem_vaza():
    resultado = t.cancelar_reserva("salão de festas", "2030-03-16", Contexto())
    assert resultado["status"] == "nao_encontrada"
    sem_vazamento(resultado)
    assert [r.codigo for r in condominio.listar_reservas("302")] == ["RSV-4821"]


def test_cancelar_a_propria_sem_confirmacao():
    contexto = Contexto()
    assert t.cancelar_reserva("quadra", "2030-03-09", contexto)["status"] == "ok"
    assert contexto.pedidos == []


def test_area_sem_taxa_reserva_direto():
    contexto = Contexto()
    resultado = t.reservar_area("quadra", "2030-04-06", contexto)
    assert resultado["status"] == "ok" and resultado["cobranca"] == 0
    assert contexto.pedidos == []


def test_area_com_taxa_pede_confirmacao_e_nao_grava():
    contexto = Contexto()
    resultado = t.reservar_area("Salão de Festas", "2030-04-20", contexto)
    assert resultado["status"] == "aguardando_confirmacao"
    assert contexto.actions.skip_summarization
    assert contexto.pedidos[0]["payload"] == {"area": "salao-de-festas", "data": "2030-04-20", "taxa": 150.0}
    assert "R$ 150,00" in contexto.pedidos[0]["hint"]
    assert condominio.data_livre("salao-de-festas", "2030-04-20")


def test_reserva_negada_nao_grava():
    assert t.reservar_area("salao-de-festas", "2030-04-20", aprovado(False))["status"] == "recusada"
    assert condominio.data_livre("salao-de-festas", "2030-04-20")


def test_reserva_aprovada_grava_uma_vez():
    resultado = t.reservar_area("salao-de-festas", "2030-04-20", aprovado())
    assert resultado["status"] == "ok" and resultado["cobranca"] == 150.0
    assert len([r for r in condominio.listar_reservas("101") if r.area == "salao-de-festas"]) == 1


def test_data_ocupada_nao_pede_confirmacao_e_nao_vaza():
    contexto = Contexto()
    resultados = [
        t.reservar_area("salao-de-festas", "2030-03-16", contexto),
        t.consultar_disponibilidade("salao-de-festas", "2030-03-16"),
        t.reservar_area("salao-de-festas", "2030-03-16", aprovado()),  # mesmo aprovado, a constraint barra
    ]
    assert resultados[0]["status"] == "indisponivel" and contexto.pedidos == []
    assert resultados[1]["disponivel"] is False
    assert resultados[2]["status"] == "indisponivel"
    sem_vazamento(*resultados)


def test_visitante_sempre_pede_confirmacao():
    contexto = Contexto()
    assert t.autorizar_visitante("Joana Ribeiro", "2030-04-21", contexto)["status"] == "aguardando_confirmacao"
    assert contexto.pedidos[0]["payload"] == {"nome": "Joana Ribeiro", "data": "2030-04-21"}
    assert condominio.listar_visitantes("101") == []
    assert t.autorizar_visitante("Joana Ribeiro", "2030-04-21", aprovado(False))["status"] == "recusada"
    assert condominio.listar_visitantes("101") == []
    assert t.autorizar_visitante("Joana Ribeiro", "2030-04-21", aprovado())["status"] == "ok"
    assert [(v.nome, v.data) for v in condominio.listar_visitantes("101")] == [("Joana Ribeiro", "2030-04-21")]


@pytest.mark.parametrize(
    "chamada",
    [
        lambda: t.reservar_area("piscina", "2030-04-06", Contexto()),
        lambda: t.reservar_area("quadra", "06/04/2030", Contexto()),
        lambda: t.autorizar_visitante("  ", "2030-04-21", Contexto()),
    ],
    ids=["area-desconhecida", "data-fora-do-formato", "visitante-sem-nome"],
)
def test_entrada_invalida_vira_resposta_normal(chamada):
    assert chamada()["status"] == "erro"


def test_regulamento_devolve_um_capitulo():
    """G4: o capítulo IV (piscina) vem sozinho, sem texto de outros capítulos."""
    resultado = t.consultar_regulamento("IV")
    assert resultado["titulo"] == "Piscina" and "9h às 20h" in resultado["texto"]
    for numero, capitulo in regulamento.capitulos().items():
        if numero != "IV":
            assert capitulo.texto.splitlines()[0] not in resultado["texto"]
    assert len(regulamento.capitulos()) == 14


@pytest.mark.parametrize("referencia,esperado", [("4", "IV"), ("Capítulo XIV", "XIV"), ("ix", "IX")])
def test_regulamento_aceita_numero_em_varios_formatos(referencia, esperado):
    assert t.consultar_regulamento(referencia)["capitulo"] == esperado


def test_regulamento_capitulo_inexistente():
    assert t.consultar_regulamento("XV")["status"] == "erro"
