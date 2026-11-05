"""Contrato da API (FastAPI TestClient) com LLM roteirizado e SQLite temporário."""

import threading

from fastapi.testclient import TestClient

from aurora.api import criar_api
from tests import apoio
from tests.apoio import chamar, texto


def cliente(**passos) -> TestClient:
    apoio.CHAMADAS.clear()
    return TestClient(criar_api(apoio.roteiros(**passos)))


def cliente_sem_cota() -> TestClient:
    modelos = {nome: apoio.SemCota(model=nome) for nome in apoio.AGENTES}
    return TestClient(criar_api(modelos))


def criar_sessao(c, apto="101") -> str:
    r = c.post("/sessoes", json={"apartamento": apto})
    assert r.status_code == 201 and set(r.json()) == {"session_id"}
    return r.json()["session_id"]


def salao(c, apto, data):
    return [r for r in c.get(f"/apartamentos/{apto}/reservas").json() if (r["area"], r["data"]) == ("salao-de-festas", data)]


RESERVAR_SALAO = {
    "assistente": [chamar("transfer_to_agent", agent_name="reservas")],
    "reservas": [chamar("reservar_area", area="salao-de-festas", data="2030-04-20"), texto("Feito.")],
}


def test_rotas_de_verificacao_e_criacao_de_sessao():
    with cliente() as c:
        assert c.get("/apartamentos/101/reservas").json() == [{"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}]
        assert c.get("/apartamentos/302/visitantes").json() == [{"nome": "Marina Duarte", "data": "2030-03-16"}]
        criar_sessao(c)
        assert c.post("/sessoes", json={"apartamento": "999"}).status_code == 422


def test_sessao_inexistente_responde_404():
    with cliente() as c:
        assert c.get("/sessoes/sessao-inexistente/eventos").status_code == 404
        assert c.post("/sessoes/sessao-inexistente/mensagens", json={"texto": "oi"}).status_code == 404
        assert c.post("/sessoes/sessao-inexistente/confirmacoes", json={"id": "x", "confirmado": True}).status_code == 404


def test_pendencia_negar_aprovar_e_reenviar():
    with cliente(**RESERVAR_SALAO) as c:
        sid = criar_sessao(c)
        r = c.post(f"/sessoes/{sid}/mensagens", json={"texto": "Reserve o salão de festas para 2030-04-20."})
        assert r.status_code == 200 and set(r.json()) == {"resposta", "confirmacoes_pendentes"}
        [pendente] = r.json()["confirmacoes_pendentes"]
        assert set(pendente) == {"id", "acao", "detalhes"} and pendente["acao"] == "reservar_area"
        assert pendente["detalhes"]["area"] == "salao-de-festas" and pendente["detalhes"]["data"] == "2030-04-20"
        assert r.json()["resposta"].startswith("Aguardando sua aprovação")
        assert not salao(c, "101", "2030-04-20")

        r = c.post(f"/sessoes/{sid}/confirmacoes", json={"id": pendente["id"], "confirmado": True})
        assert r.status_code == 200 and r.json() == {"resposta": "Feito.", "confirmacoes_pendentes": []}
        assert len(salao(c, "101", "2030-04-20")) == 1

        reenvio = c.post(f"/sessoes/{sid}/confirmacoes", json={"id": pendente["id"], "confirmado": True})
        assert reenvio.status_code == 409
        assert len(salao(c, "101", "2030-04-20")) == 1


def test_negar_nao_grava_e_reenvio_recebe_409():
    passos = dict(RESERVAR_SALAO, reservas=[RESERVAR_SALAO["reservas"][0], texto("Não reservei.")])
    with cliente(**passos) as c:
        sid = criar_sessao(c)
        [pendente] = c.post(f"/sessoes/{sid}/mensagens", json={"texto": "Reserve o salão."}).json()["confirmacoes_pendentes"]
        r = c.post(f"/sessoes/{sid}/confirmacoes", json={"id": pendente["id"], "confirmado": False})
        assert r.status_code == 200 and not salao(c, "101", "2030-04-20")
        assert c.post(f"/sessoes/{sid}/confirmacoes", json={"id": pendente["id"], "confirmado": True}).status_code == 409
        assert not salao(c, "101", "2030-04-20")


def test_id_inexistente_ou_de_outra_sessao_recebe_409():
    with cliente(**RESERVAR_SALAO) as c:
        sa, sb = criar_sessao(c, "101"), criar_sessao(c, "201")
        [pendente] = c.post(f"/sessoes/{sa}/mensagens", json={"texto": "Reserve o salão."}).json()["confirmacoes_pendentes"]
        antes = c.get("/apartamentos/101/reservas").json()
        assert c.post(f"/sessoes/{sa}/confirmacoes", json={"id": "id-inexistente", "confirmado": True}).status_code == 409
        assert c.post(f"/sessoes/{sb}/confirmacoes", json={"id": pendente["id"], "confirmado": True}).status_code == 409
        assert c.get("/apartamentos/101/reservas").json() == antes
        assert not salao(c, "201", "2030-04-20")


def test_eventos_sobrevivem_a_recriar_a_api():
    with cliente(**RESERVAR_SALAO) as c:
        sid = criar_sessao(c)
        c.post(f"/sessoes/{sid}/mensagens", json={"texto": "Reserve o salão."})
        eventos = c.get(f"/sessoes/{sid}/eventos").json()
        assert eventos and all("author" in e and "id" in e for e in eventos)
    with cliente() as c:
        assert c.get(f"/sessoes/{sid}/eventos").json() == eventos


def test_modelo_sem_cota_responde_503():
    with cliente_sem_cota() as c:
        sid = criar_sessao(c)
        r = c.post(f"/sessoes/{sid}/mensagens", json={"texto": "Reserve a quadra para 2030-08-01."})
        assert r.status_code == 503 and "indisponível" in r.json()["detail"]


def test_aprovacoes_simultaneas_gravam_uma_reserva():
    """G5 / passo 14: duas sessões, aprovações ao mesmo tempo; as duas → 200, uma reserva."""
    passos = {
        "assistente": [chamar("transfer_to_agent", agent_name="reservas")] * 2,
        "reservas": [chamar("reservar_area", area="salao-de-festas", data="2030-05-11")] * 2 + [texto("Resultado.")] * 2,
    }
    with cliente(**passos) as c:
        s3, s4 = criar_sessao(c, "101"), criar_sessao(c, "201")
        p3 = c.post(f"/sessoes/{s3}/mensagens", json={"texto": "Reserve o salão para 2030-05-11."}).json()["confirmacoes_pendentes"]
        p4 = c.post(f"/sessoes/{s4}/mensagens", json={"texto": "Reserve o salão para 2030-05-11."}).json()["confirmacoes_pendentes"]
        largada, status = threading.Barrier(2), {}

        def aprovar(sid, conf_id):
            largada.wait()
            status[sid] = c.post(f"/sessoes/{sid}/confirmacoes", json={"id": conf_id, "confirmado": True}).status_code

        threads = [threading.Thread(target=aprovar, args=a) for a in ((s3, p3[0]["id"]), (s4, p4[0]["id"]))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(status.values()) == [200, 200]
        assert len(salao(c, "101", "2030-05-11")) + len(salao(c, "201", "2030-05-11")) == 1
