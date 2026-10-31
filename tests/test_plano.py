"""Limpeza do plano (base64, enxugo, corte de valores) e montagem da mensagem."""
import json

import pytest

from tagauditor import plano


@pytest.fixture
def padrao(monkeypatch):
    """Flags do plano fixas no teste (independente do config.py do momento)."""
    for nome, valor in {
        "SANITIZAR_CHAVES_DUPLAS": False, "REMOVER_CAMPOS_BASE64": True,
        "TAMANHO_MIN_BASE64": 500, "ENXUGAR_PLANO": True, "LIMITE_VALOR_STR": 200,
        "LIMITE_OBS_STR": 500, "MINIFICAR_JSON": True, "SOMENTE_TABELA": False,
        "INSTRUCAO_PADRAO": "Audite o seguinte plano de tagueamento:",
    }.items():
        monkeypatch.setattr(plano, nome, valor)


# ---- sanitizar ---------------------------------------------------------------

def test_sanitizar_desligado_nao_mexe(padrao):
    assert plano.sanitizar("{{x}}") == "{{x}}"


def test_sanitizar_ligado_separa_chaves_duplas(padrao, monkeypatch):
    monkeypatch.setattr(plano, "SANITIZAR_CHAVES_DUPLAS", True)
    assert plano.sanitizar("a {{x}} b") == "a { {x} } b"


# ---- base64 ------------------------------------------------------------------

@pytest.mark.parametrize("valor, esperado", [
    (12345, False),
    ("QUJD" * 10, False),                                    # curto demais
    ("QUJD" * 200, True),                                    # alfabeto base64 puro
    ("data:image/png;base64," + "A" * 600, True),
    ("frase comum " * 60, False),                            # espaço não é base64
])
def test_parece_base64(padrao, valor, esperado):
    assert plano._parece_base64(valor) is esperado


def test_limpar_base64_remove_campos_e_conta(padrao):
    imagem = "QUJD" * 200
    data_uri = "data:image/png;base64," + "A" * 600
    texto = "frase comum " * 60
    stats = {"campos": 0, "chars": 0}
    limpo = plano.limpar_base64({
        "foto_b64": "abc",
        "Base64Img": {"x": 1},
        "lista": [imagem, "curto", 5],
        "data": data_uri,
        "texto": texto,
    }, stats)
    assert limpo == {
        "lista": ["[imagem/base64 removido: 800 caracteres]", "curto", 5],
        "data": "[imagem/base64 removido: 622 caracteres]",
        "texto": texto,
    }
    assert stats == {"campos": 4, "chars": 3 + len('{"x": 1}') + 800 + 622}


# ---- observações (rich_texts) --------------------------------------------------

def test_html_para_texto_tira_tags_e_entidades():
    entrada = "<p>&lt;a&gt;&nbsp;&quot;x&quot; &#39;y&apos;</p>\n<b>&amp;</b>"
    assert plano._html_para_texto(entrada) == "<a> \"x\" 'y' &"


def test_colapsa_frase_repetida():
    assert plano._colapsa_dup("Tela inicial Tela inicial") == "Tela inicial"
    assert plano._colapsa_dup("Tela inicial outra") == "Tela inicial outra"
    assert plano._colapsa_dup("um") == "um"


def test_observacoes_juntam_sem_repetir(padrao):
    item = {
        "a": "<b>Observações:</b> Enviar&nbsp;no clique",
        "b": ["Observações: Enviar no clique"],
        "c": {"d": "Observacoes: Tela inicial Tela inicial"},
        "n": 3,
    }
    assert plano._observacoes(item) == "Enviar no clique | Tela inicial"


def test_observacoes_longas_sao_cortadas(padrao, monkeypatch):
    monkeypatch.setattr(plano, "LIMITE_OBS_STR", 10)
    assert plano._observacoes(["Enviar no clique | Tela inicial"]) == \
        "Enviar no ...[+21 caracteres]"


@pytest.mark.parametrize("item", [[], {"a": "   "}, {"a": "<br/>"}, "Observações:"])
def test_observacoes_vazias(padrao, item):
    assert plano._observacoes(item) == ""


# ---- enxugo --------------------------------------------------------------------

@pytest.mark.parametrize("chave, lixo", [
    ("dom", True), ("Posição", True), ("screenshot_url", True), ("css_path", True),
    ("createdAt", True), (" HTML ", True), ("event", False), ("label", False),
    ("titulo", False), ("parametros", False),
])
def test_chave_lixo(chave, lixo):
    assert plano._chave_lixo(chave) is lixo


def test_cortar_valor(padrao, monkeypatch):
    monkeypatch.setattr(plano, "LIMITE_VALOR_STR", 10)
    assert plano._cortar_valor("abcdefghijklmnop") == "abcdefghij...[+6 caracteres]"
    assert plano._cortar_valor("curto") == "curto"
    ja_cortado = "[imagem/base64 removido: 999 caracteres]"
    assert plano._cortar_valor(ja_cortado) == ja_cortado
    assert plano._cortar_valor(12345678901234) == 12345678901234


def test_enxugar_mantem_eventos_e_tira_lixo(padrao):
    objeto = {
        "sections": [{
            "titulo": "Home",
            "dom": "<div>...</div>",
            "screenshot_url": "https://img",
            "Posição": {"x": 1},
            "vazio": {},
            "lista_vazia": [],
            "rich_texts": [{"text": "Observações: disparar só no clique"}],
            "sem_obs": {"rich_texts": []},
            "evento": {"event": "clique", "label": "entrar", "css_path": "div > a"},
            "itens": [{"html": "<a>"}, {"nome": "ok"}, []],
        }],
    }
    reduzido, aplicado = plano.enxugar_plano(objeto)
    assert aplicado is True
    assert reduzido == {
        "sections": [{
            "titulo": "Home",
            "observacoes": "disparar só no clique",
            "evento": {"event": "clique", "label": "entrar"},
            "itens": [{"nome": "ok"}],
        }],
    }


def test_enxugar_que_apagaria_tudo_devolve_o_original_cortado(padrao, monkeypatch):
    monkeypatch.setattr(plano, "LIMITE_VALOR_STR", 10)
    objeto = {"dom": "a" * 15, "nodes": ["b" * 15, 3, {"html": "c"}]}
    assert plano.enxugar_plano(objeto) == (
        {"dom": "aaaaaaaaaa...[+5 caracteres]",
         "nodes": ["bbbbbbbbbb...[+5 caracteres]", 3, {"html": "c"}]},
        False)


@pytest.mark.parametrize("vazio", [{}, []])
def test_enxugar_objeto_vazio(padrao, vazio):
    assert plano.enxugar_plano(vazio) == (vazio, True)


# ---- montar_mensagem -------------------------------------------------------------

def test_mensagem_so_com_texto(padrao):
    assert plano.montar_mensagem("  Audite isto  ", None) == "Audite isto"


@pytest.mark.parametrize("texto, anexo", [(None, None), ("   ", ""), ("", None)])
def test_mensagem_vazia_e_recusada(padrao, texto, anexo):
    with pytest.raises(ValueError, match="Digite uma mensagem"):
        plano.montar_mensagem(texto, anexo)


def test_anexo_que_nao_e_json_e_recusado(padrao):
    with pytest.raises(ValueError, match="não é um JSON válido"):
        plano.montar_mensagem("oi", "{quebrado")


def test_mensagem_com_texto_e_json_minificado(padrao, capsys):
    msg = plano.montar_mensagem("Audite", '{"event": "clique", "label": "ação"}')
    assert msg == 'Audite\n\n{"event":"clique","label":"ação"}'
    assert capsys.readouterr().out == ""          # nada removido, nada a avisar


def test_mensagem_sem_texto_usa_instrucao_padrao_e_limpa_o_plano(padrao, capsys):
    anexo = json.dumps({"event": "clique", "print_b64": "abc", "dom": "x" * 50})
    partes = plano.montar_mensagem("", anexo).split("\n\n")
    assert partes == ["Audite o seguinte plano de tagueamento:", '{"event":"clique"}']
    saida = capsys.readouterr().out
    assert "[limpeza] 1 campo(s) de imagem/base64 removido(s) (-3 caracteres)" in saida
    assert "[enxugar] plano reduzido de" in saida


def test_mensagem_sem_minificar_e_so_tabela(padrao, monkeypatch):
    monkeypatch.setattr(plano, "MINIFICAR_JSON", False)
    monkeypatch.setattr(plano, "SOMENTE_TABELA", True)
    partes = plano.montar_mensagem("Audite", '{"event": "clique"}').split("\n\n")
    assert partes[1] == '{\n  "event": "clique"\n}'
    assert partes[2].startswith("Responda somente com a auditoria em tabela")


def test_mensagem_sem_limpeza_e_com_sanitizacao(padrao, monkeypatch, capsys):
    for nome in ("REMOVER_CAMPOS_BASE64", "ENXUGAR_PLANO"):
        monkeypatch.setattr(plano, nome, False)
    monkeypatch.setattr(plano, "SANITIZAR_CHAVES_DUPLAS", True)
    msg = plano.montar_mensagem(None, '{"dom": "{{valor}}", "foto_b64": "abc"}')
    assert msg.endswith('{"dom":"{ {valor} }","foto_b64":"abc"}')
    assert capsys.readouterr().out == ""
