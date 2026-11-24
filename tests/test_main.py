"""Comando de subida: host e porta vêm do ambiente, com 127.0.0.1:8000 como padrão."""

import aurora.__main__ as principal


def subir(monkeypatch, **ambiente):
    chamado = {}
    monkeypatch.setattr(principal, "load_dotenv", lambda *_a, **_k: None)
    monkeypatch.setattr(principal.uvicorn, "run", lambda app, **opcoes: chamado.update(app=app, **opcoes))
    for nome in ("AURORA_HOST", "AURORA_PORTA"):
        monkeypatch.delenv(nome, raising=False)
    for nome, valor in ambiente.items():
        monkeypatch.setenv(nome, valor)
    principal.main()
    return chamado


def test_padrao_e_localhost_8000(monkeypatch):
    assert subir(monkeypatch) == {"app": "aurora.api:app", "host": "127.0.0.1", "port": 8000}


def test_host_e_porta_do_ambiente(monkeypatch):
    chamado = subir(monkeypatch, AURORA_HOST="0.0.0.0", AURORA_PORTA="8011")
    assert (chamado["host"], chamado["port"]) == ("0.0.0.0", 8011)


def test_variaveis_vazias_usam_o_padrao(monkeypatch):
    chamado = subir(monkeypatch, AURORA_HOST="", AURORA_PORTA="")
    assert (chamado["host"], chamado["port"]) == ("127.0.0.1", 8000)
