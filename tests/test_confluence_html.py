"""Montagem do plano a partir do HTML da página (porte do bookmarklet)."""
import json

import pytest

from tagauditor import confluence


def _plano(html_pagina, titulo="Plano"):
    r = confluence._montar_plano_de_html(titulo, html_pagina)
    assert r["arquivo"]["nome"] == titulo + ".json"
    return json.loads(r["arquivo"]["conteudo"])["sections"]


def _codigo(texto):
    """Macro de código como o Confluence renderiza no body.view."""
    return ('<div class="code panel pdl conf-macro output-block" data-hasbody="true" '
            'data-macro-name="code"><div class="codeContent panelContent pdl">'
            '<pre class="syntaxhighlighter-pre" data-syntaxhighlighter-params="brush: js">'
            + texto + '</pre></div></div>')


def _status(texto, parametros=None):
    attr = ' data-macro-parameters="{}"'.format(parametros) if parametros is not None else ""
    return ('<span class="status-macro aui-lozenge conf-macro output-inline" '
            'data-hasbody="false" data-macro-name="status"' + attr + '>' + texto + '</span>')


# ---- _js_para_json / _conf_parse_code --------------------------------------------------

def test_js_para_json_aceita_json_e_objeto_js():
    assert confluence._js_para_json('{"a": [1, 2]}') == {"a": [1, 2]}
    assert confluence._js_para_json("{event: 'clique', 'rotulo': 'Entrar', $x: [1, 2,],}") == {
        "event": "clique", "rotulo": "Entrar", "$x": [1, 2]}
    assert confluence._js_para_json("isso não é objeto") is None


@pytest.mark.parametrize("bruto", ["", "   \n\t", None])
def test_codigo_vazio(bruto):
    assert confluence._conf_parse_code(bruto) == {"raw_code_fallback": "Codigo vazio."}


def test_codigo_datalayer_em_sintaxe_js_com_comentarios_e_aspas_curvas():
    bruto = ("CÓDIGO A SER IMPLEMENTADO 1\n"
             "/* bloco de comentário */\n"
             "window.dataLayer = window.dataLayer || [];\n"
             "dataLayer.push({\n"
             "  event: ‘clique’,   // comentário de linha\n"
             "  “rotulo”: “Entrar agora”,\n"
             "  etapa: `login`,\n"
             "});")
    assert confluence._conf_parse_code(bruto) == {
        "event": "clique", "rotulo": "Entrar agora", "etapa": "login", "_is_native": False}


def test_codigo_lista_json_volta_como_lista():
    assert confluence._conf_parse_code('[{"event": "a"}, {"event": "b"}]') == [
        {"event": "a"}, {"event": "b"}]


def test_codigo_com_referencias_document_vira_string():
    bruto = 'var dados = {"page": document.title, "url": location.href, "event": "view",};'
    assert confluence._conf_parse_code(bruto) == {
        "page": "document.title", "url": "location.href", "event": "view", "_is_native": False}


def test_codigo_nativo_android():
    bruto = ('Bundle bundle = new Bundle();\n'
             'bundle.putString("event_type", "interacao");\n'
             'bundle.putString("index", "1");\n'
             'bundle.putString("clique_menu", "duplicado");\n'
             'bundle.putString("tela", "home");\n'
             "bundle.putString('rotulo', 'Menu');\n"
             'Analytics.logEvent("clique_menu", bundle);')
    assert confluence._conf_parse_code(bruto) == {
        "_is_native": True, "event": "clique_menu", "tela": "home", "rotulo": "Menu"}


def test_codigo_nativo_sem_log_event():
    assert confluence._conf_parse_code('bundle.putString("tela", "extrato");') == {
        "_is_native": True, "tela": "extrato"}


def test_codigo_lido_linha_a_linha():
    bruto = ("[\n"
             "{\n"
             "  event: clique\n"
             "\n"
             "  ecommerce: {\n"
             "     item_id: 123,\n"
             "     'item_name': \"Produto X\",\n"
             "  },\n"
             "  rotulo, 'valor solto'\n"
             "}\n"
             "]")
    assert confluence._conf_parse_code(bruto) == {
        "_is_native": False,
        "event": "clique",
        "ecommerce": {"item_id": "123", "item_name": "Produto X"},
        "rotulo": "valor solto",
    }


def test_codigo_irreconhecivel_vira_fallback():
    assert confluence._conf_parse_code("dispare o evento quando a tela abrir") == {
        "raw_code_fallback": "dispare o evento quando a tela abrir",
        "parse_warning": "Sintaxe irreconhecivel."}


# ---- mini-DOM ---------------------------------------------------------------------------

def test_dom_trata_br_tags_vazias_e_fechamento_sem_abertura():
    raiz = confluence._dom('<div class="a  b" data-x><p>um<br>dois<img src="i.png">tres</span>'
                           '</p><p>&amp; fim</p></div>')
    div = confluence._n_els(raiz)[0]
    assert confluence._n_classes(div) == ["a", "b"]
    assert confluence._n_atr(div, "data-x") == ""
    p1, p2 = confluence._n_els(div)
    assert [f if isinstance(f, str) else f.tag for f in p1.filhos] == [
        "um", "br", "\n", "dois", "img", "tres"]
    assert confluence._n_texto(p1) == "um\ndoistres"
    assert confluence._n_texto(p2) == "& fim"


def test_texto_normaliza_espacos_por_linha():
    raiz = confluence._dom("<td>  a \t b <br>   c  </td>")
    assert confluence._n_texto(raiz) == "a b\nc"


def test_codigo_de_macro_sem_pre_usa_o_texto_da_macro():
    raiz = confluence._dom('<div data-macro-name="code"><span>{"event": "x"}</span></div>')
    assert confluence._codigo_de(raiz) == '{"event": "x"}'


def test_codigo_de_sem_macro_nem_indicio():
    assert confluence._codigo_de(confluence._dom("<td><p>texto comum</p></td>")) == ""


def test_secao_por_irmaos_casos_sem_titulo():
    orfao = confluence._NoHTML("table", [], None)
    assert confluence._secao_por_irmaos(orfao, "Padrão") == "Padrão"
    pai = confluence._NoHTML("div", [], None)
    fora_da_lista = confluence._NoHTML("table", [], pai)   # não está em pai.filhos
    assert confluence._secao_por_irmaos(fora_da_lista, "Padrão") == "Padrão"


def test_secao_por_irmaos_pula_heading_vazio_e_sobe_do_table_wrap():
    raiz = confluence._dom('<h2>Extrato</h2><p>x</p><h3> </h3>'
                           '<div class="table-wrap"><table></table></div>')
    tabela = confluence._n_um(raiz, lambda n: n.tag == "table")
    assert confluence._secao_por_irmaos(tabela, "Padrão") == "Extrato"


# ---- Padrão 4: .sectionColumnWrapper com tabela "Tela" / "Código de" ---------------------

PADRAO_4 = """
<div id="main-content" class="wiki-content">
<div class="sectionColumnWrapper conf-macro output-block" data-hasbody="true" data-macro-name="section">
 <div class="sectionMacro"><div class="sectionMacroRow">
  <div class="columnMacro">
   <div class="panel"><div class="panelContent"><h3>Home &amp; Login</h3><p>texto</p></div></div>
  </div>
  <div class="columnMacro">
   <div class="table-wrap"><table class="confluenceTable"><tbody>
    <tr><th class="confluenceTh">Tela</th><td class="confluenceTd"><img src="tela.png"></td></tr>
    <tr><th class="confluenceTh">Evidência</th><td class="confluenceTd">ver anexo</td></tr>
    <tr><th class="confluenceTh">Status</th><td class="confluenceTd">""" + _status(
        "IMPLEMENTADO", "colour=Green|title=OK") + """</td></tr>
    <tr><th class="confluenceTh">Descrição</th><td class="confluenceTd">Clique no botão <strong>Entrar</strong></td></tr>
    <tr><th class="confluenceTh">Vazio</th><td class="confluenceTd">   </td></tr>
    <tr><th class="confluenceTh">Só rótulo</th></tr>
    <tr><th class="confluenceTh">Código de implementação</th><td class="confluenceTd">""" + _codigo(
        "dataLayer.push({\n  event: 'login',\n  'step': 'inicio',\n});") + """</td></tr>
   </tbody></table></div>
  </div>
 </div></div>
</div>

<div class="sectionColumnWrapper">
 <table class="confluenceTable">
  <tr><th>Código de disparo</th><td><p>Observação</p><p>dataLayer.push({"event": "logout"})</p></td></tr>
  <tr><th>Status</th><td>sem macro</td></tr>
 </table>
</div>

<div class="sectionColumnWrapper">
 <div class="panelContent"><h2>Pagamento</h2></div>
 <table class="confluenceTable"><tr><th>Legenda</th><td>x</td></tr></table>
 <table class="confluenceTable">
  <thead><tr><th>Tela</th><td><img src="p.png"></td></tr></thead>
  <tbody><tr><th>Codigo</th><td>{"event": "pagar", "valor": "10"}</td></tr></tbody>
 </table>
</div>

<div class="sectionColumnWrapper">
 <div class="panelContent"><b> </b></div>
 <table class="confluenceTable"><tr><th>Tela</th><td><img src="s.png"></td></tr></table>
</div>

<div class="sectionColumnWrapper"><table class="confluenceTable"><tr><th>Outra</th><td>y</td></tr></table></div>
</div>
"""


def test_padrao_4_section_column_wrapper():
    assert _plano(PADRAO_4) == {
        "Home & Login": [
            {"id": 1,
             "status_macro": {"text": "IMPLEMENTADO", "params": "colour=Green|title=OK"},
             "rich_texts": [{"label": "Descrição", "html": "Clique no botão Entrar"}],
             "code_json": {"event": "login", "step": "inicio", "_is_native": False}},
            {"id": 2, "status_macro": None, "rich_texts": [],
             "code_json": {"event": "logout", "_is_native": False}},
        ],
        "Pagamento": [
            {"id": 3, "status_macro": None, "rich_texts": [],
             "code_json": {"event": "pagar", "valor": "10", "_is_native": False}},
        ],
    }


# ---- Padrão 2/3: tabela "wrapped confluenceTable" com coluna "Print da Tela" --------------

PADRAO_2 = """
<h1>Plano de Tagueamento</h1>
<h2>Tela de Extrato</h2>
<p>Introdução da tela.</p>
<div class="table-wrap"><table class="wrapped confluenceTable"><colgroup><col><col></colgroup><tbody>
 <tr><th class="confluenceTh">Print da Tela</th><th class="confluenceTh">Tagueamento</th></tr>
 <tr><td class="confluenceTd"><img src="print1.png"></td><td class="confluenceTd">
  <div class="confluence-information-macro conf-macro output-block" data-macro-name="info">
   <span class="aui-icon confluence-information-macro-icon"></span>
   <div class="confluence-information-macro-body">
    <p>""" + _status("EM ANDAMENTO", "colour=Yellow") + """</p>
    """ + _codigo("dataLayer.push({event: 'extrato_view'});") + """
    """ + _codigo("dataLayer.push({event: 'extrato_click'});") + """
    <p>Observações: disparar ao carregar</p>
    <p>Observação:</p>
   </div>
  </div>
  <div class="confluence-information-macro" data-macro-name="info">
   <div class="confluence-information-macro-body"><p>Observação - só texto</p></div>
  </div>
 </td></tr>
 <tr><td class="confluenceTd"><img src="print2.png"></td><td class="confluenceTd">
  <p>dataLayer.push({"event": "filtro"})</p><p>Observações:   filtro por período</p>
 </td></tr>
 <tr><td class="confluenceTd">linha com uma coluna só</td></tr>
</tbody></table></div>

<div class="table-wrap"><table class="wrapped confluenceTable">
 <tr><th>Outra tabela</th></tr><tr><td>a</td><td>dataLayer.push({"event": "ignorado"})</td></tr>
</table></div>

<div class="bloco">
 <h4> </h4>
 <table class="wrapped confluenceTable"><thead><tr><th>Print da Tela</th><th>Código</th></tr></thead>
  <tbody><tr><td><img src="p3.png"></td><td><div data-macro-name="code"><pre>{"event": "outro"}</pre></div></td></tr></tbody>
 </table>
</div>
"""


def test_padrao_2_tabela_print_da_tela():
    assert _plano(PADRAO_2) == {
        "Tela de Extrato": [
            {"id": 1,
             "status_macro": {"text": "EM ANDAMENTO", "params": "colour=Yellow"},
             "rich_texts": [{"label": "Observações", "html": "disparar ao carregar"}],
             "code_json": {"event": "extrato_view", "_is_native": False}},
            {"id": 2, "status_macro": None,
             "rich_texts": [{"label": "Observações", "html": "- só texto"}],
             "code_json": {"raw_code_fallback": "Codigo vazio."}},
            {"id": 3, "status_macro": None,
             "rich_texts": [{"label": "Observações", "html": "filtro por período"}],
             "code_json": {"event": "filtro", "_is_native": False}},
            {"id": 4, "status_macro": None, "rich_texts": [],
             "code_json": {"event": "outro", "_is_native": False}},
        ],
    }


# ---- Padrão 1: macros section com macro de status ----------------------------------------

PADRAO_1 = """
<h2>Cartões</h2>
<div class="sectionColumnWrapper conf-macro output-block" data-macro-name="section">
 <div class="columnMacro">
  <div class="table-wrap"><table class="confluenceTable"><tbody>
   <tr><th>Evento</th><td>clique_cartao</td></tr>
   <tr><td class="confluenceTh">Descrição</td><td class="confluenceTd">Clique no cartão</td></tr>
   <tr><th>Status</th><td>""" + _status("OK", "colour=Green") + """</td></tr>
   <tr><th>Print</th><td><img src="a.png"> imagem</td></tr>
   <tr><th>Código</th><td>""" + _codigo("dataLayer.push({event: 'cartao'})") + """</td></tr>
   <tr><th>Sem valor</th></tr>
   <tr><th>Vazio</th><td> </td></tr>
  </tbody></table></div>
 </div>
</div>
<div data-macro-name="section"><p>seção sem status</p></div>
<h2>Pix</h2>
<div data-macro-name="section">""" + _status("PENDENTE") + """
 <p>Analytics.logEvent("pix_view", bundle)</p>
</div>
"""


def test_padrao_1_section_com_status():
    assert _plano(PADRAO_1) == {
        "Cartões": [
            {"id": 1,
             "status_macro": {"text": "OK", "params": "colour=Green"},
             "rich_texts": [{"label": "Evento", "html": "clique_cartao"},
                            {"label": "Descrição", "html": "Clique no cartão"}],
             "code_json": {"event": "cartao", "_is_native": False}},
        ],
        "Pix": [
            {"id": 2, "status_macro": {"text": "PENDENTE", "params": ""}, "rich_texts": [],
             "code_json": {"_is_native": True, "event": "pix_view"}},
        ],
    }


def test_nenhum_layout_reconhecido():
    raiz = confluence._dom('<h2>Nada</h2><p>só texto</p><div data-macro-name="section"></div>')
    assert confluence._extrair_padroes_bookmarklet(raiz) == ({}, 0)


# ---- fallback: blocos <pre> soltos ---------------------------------------------------------

PAGINA_PRE = """
<div class="expand-container">
 <div class="expand-control"><span class="expand-control-icon"></span><span class="expand-control-text">Login &amp; Cadastro</span></div>
 <div class="expand-content">
  <pre>dataLayer.push({<br/>  event: 'login'<br>})</pre>
  <pre>   </pre>
  <pre>texto sem código</pre>
 </div>
</div>
<h3><a name="ancora"></a></h3>
<h2 id="home">Home <em>principal</em></h2>
<pre>{&quot;event&quot;: &quot;home&quot;}</pre>
<pre>Bundle b = new Bundle(); b.putString("tela", "home"); Analytics.logEvent("home_view", b);</pre>
"""


def test_fallback_blocos_pre_por_secao():
    assert _plano(PAGINA_PRE, "Plano Pre") == {
        "Login & Cadastro": [
            {"id": 1, "code_json": {"event": "login", "_is_native": False}},
        ],
        "Home principal": [
            {"id": 2, "code_json": {"event": "home", "_is_native": False}},
            {"id": 3, "code_json": {"_is_native": True, "event": "home_view", "tela": "home"}},
        ],
    }


def test_pre_antes_de_qualquer_titulo_vai_para_fluxo_importado():
    assert list(_plano("<pre>dataLayer.push({event: 'x'})</pre>")) == ["Fluxo Importado"]


def test_varredura_que_quebra_cai_nos_blocos_pre(monkeypatch, capsys):
    def explode(raiz):
        raise RecursionError("HTML aninhado demais")
    monkeypatch.setattr(confluence, "_extrair_padroes_bookmarklet", explode)

    secoes = _plano(PADRAO_1 + "<pre>{\"event\": \"so_pre\"}</pre>")

    # sem os layouts, só os <pre> contam (inclusive o de dentro da macro de código)
    assert secoes == {
        "Cartões": [{"id": 1, "code_json": {"event": "cartao", "_is_native": False}}],
        "Pix": [{"id": 2, "code_json": {"event": "so_pre", "_is_native": False}}],
    }
    assert "Varredura de layouts falhou" in capsys.readouterr().out


def test_pagina_sem_codigo_nenhum():
    with pytest.raises(ValueError, match='Não encontrei blocos de código JSON na página "Vazia"'):
        confluence._montar_plano_de_html("Vazia", "<h2>Título</h2><pre>só texto</pre><p>nada</p>")
