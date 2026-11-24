"""Carregamento do .env: o terminal tem prioridade, mas uma variável vazia não conta."""

from aurora import config


def carregar(tmp_path, monkeypatch, conteudo, **ambiente):
    arquivo = tmp_path / ".env"
    arquivo.write_text(conteudo, encoding="utf-8")
    for nome in ("GOOGLE_API_KEY", "AURORA_PORTA"):
        monkeypatch.delenv(nome, raising=False)
    for nome, valor in ambiente.items():
        monkeypatch.setenv(nome, valor)
    config.carregar_env(arquivo)


def test_variavel_ausente_vem_do_env(tmp_path, monkeypatch):
    import os

    carregar(tmp_path, monkeypatch, "GOOGLE_API_KEY=do-arquivo\n")
    assert os.environ["GOOGLE_API_KEY"] == "do-arquivo"


def test_variavel_vazia_no_terminal_nao_anula_o_env(tmp_path, monkeypatch):
    import os

    carregar(tmp_path, monkeypatch, "GOOGLE_API_KEY=do-arquivo\n", GOOGLE_API_KEY="")
    assert os.environ["GOOGLE_API_KEY"] == "do-arquivo"


def test_variavel_preenchida_no_terminal_tem_prioridade(tmp_path, monkeypatch):
    import os

    carregar(tmp_path, monkeypatch, "GOOGLE_API_KEY=do-arquivo\n", GOOGLE_API_KEY="do-terminal")
    assert os.environ["GOOGLE_API_KEY"] == "do-terminal"


def test_valor_vazio_no_env_nao_apaga_o_terminal(tmp_path, monkeypatch):
    """O script do fluxo passa AURORA_PORTA pelo ambiente; o `AURORA_PORTA=` do .env não pode zerá-la."""
    import os

    carregar(tmp_path, monkeypatch, "AURORA_PORTA=\n", AURORA_PORTA="8001")
    assert os.environ["AURORA_PORTA"] == "8001"
