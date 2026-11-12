"""Camada de dados: estado inicial, isolamento por apartamento, códigos e exclusividade (G5)."""

import threading

import pytest

from aurora import banco
from aurora import condominio as c
from aurora.condominio import Reserva, Visitante

CODIGOS_INICIAIS = {"RSV-1377", "RSV-4821", "RSV-2950"}


def test_estado_inicial_vem_de_dados():
    assert c.listar_reservas("101") == [Reserva("RSV-1377", "quadra", "2030-03-09")]
    assert c.listar_visitantes("302") == [Visitante("Marina Duarte", "2030-03-16")]


def test_catalogo_de_areas():
    assert c.resolver_area("Salão de Festas").id == "salao-de-festas"
    assert c.resolver_area("salao-de-festas").id == "salao-de-festas"
    assert c.resolver_area("Quadra poliesportiva").id == "quadra"
    assert c.resolver_area("piscina") is None
    assert c.areas()["salao-de-festas"].gera_cobranca
    assert not c.areas()["quadra"].gera_cobranca


@pytest.mark.parametrize(
    "taxa,esperado",
    [(150.0, "R$ 150,00"), (80, "R$ 80,00"), (1500.5, "R$ 1.500,50"), (1234567.891, "R$ 1.234.567,89")],
)
def test_taxa_em_reais(taxa, esperado):
    assert c.Area("x", "X", taxa).taxa_em_reais == esperado


@pytest.mark.parametrize("data", ["2030-02-30", "2030-4-6", "06/04/2030", ""])
def test_data_invalida(data):
    with pytest.raises(ValueError):
        c.validar_data(data)


def test_data_livre_nao_revela_o_dono():
    assert c.data_livre("salao-de-festas", "2030-03-16") is False
    assert c.data_livre("salao-de-festas", "2030-04-20") is True


def test_nao_cancela_reserva_de_outro_apartamento():
    assert c.cancelar_reserva("101", area_id="salao-de-festas", data="2030-03-16") is None
    assert c.cancelar_reserva("101", codigo="RSV-4821") is None
    assert [r.codigo for r in c.listar_reservas("302")] == ["RSV-4821"]


def test_cancela_a_propria_e_libera_a_data():
    assert c.cancelar_reserva("101", area_id="quadra", data="2030-03-09").codigo == "RSV-1377"
    assert c.listar_reservas("101") == []
    assert c.data_livre("quadra", "2030-03-09")


def test_codigo_novo_nunca_repete():
    c.cancelar_reserva("101", area_id="quadra", data="2030-03-09")
    nova_mesma_data = c.criar_reserva("101", "quadra", "2030-03-09")
    outra = c.criar_reserva("101", "quadra", "2030-04-06")
    codigos = {nova_mesma_data.codigo, outra.codigo}
    assert len(codigos) == 2 and not codigos & CODIGOS_INICIAIS


def test_data_ocupada_devolve_none():
    assert c.criar_reserva("101", "salao-de-festas", "2030-03-16") is None


def test_autorizacao_de_visitante_e_idempotente():
    assert c.autorizar_visitante("101", "  Joana   Ribeiro ", "2030-04-21") is True
    assert c.autorizar_visitante("101", "joana ribeiro", "2030-04-21") is False
    assert c.listar_visitantes("101") == [Visitante("Joana Ribeiro", "2030-04-21")]


def test_disputa_simultanea_grava_uma_reserva():
    """G5: 20 threads pedem o salão na mesma data; o índice único deixa só uma."""
    largada = threading.Barrier(20)
    resultados, erros = [], []

    def disputar(apto):
        largada.wait()
        try:
            resultados.append(c.criar_reserva(apto, "salao-de-festas", "2030-05-11"))
        except Exception as e:  # noqa: BLE001
            erros.append(e)

    threads = [threading.Thread(target=disputar, args=(["101", "201"][i % 2],)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert erros == []
    assert len([r for r in resultados if r is not None]) == 1
    total = sum(
        1 for apto in ("101", "201") for r in c.listar_reservas(apto) if (r.area, r.data) == ("salao-de-festas", "2030-05-11")
    )
    assert total == 1


def test_inicializar_nao_apaga_banco_existente():
    c.criar_reserva("101", "quadra", "2030-04-06")
    antes = c.listar_reservas("101")
    banco.inicializar()
    assert c.listar_reservas("101") == antes


def test_restaurar_volta_ao_inicial_e_guarda_codigos_emitidos():
    emitido = c.criar_reserva("101", "quadra", "2030-04-06").codigo
    assert banco.restaurar() == (3, 2)
    assert c.listar_reservas("101") == [Reserva("RSV-1377", "quadra", "2030-03-09")]
    with banco.leitura() as con:
        registrados = {linha[0] for linha in con.execute("SELECT codigo FROM codigos_emitidos")}
    assert {emitido} | CODIGOS_INICIAIS <= registrados


def test_sessao_ligada_ao_apartamento():
    banco.registrar_sessao("s-1", "101")
    assert banco.apartamento_da_sessao("s-1") == "101"
    assert banco.apartamento_da_sessao("outra") is None
    banco.esquecer_sessoes()
    assert banco.apartamento_da_sessao("s-1") is None
