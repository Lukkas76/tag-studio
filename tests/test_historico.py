import pytest

from tagauditor import historico


@pytest.fixture(autouse=True)
def historico_vazio(monkeypatch):
    # cada teste começa sem nenhuma sessão guardada
    monkeypatch.setattr(historico, "_historico", {})


def _msg(role, content):
    return {"role": role, "content": content}


def test_sessao_desconhecida_tem_historico_vazio():
    assert historico.historico_sessao("nunca-vista") == []


def test_mensagens_ficam_na_ordem_por_sessao():
    historico.anexar_historico("s1", "user", "plano")
    historico.anexar_historico("s1", "assistant", "auditoria")
    historico.anexar_historico("s2", "user", "outra aba")

    assert historico.historico_sessao("s1") == [_msg("user", "plano"),
                                                _msg("assistant", "auditoria")]
    assert historico.historico_sessao("s2") == [_msg("user", "outra aba")]


def test_historico_devolvido_e_uma_copia():
    historico.anexar_historico("s1", "user", "plano")
    copia = historico.historico_sessao("s1")
    copia.append(_msg("user", "intruso"))
    copia.clear()

    assert historico.historico_sessao("s1") == [_msg("user", "plano")]


def test_sessao_mais_antiga_sai_quando_passa_do_limite(monkeypatch):
    monkeypatch.setattr(historico, "MAX_SESSOES_HISTORICO", 2)
    historico.anexar_historico("s1", "user", "um")
    historico.anexar_historico("s2", "user", "dois")
    # mensagem nova numa sessão que já existe não remove ninguém
    historico.anexar_historico("s1", "assistant", "um-resposta")
    assert historico.historico_sessao("s1") == [_msg("user", "um"),
                                                _msg("assistant", "um-resposta")]

    historico.anexar_historico("s3", "user", "três")

    assert historico.historico_sessao("s1") == []
    assert historico.historico_sessao("s2") == [_msg("user", "dois")]
    assert historico.historico_sessao("s3") == [_msg("user", "três")]


def test_conversa_longa_preserva_as_duas_primeiras_e_as_recentes(monkeypatch):
    monkeypatch.setattr(historico, "MAX_MSGS_HISTORICO", 5)
    for i in range(7):
        historico.anexar_historico("s1", "user" if i % 2 == 0 else "assistant", "m{}".format(i))

    conteudos = [m["content"] for m in historico.historico_sessao("s1")]
    assert conteudos == ["m0", "m1", "m4", "m5", "m6"]


def test_conversa_no_limite_nao_e_cortada(monkeypatch):
    monkeypatch.setattr(historico, "MAX_MSGS_HISTORICO", 3)
    for i in range(3):
        historico.anexar_historico("s1", "user", "m{}".format(i))

    assert [m["content"] for m in historico.historico_sessao("s1")] == ["m0", "m1", "m2"]
