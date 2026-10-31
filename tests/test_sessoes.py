import pytest

from tagauditor import sessoes


@pytest.fixture
def sessao():
    usuario = {"email": "pessoa@exemplo.com", "nome": "Pessoa"}
    sessoes.SESSOES_ATIVAS["a1b2c3d4"] = usuario
    yield usuario
    sessoes.SESSOES_ATIVAS.pop("a1b2c3d4", None)


def test_token_certo_devolve_o_usuario(sessao):
    assert sessoes.usuario_da_sessao("a1b2c3d4") is sessao


@pytest.mark.parametrize("token", [None, "", "a1b2c3d5", "a1b2c3d4 ", "tökén"])
def test_token_errado_ou_vazio_nao_abre_sessao(sessao, token):
    assert sessoes.usuario_da_sessao(token) is None


def test_logout_remove_a_sessao(sessao):
    assert sessoes.encerrar_sessao("a1b2c3d4") is sessao
    assert sessoes.usuario_da_sessao("a1b2c3d4") is None
    assert sessoes.encerrar_sessao("a1b2c3d4") is None
