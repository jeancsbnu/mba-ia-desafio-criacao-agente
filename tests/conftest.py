import pytest

from aurora import banco, config, modelos
from tests import apoio


@pytest.fixture(autouse=True)
def banco_temporario(tmp_path, monkeypatch):
    """Cada teste roda com bancos novos numa pasta temporária, carregados de dados/."""
    monkeypatch.setattr(config, "VAR", tmp_path)
    monkeypatch.setattr(config, "BANCO_CONDOMINIO", tmp_path / "condominio.db")
    monkeypatch.setattr(config, "BANCO_SESSOES", tmp_path / "sessoes.db")
    banco.restaurar()
    apoio.CHAMADAS.clear()
    modelos._pausado_ate.clear()
    return tmp_path


@pytest.fixture(autouse=True)
def sem_gemini(monkeypatch):
    """Sem chave: se algum caminho escapar do roteiro e chamar o Gemini, falha em vez de gastar cota."""
    for variavel in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"):
        monkeypatch.delenv(variavel, raising=False)
