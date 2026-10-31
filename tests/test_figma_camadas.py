"""Figma: escala da exportação e inventário de camadas que vai para a IA."""
import pytest

from tagauditor import figma


@pytest.fixture(autouse=True)
def limites_padrao(monkeypatch):
    monkeypatch.setattr(figma, "FIGMA_ESCALA_MAX", 2)
    monkeypatch.setattr(figma, "FIGMA_LARGURA_ALVO", 1600)
    monkeypatch.setattr(figma, "FIGMA_MAX_ELEMENTOS", 150)


def _caixa(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


def _no(id_no, tipo, x, y, w, h, **extra):
    no = {"id": id_no, "type": tipo, "name": extra.pop("name", id_no),
          "absoluteBoundingBox": _caixa(x, y, w, h)}
    no.update(extra)
    return no


def _texto(id_no, caracteres, x, y, w=100, h=20, **extra):
    return _no(id_no, "TEXT", x, y, w, h, characters=caracteres, **extra)


# ---- Escala --------------------------------------------------------------------

@pytest.mark.parametrize("dimensoes, esperado", [
    ([], 1.0),                                 # nada utilizável: 1x
    ([(None, 10), ("x", 10), (0, 10), (-5, 10), (float("inf"), 10), (10, float("inf"))], 1.0),
    ([(375, 812)], 2.0),                       # celular: limitado a FIGMA_ESCALA_MAX
    ([(1600, 900)], 1.0),
    ([(4000, 3000)], 0.5),                     # largo: nunca abaixo de 0,5x
    ([(1000, 40000)], 0.86),                   # alto: limitado a 30 MP, arredonda p/ baixo
    ([(375, 812), (1600, 900)], 1.0),          # vale a menor
    ([(3000, 1000)], 0.53),
])
def test_calcular_escala(dimensoes, esperado):
    assert figma._calcular_escala(dimensoes) == esperado


def test_calcular_escala_respeita_os_tetos(monkeypatch):
    monkeypatch.setattr(figma, "FIGMA_ESCALA_MAX", 10)
    assert figma._calcular_escala([(100, 100)]) == 4.0        # teto da API
    assert figma._calcular_escala([(1e6, 1e6)]) == 0.01       # piso


def test_texto_e_numero_da_escala():
    assert figma._texto_escala(2.0) == "2"
    assert figma._texto_escala(0.5) == "0.5"
    assert figma._texto_escala(1.25) == "1.25"
    assert figma._numero_escala(2.0) == 2 and isinstance(figma._numero_escala(2.0), int)
    assert figma._numero_escala(0.86) == 0.86


# ---- Nomes e caixas --------------------------------------------------------------

def test_normalizar_nome():
    assert figma._normalizar_nome("BTNPrimary") == "btn primary"
    assert figma._normalizar_nome("iconButton") == "icon button"
    assert figma._normalizar_nome("Botão Ação") == "botao acao"
    assert figma._normalizar_nome(None) == ""


def test_caixa():
    assert figma._caixa({"absoluteBoundingBox": _caixa(1, 2, 3, 4)}) == (1.0, 2.0, 3.0, 4.0)
    assert figma._caixa({"absoluteRenderBounds": _caixa(1, 2, 3, 4)},
                        "absoluteRenderBounds") == (1.0, 2.0, 3.0, 4.0)
    assert figma._caixa("não é nó") is None
    assert figma._caixa({}) is None
    assert figma._caixa({"absoluteBoundingBox": None}) is None
    assert figma._caixa({"absoluteBoundingBox": {"x": 1}}) is None
    assert figma._caixa({"absoluteBoundingBox": _caixa(None, 0, 1, 1)}) is None
    assert figma._caixa({"absoluteBoundingBox": _caixa("abc", 0, 1, 1)}) is None
    assert figma._caixa({"absoluteBoundingBox": _caixa(float("nan"), 0, 1, 1)}) is None
    assert figma._caixa({"absoluteBoundingBox": _caixa(0, 0, float("inf"), 1)}) is None
    assert figma._caixa({"absoluteBoundingBox": _caixa(0, 0, -1, 1)}) is None
    assert figma._caixa({"absoluteBoundingBox": _caixa(0, 0, 1, -1)}) is None


def test_caixa_referencia():
    frame = {"absoluteBoundingBox": _caixa(0, 0, 100, 200),
             "absoluteRenderBounds": _caixa(-10, -10, 120, 220)}
    caixa, render = (0.0, 0.0, 100.0, 200.0), (-10.0, -10.0, 120.0, 220.0)
    # PNG na proporção da caixa do frame
    assert figma._caixa_referencia(frame, 100, 200) == caixa
    # PNG na proporção da área de renderização (sombra vazando)
    assert figma._caixa_referencia(frame, 120, 220) == render
    # Sem o PNG: a caixa do frame
    assert figma._caixa_referencia(frame, None, None) == caixa
    # Sem área de renderização
    assert figma._caixa_referencia({"absoluteBoundingBox": _caixa(0, 0, 100, 200)}, 1, 2) == caixa
    assert figma._caixa_referencia(
        {"absoluteBoundingBox": _caixa(0, 0, 100, 200),
         "absoluteRenderBounds": _caixa(0, 0, 0, 0)}, 1, 2) == caixa
    # Frame sem caixa: usa a área de renderização, se houver
    assert figma._caixa_referencia({"absoluteRenderBounds": _caixa(0, 0, 5, 5)}, 1, 1) == \
        (0.0, 0.0, 5.0, 5.0)
    assert figma._caixa_referencia({"absoluteBoundingBox": _caixa(0, 0, 0, 10)}, 1, 1) is None
    assert figma._caixa_referencia(
        {"absoluteBoundingBox": _caixa(0, 0, 0, 10),
         "absoluteRenderBounds": _caixa(0, 0, 0, 10)}, 1, 1) is None


# ---- Interação ---------------------------------------------------------------------

def test_tem_interacao():
    assert figma._tem_interacao({"interactions": [None, {"trigger": {"type": "ON_CLICK"}}]})
    assert not figma._tem_interacao({"interactions": [{"trigger": {"type": "AFTER_TIMEOUT"}},
                                                      {"trigger": "ON_CLICK"},
                                                      {"trigger": {}}]})
    assert figma._tem_interacao({"transitionNodeID": "1:2"})
    assert figma._tem_interacao({"type": "TEXT", "style": {"hyperlink": {"url": "x"}}})
    assert figma._tem_interacao({"type": "TEXT", "style": {},
                                 "styleOverrideTable": {"1": "x", "2": {"hyperlink": {"url": "y"}}}})
    assert not figma._tem_interacao({"type": "TEXT", "style": "x",
                                     "styleOverrideTable": {"1": {"fontSize": 3}}})
    assert not figma._tem_interacao({"type": "TEXT", "styleOverrideTable": []})
    assert not figma._tem_interacao({"type": "RECTANGLE", "style": {"hyperlink": "x"}})


def test_opacidade_zero():
    assert figma._opacidade_zero({"opacity": 0})
    assert figma._opacidade_zero({"opacity": 0.0005})
    assert not figma._opacidade_zero({"opacity": 0.5})
    assert not figma._opacidade_zero({"opacity": False})
    assert not figma._opacidade_zero({})


COMPONENTES = {"c1": {"name": "Primary", "componentSetId": "s1"},
               "c2": {"name": "Chevron"},
               "c3": "não é dict"}
CONJUNTOS = {"s1": {"name": "Button"}}


def test_nomes_do_no():
    inst = {"type": "INSTANCE", "name": "Contratar", "componentId": "c1"}
    assert figma._nomes_do_no(inst, COMPONENTES, CONJUNTOS) == ["Contratar", "Primary", "Button"]
    assert figma._nomes_do_no({"type": "INSTANCE", "name": "x", "componentId": "c3"},
                              COMPONENTES, CONJUNTOS) == ["x"]
    assert figma._nomes_do_no({"type": "INSTANCE", "name": "x", "componentId": "c1"},
                              COMPONENTES, {"s1": "ruim"}) == ["x", "Primary"]
    assert figma._nomes_do_no({"type": "FRAME"}, {}, {}) == []


@pytest.mark.parametrize("no, dentro, esperado", [
    ({"type": "FRAME", "name": "BTNPrimary"}, False, True),
    ({"type": "FRAME", "name": "Background"}, False, False),       # "back" não casa
    ({"type": "GROUP", "name": "Selected"}, False, False),          # "select" não casa
    ({"type": "FRAME", "name": "Table"}, False, False),             # "tab" não casa
    ({"type": "FRAME", "name": "Tabs"}, False, True),
    ({"type": "FRAME", "name": "Check box"}, False, True),
    ({"type": "INSTANCE", "name": "Contratar", "componentId": "c1"}, False, True),
    ({"type": "FRAME", "name": "Ícone"}, False, True),              # ícone solto
    ({"type": "FRAME", "name": "Ícone"}, True, False),              # ícone dentro de botão
    ({"type": "RECTANGLE", "name": "Button"}, False, False),        # tipo que não vale nome
])
def test_casa_nome(no, dentro, esperado):
    assert figma._casa_nome(no, COMPONENTES, CONJUNTOS, dentro) is esperado


def test_nome_item():
    assert figma._nome_item({"type": "INSTANCE", "name": "Contratar", "componentId": "c1"},
                            COMPONENTES, CONJUNTOS) == "Contratar · Button"
    assert figma._nome_item({"type": "INSTANCE", "name": "Button grande", "componentId": "c1"},
                            COMPONENTES, CONJUNTOS) == "Button grande"
    assert figma._nome_item({"type": "INSTANCE", "name": "", "componentId": "c2"},
                            COMPONENTES, CONJUNTOS) == "Chevron"
    assert figma._nome_item({"type": "INSTANCE", "name": "Seta", "componentId": "c3"},
                            COMPONENTES, CONJUNTOS) == "Seta"
    assert figma._nome_item({"type": "INSTANCE", "name": "Seta"}, {}, {}) == "Seta"
    assert figma._nome_item({"type": "FRAME", "name": "x" * 100}, {}, {}) == "x" * 79 + "…"


def test_textos_internos_ignora_ocultos_e_repetidos():
    no = {"children": [
        "lixo",
        {"type": "TEXT", "id": "t0", "characters": "Oculto", "visible": False},
        {"type": "TEXT", "id": "t1", "characters": " Abrir  conta "},
        {"type": "FRAME", "opacity": 0, "children": [{"type": "TEXT", "id": "tx", "characters": "x"}]},
        {"type": "GROUP", "children": [
            {"type": "TEXT", "id": "t2", "characters": "Abrir conta"},
            {"type": "TEXT", "id": "t3", "characters": "   "},
            {"type": "TEXT", "id": "t4", "characters": "Grátis"},
        ]},
        {"type": "TEXT", "id": "t5", "characters": "Saiba mais"},
        {"type": "TEXT", "id": "t6", "characters": "Quarto"},
    ]}
    textos, ids = figma._textos_internos(no)
    assert textos == ["Abrir conta", "Grátis", "Saiba mais"]
    assert ids == ["t1", "t2", "t4", "t5"]
    assert figma._textos_internos({}) == ([], [])


def test_em_ordem_de_leitura():
    def b(nome, x, y, ordem):
        return {"nome": nome, "x0": x, "y0": y, "ordem": ordem}
    brutos = [b("direita", 200, 5, 0), b("baixo", 0, 100, 1), b("esquerda", 0, 0, 2),
              b("meio", 100, 8, 3)]
    assert [x["nome"] for x in figma._em_ordem_de_leitura(brutos)] == \
        ["esquerda", "meio", "direita", "baixo"]
    assert figma._em_ordem_de_leitura([]) == []


def test_norm_recorta_em_0_1():
    assert figma._norm(50, 200) == 0.25
    assert figma._norm(-5, 100) == 0.0
    assert figma._norm(300, 100) == 1.0


# ---- Inventário ---------------------------------------------------------------------

def _frame_de_tela():
    """Tela 400x800 em (1000, 1000) com um pouco de tudo."""
    filhos = [
        _texto("t-titulo", "Bem-vindo", 1020, 1020, 200, 30),
        # Botão (por nome de componente) com textos internos: vira um item só.
        _no("b-entrar", "INSTANCE", 1020, 1100, 360, 48, name="Entrar", componentId="c1",
            children=[_texto("t-entrar", "Entrar", 1030, 1110),
                      _no("ic", "FRAME", 1300, 1110, 20, 20, name="icon")]),
        # Interação de protótipo num retângulo: forte, sem texto.
        _no("r-clique", "RECTANGLE", 1020, 1200, 50, 50,
            interactions=[{"trigger": {"type": "ON_CLICK"}}]),
        # Área de clique invisível (opacidade 0) com interação: entra sem rótulo e
        # nada dentro dela aparece.
        _no("hotspot", "FRAME", 1100, 1200, 50, 50, opacity=0,
            interactions=[{"trigger": {"type": "ON_TAP"}}],
            children=[_texto("t-escondido", "Não aparece", 1100, 1200)]),
        # Opacidade zero sem interação: some com os filhos.
        _no("transparente", "FRAME", 1200, 1200, 50, 50, opacity=0,
            children=[_texto("t-some", "Some", 1200, 1200)]),
        # Camada oculta.
        _texto("t-oculto", "Oculto", 1020, 1300, visible=False),
        # Link num texto.
        _texto("t-link", "Termos de uso", 1020, 1700, 120, 20,
               style={"hyperlink": {"type": "URL", "url": "https://x"}}),
        # Texto vazio e fio fino demais.
        _texto("t-vazio", "  ", 1020, 1400),
        _no("fio", "RECTANGLE", 1000, 1450, 400, 1),
        _texto("t-fino", "fino", 1020, 1460, 100, 1),
        # Texto sem caixa.
        {"id": "t-sem-caixa", "type": "TEXT", "characters": "Sem caixa"},
        # Texto fora da tela (recortado a nada).
        _texto("t-fora", "Fora", 3000, 3000),
        # Carrossel que corta o conteúdo: o 2º card fica parcialmente fora.
        _no("carrossel", "FRAME", 1000, 1500, 400, 100, name="Lista", clipsContent=True,
            children=[_no("card1", "FRAME", 1000, 1500, 300, 100, name="Card",
                          children=[_texto("t-c1", "Cartão 1", 1010, 1510)]),
                      _no("card2", "FRAME", 1350, 1500, 300, 100, name="Card",
                          children=[_texto("t-c2", "Cartão 2", 1360, 1510)])]),
        # Contêiner que corta mas está fora da área: nada de dentro entra.
        _no("corte-fora", "FRAME", 5000, 5000, 10, 10, clipsContent=True,
            children=[_texto("t-cf", "Cortado", 5000, 5000)]),
        # Corta, mas sem caixa: herda o corte de fora.
        {"id": "sem-caixa", "type": "FRAME", "clipsContent": True,
         "children": [_texto("t-sc", "Dentro sem caixa", 1020, 1600)]},
        "lixo",
    ]
    raiz = _no("1:1", "FRAME", 1000, 1000, 400, 800, children=filhos)
    return raiz


def test_inventario_monta_itens_em_ordem_de_leitura():
    raiz = _frame_de_tela()
    ref = figma._caixa(raiz)
    itens, truncado = figma._inventario(raiz, ref, COMPONENTES, CONJUNTOS)
    assert truncado is False
    por_id = {i["id"]: i for i in itens}
    assert [i["ref"] for i in itens] == ["F{}".format(n) for n in range(1, len(itens) + 1)]
    assert list(por_id) == ["t-titulo", "b-entrar", "r-clique", "hotspot", "card1", "card2",
                            "t-sc", "t-link"]

    titulo = por_id["t-titulo"]
    assert titulo == {"ref": "F1", "id": "t-titulo", "tipo": "TEXT", "nome": "t-titulo",
                      "texto": "Bem-vindo", "interativo": False,
                      "bbox": {"x": 0.05, "y": 0.025, "largura": 0.5, "altura": 0.0375}}
    botao = por_id["b-entrar"]
    assert botao["interativo"] and botao["texto"] == "Entrar"
    assert botao["nome"] == "Entrar · Button"
    assert por_id["r-clique"]["interativo"] and por_id["r-clique"]["texto"] == ""
    assert por_id["hotspot"]["texto"] == ""
    assert por_id["t-link"]["interativo"] and por_id["t-link"]["texto"] == "Termos de uso"
    assert por_id["card1"]["texto"] == "Cartão 1"
    # card2 recortado pelo carrossel (vai até x=1400): largura 50 de 400
    assert por_id["card2"]["bbox"]["largura"] == 0.125
    assert not por_id["t-sc"]["interativo"]
    for oculto in ("t-entrar", "ic", "t-escondido", "transparente", "t-some", "t-oculto",
                   "t-vazio", "fio", "t-fino", "t-sem-caixa", "t-fora", "t-c1", "t-cf"):
        assert oculto not in por_id


def test_inventario_corta_pela_prioridade():
    raiz = _frame_de_tela()
    itens, truncado = figma._inventario(raiz, figma._caixa(raiz), COMPONENTES, CONJUNTOS,
                                        limite=3)
    assert truncado is True
    # Interação de protótipo vem primeiro, depois nome com texto, depois texto comum.
    assert sorted(i["id"] for i in itens) == ["hotspot", "r-clique", "t-link"]


def test_inventario_usa_o_limite_configurado(monkeypatch):
    monkeypatch.setattr(figma, "FIGMA_MAX_ELEMENTOS", 1)
    raiz = _frame_de_tela()
    itens, truncado = figma._inventario(raiz, figma._caixa(raiz), "x", None)
    assert truncado is True and len(itens) == 1
    assert figma._inventario(raiz, figma._caixa(raiz), limite=-5) == ([], True)


@pytest.mark.parametrize("raiz, ref", [
    ("não é nó", (0, 0, 10, 10)),
    ({"children": []}, None),
    ({"children": []}, (0, 0, 0, 10)),
    ({"children": []}, (0, 0, 10, 0)),
])
def test_inventario_sem_area_util(raiz, ref):
    assert figma._inventario(raiz, ref) == ([], False)


def test_inventario_frame_sem_filhos():
    assert figma._inventario({"id": "1:1"}, (0, 0, 10, 10)) == ([], False)
