"""Limites técnicos do GA4 contados em código (nome/valor/quantidade)."""
import json

import pytest

from tagauditor import limites
from tagauditor.limites import ALERTA, ERRO


@pytest.fixture(autouse=True)
def limites_oficiais(monkeypatch):
    """Números do GA4 fixos no teste (independente do config.py do momento)."""
    for nome, valor in {
        "VALIDAR_LIMITES_GA4": True, "LIMITE_NOME_EVENTO": 40,
        "LIMITE_NOME_PARAMETRO": 40, "LIMITE_VALOR_PARAMETRO": 100,
        "LIMITE_PARAMS_POR_EVENTO": 25, "LIMITE_NOME_USER_PROPERTY": 24,
        "LIMITE_VALOR_USER_PROPERTY": 36, "LIMITE_USER_PROPERTIES": 25,
    }.items():
        monkeypatch.setattr(limites, nome, valor)


def _por_parametro(achados):
    return {a["parametro"]: a for a in achados}


# ---- descoberta dos eventos -----------------------------------------------------

def test_formato_confluence_com_code_json():
    plano = {"sections": [{"titulo": "Home", "code_json": {"event": "e" * 41}}]}
    achados, total = limites.validar_limites(plano)
    assert total == 1
    assert achados[0]["evento"] == "#1 > " + "e" * 41
    assert achados[0]["localizacao"] == "raiz"
    assert achados[0]["problema"] == "{} Nome do evento com 41 caracteres (limite: 40)".format(ERRO)
    assert "excesso: 1 caractere(s)" in achados[0]["correcao"]


def test_nome_de_evento_no_limite_passa():
    assert limites.validar_limites({"event": "e" * 40}) == ([], 1)


def test_rotulo_usa_o_caminho_e_as_marcas_da_lista():
    plano = {
        "Tela   Home": {"Botão\nEntrar": {"event": "a" * 41}},
        "eventos": [
            {"id": 7, "event": "b" * 41},
            {"ordem": " ", "numero": "9", "event": "c" * 41},
            {"event": "d" * 41},
            "texto solto",
        ],
        "code_json": "não é dict",
    }
    achados, total = limites.validar_limites(plano)
    assert total == 4
    assert [a["evento"] for a in achados] == [
        "Tela Home > Botão Entrar > " + "a" * 41,
        "#7 > " + "b" * 41,
        "#9 > " + "c" * 41,
        "#3 > " + "d" * 41,
    ]


def test_parte_vazia_nao_entra_no_rotulo():
    plano = {"Home": {"   ": {"event": "", "p" * 41: "x"}}}
    achados, total = limites.validar_limites(plano)
    assert total == 1
    assert [a["evento"] for a in achados] == ["Home"]


def test_json_profundo_demais_e_ignorado():
    fundo = {"event": "x" * 41}
    for _ in range(20):
        fundo = {"nivel": fundo}
    assert limites.validar_limites(fundo) == ([], 0)


def test_parametro_profundo_demais_nao_e_contado():
    fundo = {"p" * 41: "v"}
    for _ in range(20):
        fundo = {"nivel": fundo}
    assert limites.validar_limites({"event": "clique", "dados": fundo}) == ([], 1)


# ---- parâmetros do evento --------------------------------------------------------

def test_nome_e_valor_de_parametro():
    plano = {
        "event": "clique",
        "p" * 41: "x",
        "label": "v" * 101,
        "page_location": "https://site/" + "u" * 90,
        "numero": 12345,
        "ativo": True,
        "dinamico": "{{" + "x" * 200 + "}}",
        "runtime": "${" + "y" * 200 + "}",
        "_meta": "z" * 500,
        "event_data": {"items": [{"item_name": "n" * 101}]},
    }
    achados, total = limites.validar_limites(plano)
    assert total == 1
    por = _por_parametro(achados)
    assert set(por) == {"p" * 41, "label", "page_location", "item_name"}

    assert por["p" * 41]["problema"] == \
        "{} Nome do parâmetro com 41 caracteres (limite: 40)".format(ERRO)
    assert "descarta o parâmetro" in por["p" * 41]["correcao"]

    assert por["label"]["problema"] == \
        "{} Valor do parâmetro com 101 caracteres (limite: 100)".format(ERRO)
    assert "trunca o restante" in por["label"]["correcao"]
    assert "utm" not in por["label"]["correcao"]
    assert "utm" in por["page_location"]["correcao"]

    assert por["item_name"]["localizacao"] == "event_data.items[0].item_name"
    assert por["label"]["localizacao"] == "raiz"


def test_parametros_demais_no_evento():
    plano = {"event": "x"}
    plano.update({"p{}".format(i): i for i in range(26)})
    achados, _ = limites.validar_limites(plano)
    assert len(achados) == 1
    assert achados[0]["parametro"] == "(evento inteiro)"
    assert achados[0]["valor"] == "26 parâmetros"
    assert achados[0]["problema"] == "{} 26 parâmetros no evento (limite: 25)".format(ERRO)
    assert "Remover ou agrupar 1 parâmetro(s)" in achados[0]["correcao"]


def test_25_parametros_passam():
    plano = {"event": "x"}
    plano.update({"p{}".format(i): i for i in range(25)})
    assert limites.validar_limites(plano) == ([], 1)


def test_nome_de_evento_dinamico_nao_e_contado():
    achados, _ = limites.validar_limites({"event": "{{nome_" + "x" * 50 + "}}", "label": "v" * 101})
    assert [a["evento"] for a in achados] == ["evento"]


# ---- escopo de usuário ---------------------------------------------------------

def test_user_property_tem_limites_proprios_e_user_id_nao_conta():
    plano = {
        "event": "login",
        "user": {"u" * 25: "a", "tipo_cliente": "c" * 37, "user_id": "d" * 101,
                 "segmento": "s" * 36},
    }
    achados, _ = limites.validar_limites(plano)
    por = _por_parametro(achados)
    assert set(por) == {"u" * 25, "tipo_cliente", "user_id"}
    assert por["u" * 25]["problema"] == \
        "{} Nome de user property com 25 caracteres (limite: 24)".format(ALERTA)
    assert "não envia a user property" in por["u" * 25]["correcao"]
    assert por["tipo_cliente"]["problema"] == \
        "{} Valor de user property com 37 caracteres (limite: 36)".format(ALERTA)
    assert por["tipo_cliente"]["localizacao"] == "user.tipo_cliente"
    assert por["user_id"]["problema"] == \
        "{} Valor do parâmetro com 101 caracteres (limite: 100)".format(ERRO)


def test_user_properties_demais_no_plano_inteiro():
    plano = [
        {"event": "a", "user": {"up{}".format(i): "v" for i in range(13)}},
        {"event": "b", "up": {"up{}".format(i): "v" for i in range(13, 26)}},
    ]
    achados, total = limites.validar_limites(plano)
    assert total == 2
    assert len(achados) == 1
    assert achados[0]["evento"] == "plano inteiro"
    assert achados[0]["valor"] == "26 nomes distintos"
    assert achados[0]["problema"].startswith(ALERTA + " 26 user properties distintas")


# ---- tabela ----------------------------------------------------------------------

def test_tabela_agrupa_repeticoes_e_poe_erros_primeiro():
    plano = [
        {"event": "login", "user": {"tipo": "c" * 37}},
        {"event": "a" * 70, "label": "v|" * 51},
        {"event": "b", "label": "v|" * 51},
    ]
    achados, _ = limites.validar_limites(plano)
    linhas = limites._tabela(achados).split("\n")
    assert linhas[0] == "### Limites técnicos do GA4 — validação automática"
    assert linhas[2].startswith("| Evento | Localização |")
    corpo = linhas[4:]
    assert len(corpo) == 3
    # o 1o evento (nome longo demais) primeiro, depois o label repetido, por fim o alerta
    assert corpo[0].startswith("| #2 > " + "a" * 55 + "… |")
    assert corpo[1].startswith("| #2 > " + "a" * 55 + "… (+1 evento(s)) | raiz | label |")
    assert "`" + "v\\|" * 30 + "…`" in corpo[1]
    assert corpo[2].startswith("| #1 > login | user.tipo | tipo |")
    assert ALERTA in corpo[2]


# ---- remoção dos apontamentos de tamanho da IA -----------------------------------

_IDX = {"parametro": 0, "valor": 1, "problema": 2, "correcao": 3}


@pytest.mark.parametrize("problema, correcao, esperado", [
    ("", "", False),
    ("OK", "OK", False),
    ("Sem ajustes", "", False),
    ("Excede 100 caracteres", "Reduzir", True),
    ("Valor truncado por exceder o limite", "", True),
    ("Acima do limite técnico do GA4", "", True),
    ("Nome muito longo", "Ultrapassa o tamanho permitido", True),
    ("Sem caracteres especiais", "Usar hífen", False),
    ("Usar minúsculas", "clique", False),
])
def test_linha_de_limite(problema, correcao, esperado):
    assert limites._linha_de_limite(["label", "x", problema, correcao], _IDX) is esperado


def test_linha_curta_sem_coluna_de_correcao():
    assert limites._linha_de_limite(["label"], {"problema": 0, "correcao": 5}) is False
    assert limites._linha_de_limite(["40 caracteres"], {"problema": 0}) is True


_RESPOSTA_IA = "\n".join([
    "Resumo da auditoria",
    "",
    "| Evento | Parâmetro | Valor | Problema | Correção |",
    "|---|---|---|---|---|",
    "| clique | label | abc | Excede 100 caracteres | Reduzir |",
    "| clique | action | Clique | Usar minúsculas | clique |",
    "",
    "",
])


def test_troca_apontamentos_da_ia_pela_tabela_contada(capsys):
    plano = json.dumps({"event": "clique", "label": "v" * 101})
    texto = limites.aplicar_limites_ga4(_RESPOSTA_IA, plano)
    assert "Excede 100 caracteres" not in texto
    assert "| clique | action | Clique | Usar minúsculas | clique |" in texto
    assert "| clique | action | Clique | Usar minúsculas | clique |\n\n### Limites técnicos" in texto
    assert texto.rstrip().endswith("|")
    saida = capsys.readouterr().out
    assert "1 apontamento(s) de tamanho da IA substituído(s)" in saida
    assert "1 violação(ões) de limite do GA4 em 1 evento(s)" in saida


def test_sem_violacao_anexa_o_selo_de_conferido(capsys):
    texto = limites.aplicar_limites_ga4("Tudo certo.\n", json.dumps([{"event": "a"}, {"event": "b"}]))
    assert texto.startswith("Tudo certo.\n\n> ✅ **Limites técnicos do GA4:** 2 evento(s)")
    assert "nenhum limite do GA4 estourado" in capsys.readouterr().out


def test_tabela_da_ia_sem_apontamento_de_tamanho_fica_igual(capsys):
    resposta = "| Evento | Problema |\n|---|---|\n| clique | Usar minúsculas |"
    texto = limites.aplicar_limites_ga4(resposta, '{"event": "clique"}')
    assert texto.startswith(resposta + "\n\n> ✅")
    assert "substituído" not in capsys.readouterr().out


def test_plano_sem_eventos_nao_mexe_na_resposta(capsys):
    assert limites.aplicar_limites_ga4(_RESPOSTA_IA, '{"titulo": "sem eventos"}') == _RESPOSTA_IA
    assert "nenhum evento reconhecido" in capsys.readouterr().out


@pytest.mark.parametrize("texto, anexo", [
    (_RESPOSTA_IA, None),
    (_RESPOSTA_IA, ""),
    (_RESPOSTA_IA, "{json quebrado"),
    (None, '{"event": "x"}'),
    (123, '{"event": "x"}'),
])
def test_sem_plano_valido_a_resposta_fica_como_esta(texto, anexo):
    assert limites.aplicar_limites_ga4(texto, anexo) == texto


def test_anexo_de_tipo_errado_e_ignorado():
    assert limites.aplicar_limites_ga4("x", 12345) == "x"


def test_validacao_desligada(monkeypatch):
    monkeypatch.setattr(limites, "VALIDAR_LIMITES_GA4", False)
    plano = json.dumps({"event": "e" * 99})
    assert limites.aplicar_limites_ga4(_RESPOSTA_IA, plano) == _RESPOSTA_IA
