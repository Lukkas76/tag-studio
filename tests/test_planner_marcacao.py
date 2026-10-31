"""Tag Planner: bloco de coordenadas (```marcacao) — extração, JSON tolerante,
normalização dos elementos, ajuste às telas enviadas e a 2ª etapa (marcação
tela a tela, com a IA simulada)."""
import json
import threading

import pytest

from tagauditor import planner


def _el(ident="T1", imagem=1, parte=1, classe="OBRIGATÓRIO", decisao="TAGUEAR", **extra):
    el = {"id": ident, "imagem": imagem, "parte": parte, "texto": "Solicitar",
          "classificacao": classe, "decisao": decisao, "legenda": ident + " — " + decisao,
          "figma_ref": None, "bbox_px": {"x": 10, "y": 20, "largura": 30, "altura": 40},
          "bbox_normalizada": {"x": 0.1, "y": 0.1, "largura": 0.3, "altura": 0.2}}
    el.update(extra)
    return el


def _bloco(*elementos, cerca="```", rotulo="marcacao", **extra):
    corpo = {"elementos": list(elementos)}
    corpo.update(extra)
    return "{}{}\n{}\n{}".format(cerca, rotulo, json.dumps(corpo, ensure_ascii=False), cerca)


ESPERADO_T1 = {"id": "T1", "imagem": 1, "parte": 1, "texto": "Solicitar",
               "classificacao": "OBRIGATORIO", "decisao": "TAGUEAR", "legenda": "T1 — TAGUEAR",
               "figma_ref": None, "bbox_px": {"x": 10, "y": 20, "largura": 30, "altura": 40},
               "bbox_normalizada": {"x": 0.1, "y": 0.1, "largura": 0.3, "altura": 0.2}}


# ---- extrair_marcacao -------------------------------------------------------

def test_resposta_que_nao_e_texto():
    assert planner.extrair_marcacao(None) == ("", None, None)


def test_resposta_sem_bloco_nao_e_erro():
    assert planner.extrair_marcacao("## KPI\nTaxa de conversão.") == (
        "## KPI\nTaxa de conversão.", None, None)


def test_bloco_marcacao_sai_do_texto_e_vira_elementos():
    texto = "## KPI\r\nTaxa.\r\n\r\n" + _bloco(_el()).replace("\n", "\r\n") + "\r\n"
    limpo, marcacao, erro = planner.extrair_marcacao(texto)
    assert limpo == "## KPI\nTaxa."
    assert marcacao == {"elementos": [ESPERADO_T1]}
    assert erro is None


def test_bloco_com_til_e_cedilha():
    texto = "Análise\n" + _bloco(_el(), cerca="~~~~", rotulo=" Marcação")
    limpo, marcacao, _erro = planner.extrair_marcacao(texto)
    assert limpo == "Análise"
    assert marcacao["elementos"][0]["id"] == "T1"


def test_bloco_marcacao_no_meio_da_linha():
    texto = "Veja as coordenadas: ```marcacao {\"elementos\": [" + json.dumps(_el()) + "]}``` fim"
    limpo, marcacao, _erro = planner.extrair_marcacao(texto)
    assert limpo == "Veja as coordenadas:  fim"
    assert marcacao["elementos"][0]["id"] == "T1"


def test_sem_bloco_marcacao_usa_o_ultimo_json_com_coordenadas():
    texto = ("```json\n{\"evento\": \"clique\"}\n```\n\n"
             "```json\n" + json.dumps({"elementos": [_el("T8")]}) + "\n```\n\n"
             "```JSON\n" + json.dumps({"elementos": [_el("T9")]}) + "\n```")
    limpo, marcacao, erro = planner.extrair_marcacao(texto)
    assert [e["id"] for e in marcacao["elementos"]] == ["T9"]
    assert erro is None
    assert '{"evento": "clique"}' in limpo and "T8" in limpo and "T9" not in limpo


def test_varios_blocos_vale_o_ultimo_que_der_certo():
    texto = _bloco(_el("T1")) + "\nTexto\n" + _bloco(_el("T2")) + "\n```marcacao\n{quebrado\n```"
    limpo, marcacao, erro = planner.extrair_marcacao(texto)
    assert limpo == "Texto"
    assert [e["id"] for e in marcacao["elementos"]] == ["T2"]
    assert erro is None


def test_bloco_invalido_traz_o_motivo():
    limpo, marcacao, erro = planner.extrair_marcacao("Texto\n```marcacao\n{\"elementos\": [}\n```")
    assert (limpo, marcacao) == ("Texto", None)
    assert erro.startswith("O bloco de coordenadas (marcacao) não é um JSON válido:")


def test_bloco_vazio():
    assert planner.extrair_marcacao("Texto\n```marcacao\n\n```") == (
        "Texto", None, "O bloco de coordenadas (marcacao) veio vazio.")


def test_bloco_cortado_pelo_limite_de_tokens_sai_do_texto():
    limpo, marcacao, erro = planner.extrair_marcacao("Texto\n```marcacao\n{\"elementos\": [{\"id\"")
    assert (limpo, marcacao) == ("Texto", None)
    assert "não é um JSON válido" in erro


def test_bloco_com_cerca_de_fechamento_maior():
    limpo, marcacao, _erro = planner.extrair_marcacao(
        "A\n```marcacao\n" + json.dumps({"elementos": [_el()]}) + "\n`````\nB")
    assert limpo == "A\n\nB"
    assert marcacao["elementos"][0]["id"] == "T1"


def test_bloco_sem_lista_de_elementos():
    _limpo, marcacao, erro = planner.extrair_marcacao("```marcacao\n{\"foo\": 1}\n```")
    assert marcacao is None
    assert erro == "O bloco de coordenadas (marcacao) não traz a lista \"elementos\"."


def test_bloco_sem_nenhuma_posicao_valida():
    _limpo, marcacao, erro = planner.extrair_marcacao(
        _bloco({"id": "T1", "bbox_px": {"x": 0, "y": 0, "largura": 0, "altura": 0}}))
    assert marcacao is None
    assert erro.startswith("Nenhum elemento do bloco de coordenadas")


# ---- Limpeza das cercas que sobram ------------------------------------------

@pytest.mark.parametrize("texto, esperado", [
    ("a\n\n\n\nb", "a\n\nb"),
    ("a\n```\n\n```\nb", "a\nb"),
    ("a\n```js\ncodigo\n```\nb", "a\n```js\ncodigo\n```\nb"),
    ("a\n```x`y\nb", "a\n```x`y\nb"),
    ("texto\n```\n\n", "texto"),
    ("texto\n```\nsem fechar", "texto\n```\nsem fechar"),
    ("a\n~~~\n```\n~~~", "a\n~~~\n```\n~~~"),
    ("\n\na", "a"),
])
def test_limpar_cercas(texto, esperado):
    assert planner._limpar_cercas(texto) == esperado


def test_remover_trechos_sobrepostos():
    assert planner._remover_trechos("0123456789", [(5, 8, ""), (2, 4, ""), (3, 6, "")]) == "0189"


# ---- JSON tolerante ---------------------------------------------------------

def test_json_com_os_deslizes_comuns_de_modelo():
    corpo = """Segue o bloco:
    {
      // comentário de linha
      "elementos": [
        {"id": "T1", "url": "https://x.com/a, b]", "ok": True, "ref": None, /* bloco */
         "aspas": "diz \\"oi\\"", "falso": False,},
      ],
    }
    Obrigado!"""
    objeto, erro = planner._carregar_json_tolerante(corpo)
    assert erro is None
    assert objeto == {"elementos": [{"id": "T1", "url": "https://x.com/a, b]", "ok": True,
                                     "ref": None, "aspas": 'diz "oi"', "falso": False}]}


def test_json_com_aspas_tipograficas():
    objeto, erro = planner._carregar_json_tolerante("{“elementos”: [], “x”: True}")
    assert (objeto, erro) == ({"elementos": [], "x": True}, None)


def test_json_sem_chaves_e_com_virgula_no_fim():
    assert planner._carregar_json_tolerante("1,") == (1, None)


def test_json_vazio_ou_invalido():
    assert planner._carregar_json_tolerante(None) == (
        None, "O bloco de coordenadas (marcacao) veio vazio.")
    objeto, erro = planner._carregar_json_tolerante("texto sem json")
    assert objeto is None and "não é um JSON válido" in erro


def test_comentario_sem_fim_vai_ate_o_fim_do_texto():
    assert planner._varrer_fora_de_strings("[1] // fim", planner._tratar_comentario) == "[1] "
    assert planner._varrer_fora_de_strings("[1] /* aberto", planner._tratar_comentario) == "[1] "
    assert planner._varrer_fora_de_strings('"a // b" /* c */ 2',
                                           planner._tratar_comentario) == '"a // b"  2'


def test_recortar_json_sem_chaves():
    assert planner._recortar_json("texto") == "texto"
    assert planner._recortar_json("} antes {") == "} antes {"
    assert planner._recortar_json("ok: [1, 2] fim") == "[1, 2]"


# ---- Normalização dos elementos ---------------------------------------------

@pytest.mark.parametrize("objeto, esperado", [
    ([1, 2], [1, 2]),
    ({"items": [3]}, [3]),
    ({"elements": [4], "elementos": "não é lista"}, [4]),
    ({"marcacao": {"elementos": [5]}}, [5]),
    ({"marcação": [6]}, [6]),
    ({"marcacao": {"marcacao": {"marcacao": {"elementos": [7]}}}}, None),
    ({"bbox_px": [1, 2, 3, 4]}, [{"bbox_px": [1, 2, 3, 4]}]),
    ({"x": 1}, None),
    ("texto", None),
])
def test_lista_de_elementos(objeto, esperado):
    assert planner._lista_de_elementos(objeto) == esperado


@pytest.mark.parametrize("valor, esperado", [
    ([1, 2, 3, 4], (1, 2, 3, 4)),
    ([1, 2, 3], None),
    (["1", "x", 3, 4], None),
    ({"left": 10, "top": 20, "width": 30, "height": 40}, (10, 20, 30, 40)),
    ({"x1": 10, "y1": 20, "x2": 40, "y2": 60}, (10, 20, 30, 40)),
    ({"X": 10, "Y": 20, "Right": 15, "Bottom": 25}, (10, 20, 5, 5)),
    ({"x": "45%", "y": "10%", "l": "5%", "a": "5,5%"}, (45, 10, 5, 5.5)),
    ({"x": 10, "y": 20}, None),
    ({"y": 20, "w": 5, "h": 5}, None),
    ({"x": -5, "y": -2, "w": 20, "h": 10}, (0, 0, 15, 8)),
    ({"x": -30, "y": 0, "w": 20, "h": 10}, None),
    ({"x": 0, "y": 0, "largura": 0, "altura": 0}, None),
    ("10,20,30,40", None),
])
def test_caixa_aceita_as_variacoes_dos_modelos(valor, esperado):
    caixa = planner._bbox(valor)
    if esperado is None:
        assert caixa is None
    else:
        assert caixa == dict(zip(("x", "y", "largura", "altura"), esperado))


@pytest.mark.parametrize("classificacao, decisao, classe", [
    ("OBRIGATÓRIO", "TAGUEAR", "OBRIGATORIO"),
    ("OPCIONAL/DIAGNÓSTICO", None, "OPCIONAL"),
    ("diagnóstico", None, "OPCIONAL"),
    ("NÃO TAGUEAR", None, "NAO_TAGUEAR"),
    ("descartar", None, "NAO_TAGUEAR"),
    ("ignorar", None, "NAO_TAGUEAR"),
    ("NECESSITA CONTEXTO", None, "NECESSITA_CONTEXTO"),
    (None, "A VALIDAR", "NECESSITA_CONTEXTO"),
    (None, "TAGUEAR", "OBRIGATORIO"),
    ("xyz", "abc", "NECESSITA_CONTEXTO"),
    (None, None, "NECESSITA_CONTEXTO"),
])
def test_classificacao(classificacao, decisao, classe):
    assert planner._classificar(classificacao, decisao) == classe


@pytest.mark.parametrize("valor, classe, decisao", [
    ("não taguear", "OBRIGATORIO", "NÃO TAGUEAR"),
    ("descartado", "OBRIGATORIO", "NÃO TAGUEAR"),
    ("validar", "OBRIGATORIO", "A VALIDAR"),
    ("falta contexto", "OBRIGATORIO", "A VALIDAR"),
    ("opcional", "OBRIGATORIO", "OPCIONAL"),
    ("diagnóstico", "OBRIGATORIO", "OPCIONAL"),
    ("taguear", "OPCIONAL", "TAGUEAR"),
    ("obrigatório", "OPCIONAL", "TAGUEAR"),
    (None, "OPCIONAL", "OPCIONAL"),
    (None, "NAO_TAGUEAR", "NÃO TAGUEAR"),
    ("???", "NECESSITA_CONTEXTO", "A VALIDAR"),
])
def test_decisao(valor, classe, decisao):
    assert planner._decisao(valor, classe) == decisao


@pytest.mark.parametrize("valor, esperado", [
    (None, None), (True, None), (12, "F12"), (0, None), ("f007", "F7"), ("12", "F12"),
    ("F1234567", None), ("abc", None), (1.5, None),
])
def test_codigo_da_camada_do_figma(valor, esperado):
    assert planner._ref_figma(valor) == esperado


@pytest.mark.parametrize("valor, esperado", [
    ("Imagem 2", 2), ("abc", 1), (2.0, 2), (None, 1), (0, 1), ("1000", 1), (3, 3),
])
def test_indice_de_imagem_e_parte(valor, esperado):
    assert planner._indice(valor) == esperado


def test_elemento_com_caixa_generica_normalizada():
    elemento = planner._normalizar_elemento(
        {"ID": "T7", "Text": "Ver", "bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.1},
         "Image": "Imagem 2", "part": 2}, 1)
    assert elemento == {"id": "T7", "imagem": 2, "parte": 2, "texto": "Ver",
                        "classificacao": "NECESSITA_CONTEXTO", "decisao": "A VALIDAR",
                        "legenda": "T7 — A VALIDAR: Ver", "figma_ref": None, "bbox_px": None,
                        "bbox_normalizada": {"x": 0.1, "y": 0.2, "largura": 0.3, "altura": 0.1}}


def test_elemento_com_caixa_generica_em_pixels():
    elemento = planner._normalizar_elemento({"bbox": [10, 20, 30, 40], "decision": "TAGUEAR"}, 3)
    assert elemento["id"] == "T3"
    assert elemento["bbox_px"] == {"x": 10, "y": 20, "largura": 30, "altura": 40}
    assert elemento["bbox_normalizada"] is None
    assert elemento["classificacao"] == "OBRIGATORIO"


def test_elemento_so_com_codigo_do_figma():
    elemento = planner._normalizar_elemento({"figma_ref": "F3", "caption": ""}, 1)
    assert elemento["figma_ref"] == "F3"
    assert elemento["legenda"] == "T1 — A VALIDAR: elemento"
    assert elemento["bbox_px"] is None and elemento["bbox_normalizada"] is None


@pytest.mark.parametrize("bruto", ["texto", {"texto": "sem caixa"}, {"bbox": "x"}])
def test_elemento_sem_onde_desenhar_e_descartado(bruto):
    assert planner._normalizar_elemento(bruto, 1) is None


@pytest.mark.parametrize("bruto, esperado", [
    ("x", False), ({"Sem Elementos": "Sim"}, True), ({"no_elements": True}, True),
    ({"sem_elementos": "não"}, False), ({"sem_elementos": 1}, False), ({}, False),
])
def test_item_sem_elementos(bruto, esperado):
    assert planner._eh_sem_elementos(bruto) is esperado


def test_motivo_de_imagem_sem_elementos():
    assert planner._item_sem_elementos({"Image": "Imagem 3", "Reason": " carregando "}) == {
        "imagem": 3, "motivo": "carregando"}
    assert planner._item_sem_elementos({}, imagem=2) == {
        "imagem": 2, "motivo": "o modelo indicou que não há elementos a marcar nesta tela"}
    assert planner._item_sem_elementos("x")["imagem"] == 1


def test_marcacao_com_elementos_e_imagens_sem_elementos():
    marcacao, erro = planner._normalizar_marcacao({
        "elementos": [_el(), {"imagem": 2, "sem_elementos": True, "motivo": "vazia"}, "lixo"],
        "sem_elementos": [{"imagem": 3, "motivo": "carregando"}, "x"],
    })
    assert erro is None
    assert marcacao == {"elementos": [ESPERADO_T1],
                        "sem_elementos": [{"imagem": 3, "motivo": "carregando"},
                                          {"imagem": 2, "motivo": "vazia"}]}


def test_marcacao_so_com_sem_elementos_no_topo():
    marcacao, erro = planner._normalizar_marcacao(
        {"imagem": 2, "sem_elementos": True, "motivo": "só texto"})
    assert (marcacao, erro) == ({"elementos": [], "sem_elementos": [
        {"imagem": 2, "motivo": "só texto"}]}, None)


def test_marcacao_corta_no_maximo_de_elementos(monkeypatch):
    monkeypatch.setattr(planner, "MAX_ELEMENTOS_MARCACAO", 2)
    marcacao, _erro = planner._normalizar_marcacao([_el("T1"), _el("T2"), _el("T3")])
    assert [e["id"] for e in marcacao["elementos"]] == ["T1", "T2"]


# ---- Ajuste às telas enviadas -----------------------------------------------

def _telas(*partes_por_tela):
    return [{"indice": i, "nome": "Tela {}".format(i), "partes": [{}] * n}
            for i, n in enumerate(partes_por_tela, 1)]


def test_ajuste_descarta_imagem_inexistente_e_corrige_a_parte():
    marcacao = {"elementos": [dict(ESPERADO_T1, parte=5), dict(ESPERADO_T1, id="T2", imagem=2,
                                                               parte=3),
                              dict(ESPERADO_T1, id="T3", imagem=4)],
                "sem_elementos": [{"imagem": 3, "motivo": "a"}, {"imagem": 3, "motivo": "dup"},
                                  {"imagem": 1, "motivo": "tem elemento"},
                                  {"imagem": 9, "motivo": "não existe"}]}
    ajustada, descartados = planner._ajustar_marcacao_as_telas(marcacao, _telas(2, 0, 1))
    assert descartados == 1
    assert [(e["id"], e["imagem"], e["parte"]) for e in ajustada["elementos"]] == [
        ("T1", 1, 2), ("T2", 2, 1)]
    assert ajustada["sem_elementos"] == [{"imagem": 3, "motivo": "a"}]


def test_ajuste_sem_nada_que_sobre():
    assert planner._ajustar_marcacao_as_telas(None, _telas(1)) == (None, 0)
    marcacao = {"elementos": [dict(ESPERADO_T1, imagem=5)]}
    assert planner._ajustar_marcacao_as_telas(marcacao, _telas(1)) == (None, 1)
    marcacao = {"elementos": [ESPERADO_T1.copy()]}
    assert planner._ajustar_marcacao_as_telas(marcacao, _telas(1)) == (
        {"elementos": [ESPERADO_T1]}, 0)


# ---- 2ª etapa: marcação tela a tela -----------------------------------------

class IAFalsa:
    """Substitui bridge.gerar_texto: responde pelo rótulo da chamada
    ("planner-marcacao-2"...). Resposta = texto, (texto, uso), exceção ou
    lista delas (uma por chamada)."""

    def __init__(self, respostas):
        self.respostas = {k: (list(v) if isinstance(v, list) else v)
                          for k, v in respostas.items()}
        self.chamadas = []
        self._trava = threading.Lock()

    def __call__(self, mensagens, system_prompt, **kw):
        with self._trava:
            self.chamadas.append(dict(kw, mensagens=mensagens, system_prompt=system_prompt))
            resposta = self.respostas[kw["rotulo"]]
            if isinstance(resposta, list):
                resposta = resposta.pop(0)
        if isinstance(resposta, BaseException):
            raise resposta
        if isinstance(resposta, tuple):
            return resposta
        return resposta, {"entrada": 100, "saida": 10, "total": 110, "fonte": "api"}

    def chamada(self, rotulo):
        return [c for c in self.chamadas if c["rotulo"] == rotulo][0]


@pytest.fixture
def ia(monkeypatch):
    def instalar(respostas):
        falsa = IAFalsa(respostas)
        monkeypatch.setattr(planner.bridge, "gerar_texto", falsa)
        return falsa
    monkeypatch.setattr(planner, "PLANNER_DETALHE_IMAGEM", "high")
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_REASONING_EFFORT", None)
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_MAX_TOKENS", 1234)
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_PARALELAS", 2)
    return instalar


def _parte(n=1):
    return {"parte": n, "mime": "image/png", "base64": "QUJD", "largura": 100, "altura": 200,
            "faixa": 0, "recorte": {"y": 0, "altura": 1}}


def _tela(indice, n_partes=1, inventario=None):
    return {"indice": indice, "nome": "Tela {}".format(indice), "origem": "upload",
            "largura": 100, "altura": 200,
            "figma": {"node_id": "", "file_key": "", "inventario": inventario or []},
            "partes": [_parte(n) for n in range(1, n_partes + 1)]}


CONTEXTO = {"pergunta": "Quantos contratam?", "canal": ""}


def test_somar_usos():
    assert planner._somar_usos([{"entrada": 1, "saida": 2, "total": 3, "fonte": "api"}, None,
                                {"entrada": "4", "saida": None, "fonte": "estimativa"}]) == {
        "entrada": 5, "saida": 2, "total": 3, "fonte": "estimativa"}
    assert planner._somar_usos([{"fonte": "bridge"}, {"fonte": "api"}])["fonte"] == "api"
    assert planner._somar_usos([{"fonte": "bridge"}])["fonte"] == "bridge"
    assert planner._somar_usos([{}]) == {"entrada": 0, "saida": 0, "total": 0, "fonte": "api"}


def test_descrever_partes():
    assert planner._descrever_partes(1) == "tela inteira"
    assert planner._descrever_partes(0) == "tela inteira"
    assert planner._descrever_partes(3) == "3 partes"


def test_texto_da_marcacao_de_uma_tela(monkeypatch):
    monkeypatch.setattr(planner, "MAX_CHARS_ANALISE_MARCACAO", 10)
    inventario = [{"ref": "F1", "tipo": "TEXT", "nome": "", "texto": "Olá", "interativo": False,
                   "bbox": None}]
    telas = [_tela(1), _tela(2, 2, inventario)]
    rascunho = {"elementos": [ESPERADO_T1, dict(ESPERADO_T1, id="T2", imagem=2, texto="")]}
    texto = planner._texto_marcacao_tela(CONTEXTO, telas, 2, "x" * 50, rascunho, True)
    assert texto.startswith('## Marcação da Imagem 2 de 2: "Tela 2"\nEsta mensagem traz só a '
                            'Imagem 2 (2 partes). Use "imagem": 2')
    assert "**Canal:** " + planner.CANAL_NAO_INFORMADO in texto
    assert "Uma tentativa anterior não devolveu nenhum elemento" in texto
    assert ('- T1 · OBRIGATORIO · TAGUEAR · "Solicitar" · T1 — TAGUEAR (a análise situou na '
            "Imagem 1)") in texto
    assert '- T2 · OBRIGATORIO · TAGUEAR · "-" · T1 — TAGUEAR (nesta tela)' in texto
    assert "```\nxxxxxxxxxx\n[... texto cortado ...]\n```" in texto
    assert '## Inventário de camadas do Figma desta tela' in texto
    assert 'F1 | TEXT | texto: "Olá"' in texto
    assert texto.endswith("As imagens desta tela vêm a seguir.")


def test_texto_da_marcacao_sem_rascunho():
    texto = planner._texto_marcacao_tela({"pergunta": "P?", "canal": "Web"}, [_tela(1)], 1,
                                         None, None, False)
    assert "(a análise não trouxe a lista de coordenadas" in texto
    assert "**Canal:** Web" in texto
    assert "tentativa anterior" not in texto
    assert "Inventário" not in texto
    assert "(tela inteira)" in texto


def test_marcar_tela_manda_so_aquela_tela_e_corrige_imagem_e_parte(ia):
    falsa = ia({"planner-marcacao-2": "Ok\n" + _bloco(_el("T5", imagem=7, parte=9),
                                                      _el("T6", imagem=1, parte=0))})
    telas = [_tela(1), _tela(2, 2)]
    resultado = planner._marcar_tela(CONTEXTO, telas, 2, "análise", None)
    assert resultado["ok"] is True
    assert resultado["erro"] is None and resultado["motivo"] is None
    assert resultado["uso"]["total"] == 110
    assert [(e["id"], e["imagem"], e["parte"]) for e in resultado["elementos"]] == [
        ("T5", 2, 2), ("T6", 2, 1)]
    chamada = falsa.chamada("planner-marcacao-2")
    assert chamada["system_prompt"] == planner.INSTRUCOES_MARCACAO
    assert chamada["reasoning_effort"] == ""
    assert chamada["max_tokens"] == 1234
    assert chamada["tokens_extra_estimados"] == 2 * 34
    conteudo = chamada["mensagens"][0]["content"]
    assert [p["type"] for p in conteudo] == ["text", "text", "image_url", "text", "image_url"]
    assert conteudo[2]["image_url"]["detail"] == "high"


def test_marcar_tela_sem_detalhe_de_imagem(ia, monkeypatch):
    monkeypatch.setattr(planner, "PLANNER_DETALHE_IMAGEM", "")
    falsa = ia({"planner-marcacao-1": _bloco(_el())})
    planner._marcar_tela(CONTEXTO, [_tela(1)], 1, "análise", None)
    imagem = falsa.chamada("planner-marcacao-1")["mensagens"][0]["content"][2]["image_url"]
    assert "detail" not in imagem


def test_marcar_tela_com_motivo_de_tela_sem_elementos(ia):
    ia({"planner-marcacao-1": '```marcacao\n{"elementos": [], "sem_elementos": true, '
                              '"motivo": "tela de carregamento"}\n```'})
    resultado = planner._marcar_tela(CONTEXTO, [_tela(1)], 1, "análise", None)
    assert resultado["ok"] is True
    assert resultado["elementos"] == []
    assert resultado["motivo"] == "tela de carregamento"
    assert resultado["erro"] is None


def test_marcar_tela_nunca_levanta_quando_a_ia_falha(ia):
    ia({"planner-marcacao-1": RuntimeError("HTTP 503")})
    assert planner._marcar_tela(CONTEXTO, [_tela(1)], 1, "análise", None) == {
        "ok": False, "elementos": [], "motivo": None, "uso": None, "erro": "HTTP 503"}


@pytest.mark.parametrize("resposta, erro", [
    ((None, {"total": 1}), "sem bloco de coordenadas"),
    ("Não achei nada.", "sem bloco de coordenadas"),
    ("```marcacao\n{quebrado\n```", "não é um JSON válido"),
])
def test_marcar_tela_sem_marcacao_utilizavel(ia, resposta, erro):
    ia({"planner-marcacao-1": resposta})
    resultado = planner._marcar_tela(CONTEXTO, [_tela(1)], 1, "análise", None)
    assert resultado["ok"] is False
    assert resultado["elementos"] == []
    assert erro in resultado["erro"]


def test_rodar_marcacoes_sem_telas():
    assert planner._rodar_marcacoes([], CONTEXTO, []) == {}


def test_etapa_por_imagem_substitui_a_marcacao_da_analise(ia, monkeypatch, capsys):
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_POR_IMAGEM", True)
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_REFAZER", True)
    falsa = ia({
        "planner-marcacao-1": RuntimeError("HTTP 500"),          # falhou: fica a da análise
        "planner-marcacao-2": _bloco(_el("T2", imagem=2)),
        "planner-marcacao-3": '```marcacao\n{"elementos": [], "sem_elementos": [{"imagem": 3, '
                              '"motivo": "só texto"}]}\n```',
    })
    telas = [_tela(1), _tela(2), _tela(3), _tela(4, 0, [{"ref": "F1"}])]
    rascunho = {"elementos": [ESPERADO_T1, dict(ESPERADO_T1, id="T9", imagem=4),
                              dict(ESPERADO_T1, id="T8", imagem=3)],
                "sem_elementos": [{"imagem": 2, "motivo": "a análise achou vazia"}]}
    info = {}
    marcacao, usos = planner.marcar_tela_a_tela(CONTEXTO, telas, "análise", rascunho, info)
    assert [(e["id"], e["imagem"]) for e in marcacao["elementos"]] == [
        ("T1", 1), ("T2", 2), ("T9", 4)]
    assert marcacao["sem_elementos"] == [{"imagem": 3, "motivo": "só texto"}]
    assert len(usos) == 2
    assert info == {"marcacao_chamadas": 3, "marcacao_refeitas": 0, "marcacao_falhas": 1}
    assert sorted(c["rotulo"] for c in falsa.chamadas) == [
        "planner-marcacao-1", "planner-marcacao-2", "planner-marcacao-3"]
    assert "3 chamada(s), até 2 ao mesmo tempo" in capsys.readouterr().out


def test_tela_sem_marcacao_ganha_nova_tentativa(ia, monkeypatch, capsys):
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_POR_IMAGEM", False)
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_REFAZER", True)
    falsa = ia({"planner-marcacao-2": _bloco(_el("T2", imagem=2)),
                "planner-marcacao-3": RuntimeError("HTTP 500")})
    info = {}
    marcacao, usos = planner.marcar_tela_a_tela(
        CONTEXTO, [_tela(1), _tela(2), _tela(3)], "análise", {"elementos": [ESPERADO_T1]}, info)
    assert [e["id"] for e in marcacao["elementos"]] == ["T1", "T2"]
    assert "sem_elementos" not in marcacao
    assert len(usos) == 1
    assert info == {"marcacao_chamadas": 2, "marcacao_refeitas": 2, "marcacao_falhas": 1}
    texto = falsa.chamada("planner-marcacao-2")["mensagens"][0]["content"][0]["text"]
    assert "Uma tentativa anterior não devolveu nenhum elemento" in texto
    assert "Imagem(ns) sem marcação: 2, 3. Nova tentativa só delas." in capsys.readouterr().out


def test_tudo_falhou_e_sem_rascunho(ia, monkeypatch):
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_POR_IMAGEM", False)
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_REFAZER", True)
    ia({"planner-marcacao-1": RuntimeError("HTTP 500")})
    info = {}
    assert planner.marcar_tela_a_tela(CONTEXTO, [_tela(1)], "análise", None, info) == (None, [])
    assert info["marcacao_falhas"] == 1


def test_etapas_desligadas_devolvem_o_rascunho(ia, monkeypatch):
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_POR_IMAGEM", False)
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_REFAZER", False)
    monkeypatch.setattr(planner, "MAX_ELEMENTOS_MARCACAO", 2)
    falsa = ia({})
    rascunho = {"elementos": [dict(ESPERADO_T1, id="T{}".format(n)) for n in range(1, 4)],
                "sem_elementos": [{"imagem": 2, "motivo": "vazia"}]}
    info = {}
    marcacao, usos = planner.marcar_tela_a_tela(CONTEXTO, [_tela(1), _tela(2)], "a", rascunho,
                                                info)
    assert [e["id"] for e in marcacao["elementos"]] == ["T1", "T2"]
    assert marcacao["sem_elementos"] == [{"imagem": 2, "motivo": "vazia"}]
    assert usos == [] and falsa.chamadas == []
    assert info == {"marcacao_chamadas": 0, "marcacao_refeitas": 0, "marcacao_falhas": 0}


def test_paralelas_nao_configuradas_vira_uma_por_vez(ia, monkeypatch, capsys):
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_POR_IMAGEM", True)
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_REFAZER", False)
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_PARALELAS", None)
    ia({"planner-marcacao-1": _bloco(_el()), "planner-marcacao-2": _bloco(_el("T2", imagem=2))})
    marcacao, usos = planner.marcar_tela_a_tela(CONTEXTO, [_tela(1), _tela(2)], "a", None, {})
    assert [e["id"] for e in marcacao["elementos"]] == ["T1", "T2"]
    assert len(usos) == 2
    assert "2 chamada(s), até 1 ao mesmo tempo" in capsys.readouterr().out
