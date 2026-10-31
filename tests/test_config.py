"""config.py: credenciais vindas de segredos.py (ou vazias sem ele), CA por
variável de ambiente e coerência de alguns valores. Os testes passam com e
sem tagauditor/segredos.py, e nunca leem o conteúdo dele."""
import importlib
import os
import sys
import types

import pytest

import tagauditor
from tagauditor import config

_CREDENCIAIS = ("IDENTIFICADOR", "SENHA", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                "CONFLUENCE_TOKEN", "CONFLUENCE_USUARIO", "CONFLUENCE_SENHA", "FIGMA_TOKEN")


@pytest.fixture
def config_restaurado():
    """No fim do teste recarrega o config.py de verdade e devolve ao módulo os
    MESMOS objetos de antes (quem já importou valores dele não percebe nada).
    Os patches do teste ficam num monkeypatch.context(), desfeito antes."""
    original = dict(vars(config))
    yield
    importlib.reload(config)
    vars(config).update(original)


def test_sem_segredos_py_as_credenciais_ficam_vazias(config_restaurado, monkeypatch, capsys):
    with monkeypatch.context() as m:
        m.setitem(sys.modules, "tagauditor.segredos", None)    # o import passa a falhar
        m.delattr(tagauditor, "segredos", raising=False)
        importlib.reload(config)
        assert config._segredos is None
        for nome in _CREDENCIAIS:
            assert getattr(config, nome) == "", nome
    assert "segredos.py nao encontrado" in capsys.readouterr().out


def test_ca_interna_pela_variavel_de_ambiente(config_restaurado, monkeypatch, tmp_path):
    ca = str(tmp_path / "ca-banco.pem")
    with monkeypatch.context() as m:
        m.setenv("TAG_STUDIO_CA", ca)
        importlib.reload(config)
        assert config.ARQUIVO_CA == ca


def test_sem_variavel_de_ambiente_nao_ha_ca_extra(config_restaurado, monkeypatch):
    with monkeypatch.context() as m:
        m.delenv("TAG_STUDIO_CA", raising=False)
        importlib.reload(config)
        assert config.ARQUIVO_CA == ""


def test_segredo_le_do_modulo_de_segredos(monkeypatch):
    monkeypatch.setattr(config, "_segredos", types.SimpleNamespace(FIGMA_TOKEN="figd_teste"))
    assert config._segredo("FIGMA_TOKEN") == "figd_teste"
    assert config._segredo("NAO_EXISTE") == ""


def test_segredo_sem_modulo_e_vazio(monkeypatch):
    monkeypatch.setattr(config, "_segredos", None)
    assert config._segredo("FIGMA_TOKEN") == ""


def test_base_dir_e_a_raiz_do_projeto():
    assert os.path.isfile(os.path.join(config.BASE_DIR, "tag_auditor_chat.py"))
    assert os.path.isdir(os.path.join(config.BASE_DIR, "tagauditor"))


def test_valores_que_outras_partes_dependem():
    assert config.PLANNER_MAX_PARTES <= 10             # teto do Azure OpenAI por requisição
    assert config.PLANNER_MODELO_LLM == config.MODELO_LLM
    assert len(config.MODELO_LLM.split(";")) == 2
    assert config.URL_AGENTE.startswith(config.BASE_API)
    assert all(origem.startswith("https://") for origem in config.CONFLUENCE_ORIGENS)
    assert config.TEMPERATURA is None or config.REASONING_EFFORT is None


def test_segredos_exemplo_e_um_modelo_em_branco():
    from tagauditor import segredos_exemplo
    nomes = [n for n in vars(segredos_exemplo) if n.isupper()]
    assert sorted(nomes) == sorted(_CREDENCIAIS)
    for nome in nomes:
        assert getattr(segredos_exemplo, nome) == "", nome    # nada de credencial no Git
