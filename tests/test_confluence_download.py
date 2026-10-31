"""Fluxo de download do plano no Confluence (sem rede: tudo por stubs)."""
import base64
import email.message
import io
import json
import urllib.error

import pytest

from tagauditor import confluence

BASE = "https://confluence.bradesco.com.br:8443"
URL_ANEXOS_123 = BASE + "/rest/api/content/123/child/attachment?limit=200&expand=version"
URL_PAGINA_REST_123 = BASE + "/rest/api/content/123?expand=body.view,body.export_view"
URL_ANEXOS_UI_123 = BASE + "/pages/viewpageattachments.action?pageId=123"
URL_PAGINA_UI_123 = BASE + "/pages/viewpage.action?pageId=123"

PAGINA_LOGIN = ('<html><form><input name="os_username"><input type="password" '
                'name="os_password"></form></html>')


# ---- stubs --------------------------------------------------------------------

class _AbridorProibido:
    def open(self, req, timeout=None):
        raise AssertionError("teste tentou acessar a rede: " + req.full_url)


@pytest.fixture(autouse=True)
def _sem_rede_nem_segredos(monkeypatch):
    """Nada de rede real, nada de espera e o mesmo comportamento com ou sem
    tagauditor/segredos.py (no pipeline ele não existe)."""
    monkeypatch.setattr(confluence, "_ABRIR", _AbridorProibido())
    monkeypatch.setattr(confluence, "CONFLUENCE_TOKEN", "")
    monkeypatch.setattr(confluence, "CONFLUENCE_USUARIO", "")
    monkeypatch.setattr(confluence, "CONFLUENCE_SENHA", "")
    monkeypatch.setattr(confluence.time, "sleep", lambda s: None)


class _Resposta:
    def __init__(self, status, corpo, url):
        self.status, self._corpo, self._url = status, corpo, url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._corpo

    def geturl(self):
        return self._url


class _Abridor:
    """Substitui confluence._ABRIR: devolve/levanta as respostas em ordem."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.pedidos = []

    def open(self, req, timeout=None):
        self.pedidos.append((req, timeout))
        r = self.respostas.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def _http_erro(codigo, corpo=b"", cabecalhos=None, url=BASE + "/x"):
    hdrs = None
    if cabecalhos is not None:
        hdrs = email.message.Message()
        for k, v in cabecalhos.items():
            hdrs[k] = v
    return urllib.error.HTTPError(url, codigo, "erro", hdrs, io.BytesIO(corpo))


class _Confluence:
    """Substitui confluence._get_confluence respondendo por URL exata.
    Cada rota é (status, corpo[, url_final]) ou uma lista delas (em ordem)."""

    def __init__(self, rotas):
        self.rotas = {u: (list(r) if isinstance(r, list) else [r]) for u, r in rotas.items()}
        self.chamadas = []

    def __call__(self, url, tentativas=3):
        self.chamadas.append(url)
        assert url in self.rotas, "URL inesperada: " + url
        fila = self.rotas[url]
        resp = fila.pop(0) if len(fila) > 1 else fila[0]
        status, corpo = resp[0], resp[1]
        final = resp[2] if len(resp) > 2 else url
        if isinstance(corpo, str):
            corpo = corpo.encode("utf-8")
        return status, corpo, final


@pytest.fixture
def conf(monkeypatch):
    def instalar(rotas):
        falso = _Confluence(rotas)
        monkeypatch.setattr(confluence, "_get_confluence", falso)
        return falso
    return instalar


def _json(obj):
    return json.dumps(obj)


def _anexo(titulo, download, quando=""):
    return {"title": titulo, "_links": {"download": download}, "version": {"when": quando}}


PAGINA_COM_CODIGO = ('<h2>Login</h2><pre>dataLayer.push({event: \'login\'})</pre>')


# ---- cabeçalhos e mensagens ------------------------------------------------------

def test_cabecalho_sem_credenciais_so_identifica_navegador():
    h = confluence._headers_confluence()
    assert "Authorization" not in h
    assert h["Accept"] == "application/json"
    assert h["User-Agent"].startswith("Mozilla/5.0")


def test_cabecalho_com_token_usa_bearer(monkeypatch):
    monkeypatch.setattr(confluence, "CONFLUENCE_TOKEN", "tok-123")
    monkeypatch.setattr(confluence, "CONFLUENCE_USUARIO", "fulano")
    assert confluence._headers_confluence()["Authorization"] == "Bearer tok-123"


def test_cabecalho_com_usuario_e_senha_usa_basic(monkeypatch):
    monkeypatch.setattr(confluence, "CONFLUENCE_USUARIO", "fulano")
    monkeypatch.setattr(confluence, "CONFLUENCE_SENHA", "s3nh@")
    esperado = "Basic " + base64.b64encode(b"fulano:s3nh@").decode("ascii")
    assert confluence._headers_confluence()["Authorization"] == esperado


@pytest.mark.parametrize("status,trecho", [
    (401, "recusou o acesso (HTTP 401)"),
    (403, "recusou o acesso (HTTP 403)"),
    (404, "Página não encontrada"),
    (429, "limitando as requisições (HTTP 429)"),
    (500, "O Confluence respondeu HTTP 500."),
])
def test_mensagens_de_erro_por_status(status, trecho):
    assert trecho in confluence._erro_confluence(status)


def test_detecta_pagina_de_login():
    assert confluence._eh_pagina_login(PAGINA_LOGIN)
    assert confluence._eh_pagina_login('<input name="os_password">')
    assert not confluence._eh_pagina_login("<html><h1>Plano</h1></html>")


def test_carregar_json_invalido_vira_none():
    assert confluence._carregar_json('{"a": 1}') == {"a": 1}
    assert confluence._carregar_json("<html>") is None


# ---- _get_confluence -----------------------------------------------------------------

def test_get_devolve_status_corpo_e_url_final(monkeypatch):
    monkeypatch.setattr(confluence, "CONFLUENCE_TOKEN", "tok")
    abridor = _Abridor(_Resposta(200, b"ok", BASE + "/pages/viewpage.action?pageId=9"))
    monkeypatch.setattr(confluence, "_ABRIR", abridor)

    r = confluence._get_confluence("https://CONFLUENCE.bradesco.com.br:8443/x/a b#frag")

    assert r == (200, b"ok", BASE + "/pages/viewpage.action?pageId=9")
    req, timeout = abridor.pedidos[0]
    assert req.full_url == BASE + "/x/a%20b"
    assert req.get_header("Authorization") == "Bearer tok"
    assert timeout == confluence.CONFLUENCE_TIMEOUT


def test_get_fora_da_origem_nem_monta_requisicao(monkeypatch):
    abridor = _Abridor()
    monkeypatch.setattr(confluence, "_ABRIR", abridor)
    with pytest.raises(ValueError, match="não é do Confluence"):
        confluence._get_confluence("https://exemplo.com/x/abc")
    assert abridor.pedidos == []


def test_get_http_erro_devolve_codigo_e_corpo(monkeypatch):
    monkeypatch.setattr(confluence, "_ABRIR", _Abridor(_http_erro(404, b"nao achei")))
    url = BASE + "/pages/viewpage.action?pageId=1"
    assert confluence._get_confluence(url) == (404, b"nao achei", url)


def test_get_429_respeita_retry_after_e_tenta_de_novo(monkeypatch):
    esperas = []
    monkeypatch.setattr(confluence.time, "sleep", esperas.append)
    abridor = _Abridor(
        _http_erro(429, b'{"message": "rate limit"}',
                   {"Retry-After": "5", "Server": "Confluence", "X-Outro": "ignorado"}),
        _Resposta(200, b"{}", BASE + "/y"))
    monkeypatch.setattr(confluence, "_ABRIR", abridor)

    assert confluence._get_confluence(BASE + "/y") == (200, b"{}", BASE + "/y")
    assert esperas == [5]
    assert len(abridor.pedidos) == 2


def test_get_429_persistente_desiste_e_devolve_429(monkeypatch, capsys):
    esperas = []
    monkeypatch.setattr(confluence.time, "sleep", esperas.append)
    monkeypatch.setattr(confluence, "_ABRIR", _Abridor(
        _http_erro(429, b"<html>WAF</html>", {}),
        _http_erro(429, b"<html>WAF</html>", {"Retry-After": "30"}),
        _http_erro(429, b"<html>WAF\nfim</html>", {})))
    url = BASE + "/z"

    status, corpo, final = confluence._get_confluence(url)

    assert (status, final) == (429, url)
    assert corpo == b"<html>WAF\nfim</html>"
    # sem Retry-After: 2s, 4s...; Retry-After grande é limitado a 10s
    assert esperas == [2, 10]
    assert "HTTP 429" in capsys.readouterr().out


def test_get_429_retry_after_invalido_ou_sem_cabecalhos(monkeypatch):
    esperas = []
    monkeypatch.setattr(confluence.time, "sleep", esperas.append)
    monkeypatch.setattr(confluence, "_ABRIR", _Abridor(
        _http_erro(429, b"", {"Retry-After": "amanha"}),
        _http_erro(429, b"", None),          # sem cabeçalhos (headers None)
        _Resposta(200, b"ok", BASE + "/w")))
    assert confluence._get_confluence(BASE + "/w")[0] == 200
    assert esperas == [2, 4]


def test_get_429_com_cabecalhos_quebrados_e_corpo_ilegivel(monkeypatch):
    class _CabecalhosQuebrados:
        def __bool__(self):
            return True

        def items(self):
            raise RuntimeError("cabeçalho corrompido")

        def get(self, nome, padrao=None):
            return "3"

    class _ErroIlegivel(urllib.error.HTTPError):
        def read(self, *a):
            raise OSError("conexão caiu no meio")

    erro = _ErroIlegivel(BASE + "/v", 429, "erro", _CabecalhosQuebrados(), io.BytesIO(b""))
    esperas = []
    monkeypatch.setattr(confluence.time, "sleep", esperas.append)
    monkeypatch.setattr(confluence, "_ABRIR", _Abridor(erro))

    assert confluence._get_confluence(BASE + "/v", tentativas=1) == (429, b"", BASE + "/v")
    assert esperas == []   # última tentativa: não espera


def test_get_sem_tentativas_devolve_429(monkeypatch):
    abridor = _Abridor()
    monkeypatch.setattr(confluence, "_ABRIR", abridor)
    assert confluence._get_confluence(BASE + "/q", tentativas=0) == (429, b"", BASE + "/q")
    assert abridor.pedidos == []


def test_get_redirect_para_fora_propaga_value_error(monkeypatch):
    monkeypatch.setattr(confluence, "_ABRIR", _Abridor(ValueError("Essa URL não é do Confluence do banco.")))
    with pytest.raises(ValueError, match="não é do Confluence"):
        confluence._get_confluence(BASE + "/x/abc")


@pytest.mark.parametrize("falha", [
    urllib.error.URLError("[Errno 11001] getaddrinfo failed"),
    TimeoutError("timed out"),
    ConnectionResetError("reset"),
])
def test_get_falha_de_conexao_vira_value_error(monkeypatch, falha):
    monkeypatch.setattr(confluence, "_ABRIR", _Abridor(falha))
    with pytest.raises(ValueError, match="Não consegui conectar ao Confluence"):
        confluence._get_confluence(BASE + "/x/abc")


# ---- resolução da página ---------------------------------------------------------------

@pytest.mark.parametrize("url,esperado", [
    (BASE + "/pages/viewpage.action?pageId=123", (BASE, "123", None)),
    pytest.param(
        BASE + "/spaces/ECIS/pages/456/Lista+de+Imagem", (BASE, "456", None),
        marks=pytest.mark.xfail(reason=(
            "bug conhecido: o laço de marcadores para no primeiro da lista ('/pages/'), "
            "não no que aparece antes no caminho, e a base vira .../spaces/ECIS"))),
    (BASE + "/pages/456/Lista+de+Imagem", (BASE, "456", None)),
    ("https://confluence.bradesco.com.br/wiki/pages/viewpage.action?pageId=9",
     ("https://confluence.bradesco.com.br/wiki", "9", None)),
    (BASE + "/display/ECIS/Lista+de+Imagem+Base", (BASE, None, ("ECIS", "Lista de Imagem Base"))),
    (BASE + "/x/AbC", (BASE, None, None)),
    (BASE + "/outra/coisa", (BASE, None, None)),
])
def test_base_e_pagina_a_partir_da_url(url, esperado):
    assert confluence._confluence_base_pagina(url) == esperado


@pytest.mark.parametrize("url", [
    "ftp://confluence.bradesco.com.br:8443/pages/viewpage.action?pageId=1",
    "confluence.bradesco.com.br:8443/pages/viewpage.action?pageId=1",
    "https:///pages/viewpage.action?pageId=1",
])
def test_url_que_nao_parece_do_confluence(url):
    with pytest.raises(ValueError, match="não parece uma URL do Confluence"):
        confluence._confluence_base_pagina(url)


def test_resolver_com_page_id_nao_acessa_o_confluence(conf):
    falso = conf({})
    assert confluence._confluence_resolver_pagina(URL_PAGINA_UI_123) == (BASE, "123")
    assert falso.chamadas == []


URL_DISPLAY = BASE + "/display/ECIS/Lista+de+Imagem"
URL_BUSCA_DISPLAY = BASE + "/rest/api/content?spaceKey=ECIS&title=Lista%20de%20Imagem&limit=1"


def test_resolver_display_pela_busca_rest(conf):
    falso = conf({URL_BUSCA_DISPLAY: (200, _json({"results": [{"id": 777, "title": "x"}]}))})
    assert confluence._confluence_resolver_pagina(URL_DISPLAY) == (BASE, "777")
    assert falso.chamadas == [URL_BUSCA_DISPLAY]


@pytest.mark.parametrize("resposta_busca", [
    (200, _json({"results": []})),
    (200, "isso não é json"),
    (429, ""),
])
def test_resolver_display_sem_rest_le_o_id_da_propria_pagina(conf, resposta_busca):
    pagina = '<meta name="ajs-page-id" content="888"><meta name="ajs-space-key" content="ECIS">'
    falso = conf({URL_BUSCA_DISPLAY: resposta_busca, URL_DISPLAY: (200, pagina)})
    assert confluence._confluence_resolver_pagina(URL_DISPLAY) == (BASE, "888")
    assert falso.chamadas == [URL_BUSCA_DISPLAY, URL_DISPLAY]


def test_resolver_display_com_erro_na_busca(conf):
    conf({URL_BUSCA_DISPLAY: (404, "")})
    with pytest.raises(ValueError, match="HTTP 404"):
        confluence._confluence_resolver_pagina(URL_DISPLAY)


def test_resolver_link_curto_segue_o_redirect(conf):
    curto = BASE + "/x/AbC"
    conf({curto: (200, "<html></html>", BASE + "/pages/viewpage.action?pageId=999")})
    assert confluence._confluence_resolver_pagina(curto) == (BASE, "999")


def test_resolver_link_curto_para_display_usa_meta_da_pagina(conf):
    curto = BASE + "/x/AbC"
    conf({curto: (200, '<meta name="ajs-page-id"   content="4242">', URL_DISPLAY)})
    assert confluence._confluence_resolver_pagina(curto) == (BASE, "4242")


def test_resolver_sem_como_identificar_a_pagina(conf):
    curto = BASE + "/x/AbC"
    conf({curto: (200, "<html>sem id</html>")})
    with pytest.raises(ValueError, match="Não consegui identificar a página"):
        confluence._confluence_resolver_pagina(curto)


# ---- listagem de anexos ---------------------------------------------------------------

def test_anexos_rest_filtra_json_e_ordena_do_mais_recente(conf):
    conf({URL_ANEXOS_123: (200, _json({"results": [
        _anexo("plano-v1.json", "/download/attachments/123/plano-v1.json", "2026-01-01T10:00"),
        _anexo("print.png", "/download/attachments/123/print.png", "2026-03-01T10:00"),
        _anexo("PLANO-V3.JSON", "/download/attachments/123/PLANO-V3.JSON", "2026-03-01T10:00"),
        {"title": "sem-link.json", "_links": None, "version": None},
        {"title": None, "_links": {"download": "/download/attachments/123/x"}},
        {"title": "sem-versao.json", "_links": {"download": "/download/attachments/123/s.json"}},
        _anexo("plano-v2.json", "/download/attachments/123/plano-v2.json", "2026-02-01T10:00"),
    ]}))})

    anexos = confluence._confluence_anexos_json(BASE, "123")

    assert [a["nome"] for a in anexos] == [
        "PLANO-V3.JSON", "plano-v2.json", "plano-v1.json", "sem-versao.json"]
    assert anexos[0] == {"nome": "PLANO-V3.JSON",
                         "download": "/download/attachments/123/PLANO-V3.JSON",
                         "quando": "2026-03-01T10:00"}
    assert anexos[-1]["quando"] == ""


def test_anexos_rest_resposta_invalida_vira_lista_vazia(conf):
    conf({URL_ANEXOS_123: (200, "<html>proxy</html>")})
    assert confluence._confluence_anexos_json(BASE, "123") == []


def test_anexos_rest_429_sinaliza_rate_limit(conf):
    conf({URL_ANEXOS_123: (429, "")})
    with pytest.raises(confluence._RateLimit429):
        confluence._confluence_anexos_json(BASE, "123")


def test_anexos_rest_erro_http(conf):
    conf({URL_ANEXOS_123: (500, "")})
    with pytest.raises(ValueError, match="respondeu HTTP 500"):
        confluence._confluence_anexos_json(BASE, "123")


PAGINA_ANEXOS_UI = """
<table class="tableview attachments">
<tr><td><a href="/download/attachments/123/plano%20v2.json?version=2&amp;modificationDate=1700000000000&amp;api=v2">plano v2.json</a></td></tr>
<tr><td><a href="https://confluence.bradesco.com.br:8443/download/attachments/123/plano%20v2.json?version=1">versão antiga</a></td></tr>
<tr><td><a href="/download/attachments/123/ANTIGO.JSON">ANTIGO.JSON</a></td></tr>
<tr><td><a href="/download/attachments/999/de-outra-pagina.json">outra</a></td></tr>
<tr><td><a href="/download/attachments/123/print.png">print.png</a></td></tr>
</table>
"""


def test_anexos_pela_interface(conf):
    conf({URL_ANEXOS_UI_123: (200, PAGINA_ANEXOS_UI)})
    assert confluence._confluence_anexos_via_ui(BASE, "123") == [
        {"nome": "plano v2.json",
         "download": ("/download/attachments/123/plano%20v2.json?version=2"
                      "&modificationDate=1700000000000&api=v2"),
         "quando": ""},
        {"nome": "ANTIGO.JSON", "download": "/download/attachments/123/ANTIGO.JSON", "quando": ""},
    ]


@pytest.mark.parametrize("resposta", [(403, ""), (200, PAGINA_LOGIN)])
def test_anexos_pela_interface_sem_acesso_vira_lista_vazia(conf, resposta):
    conf({URL_ANEXOS_UI_123: resposta})
    assert confluence._confluence_anexos_via_ui(BASE, "123") == []


# ---- plano a partir do conteúdo da página ----------------------------------------------

def test_pagina_rest_monta_plano_do_body_view(conf):
    conf({URL_PAGINA_REST_123: (200, _json({
        "title": "Plano Extrato",
        "body": {"view": {"value": PAGINA_COM_CODIGO}}}))})

    r = confluence._confluence_pagina_para_plano(BASE, "123")

    assert r["arquivo"]["nome"] == "Plano Extrato.json"
    plano = json.loads(r["arquivo"]["conteudo"])
    assert plano["sections"]["Login"][0]["code_json"] == {"event": "login", "_is_native": False}


def test_pagina_rest_usa_export_view_e_titulo_padrao(conf):
    conf({URL_PAGINA_REST_123: (200, _json({
        "body": {"view": {"value": ""}, "export_view": {"value": PAGINA_COM_CODIGO}}}))})
    assert confluence._confluence_pagina_para_plano(BASE, "123")["arquivo"]["nome"] == \
        "pagina-confluence.json"


@pytest.mark.parametrize("corpo", [_json({"title": "Vazia", "body": {}}), "nao-json"])
def test_pagina_rest_sem_conteudo(conf, corpo):
    conf({URL_PAGINA_REST_123: (200, corpo)})
    with pytest.raises(ValueError, match="sem conteúdo"):
        confluence._confluence_pagina_para_plano(BASE, "123")


def test_pagina_rest_429_e_erro(conf):
    conf({URL_PAGINA_REST_123: (429, "")})
    with pytest.raises(confluence._RateLimit429):
        confluence._confluence_pagina_para_plano(BASE, "123")
    conf({URL_PAGINA_REST_123: (403, "")})
    with pytest.raises(ValueError, match="HTTP 403"):
        confluence._confluence_pagina_para_plano(BASE, "123")


@pytest.mark.parametrize("cabeca,titulo", [
    ('<meta name="ajs-page-title" content="Plano &amp; Tags"><title>Ignorado - Confluence</title>',
     "Plano & Tags"),
    ('<meta name="ajs-page-title" content="  "><title>Meu Plano - Confluence</title>', "Meu Plano"),
    ("<title>\n Só Título \n</title>", "Só Título"),
    ("<title> - Confluence</title>", "pagina-confluence"),
    ("", "pagina-confluence"),
])
def test_pagina_pela_interface_descobre_o_titulo(conf, cabeca, titulo):
    conf({URL_PAGINA_UI_123: (200, "<html><head>" + cabeca + "</head><body>"
                              + PAGINA_COM_CODIGO + "</body></html>")})
    r = confluence._confluence_plano_via_ui(BASE, "123")
    assert r["arquivo"]["nome"] == titulo + ".json"
    assert "Login" in json.loads(r["arquivo"]["conteudo"])["sections"]


def test_pagina_pela_interface_pede_login_sem_usuario(conf):
    falso = conf({URL_PAGINA_UI_123: (200, PAGINA_LOGIN)})
    with pytest.raises(ValueError, match="pediu login"):
        confluence._confluence_plano_via_ui(BASE, "123")
    assert falso.chamadas == [URL_PAGINA_UI_123]


def test_pagina_pela_interface_erro_http_sem_usuario(conf):
    conf({URL_PAGINA_UI_123: (403, "")})
    with pytest.raises(ValueError, match="HTTP 403"):
        confluence._confluence_plano_via_ui(BASE, "123")


def test_pagina_pela_interface_forca_basic_com_usuario(conf, monkeypatch):
    monkeypatch.setattr(confluence, "CONFLUENCE_USUARIO", "fulano")
    basic = URL_PAGINA_UI_123 + "&os_authType=basic"
    falso = conf({URL_PAGINA_UI_123: (200, PAGINA_LOGIN),
                  basic: (200, '<meta name="ajs-page-title" content="Plano">' + PAGINA_COM_CODIGO)})
    assert confluence._confluence_plano_via_ui(BASE, "123")["arquivo"]["nome"] == "Plano.json"
    assert falso.chamadas == [URL_PAGINA_UI_123, basic]


def test_pagina_pela_interface_basic_tambem_recusado(conf, monkeypatch):
    monkeypatch.setattr(confluence, "CONFLUENCE_USUARIO", "fulano")
    conf({URL_PAGINA_UI_123: (401, ""), URL_PAGINA_UI_123 + "&os_authType=basic": (401, "")})
    with pytest.raises(ValueError, match="HTTP 401"):
        confluence._confluence_plano_via_ui(BASE, "123")


# ---- confluence_baixar_json (fluxo completo) ---------------------------------------------

@pytest.mark.parametrize("url", ["", "   ", '""', None])
def test_baixar_sem_url(url):
    with pytest.raises(ValueError, match="Cole a URL da página"):
        confluence.confluence_baixar_json(url)


def test_baixar_link_direto_do_anexo(conf):
    url = BASE + "/download/attachments/123/plano%20tag.json?version=2&api=v2"
    conf({url: (200, '{"sections": {}}')})
    assert confluence.confluence_baixar_json(' "' + url + '" ') == {
        "arquivo": {"nome": "plano tag.json", "conteudo": '{"sections": {}}'}}


def test_baixar_link_direto_com_erro_http(conf):
    url = BASE + "/download/attachments/123/plano.json"
    conf({url: (404, "")})
    with pytest.raises(ValueError, match="Página não encontrada"):
        confluence.confluence_baixar_json(url)


def test_baixar_link_direto_sem_json_valido(conf):
    url = BASE + "/download/attachments/123/?api=v2"
    conf({url: (200, "<html>não é json</html>")})
    with pytest.raises(ValueError, match='O arquivo "plano.json" não contém JSON válido'):
        confluence.confluence_baixar_json(url)


def test_baixar_url_invalida():
    with pytest.raises(ValueError, match="não parece uma URL"):
        confluence.confluence_baixar_json("pagina do plano no confluence")


def test_baixar_unico_anexo_json(conf):
    download = "/download/attachments/123/plano.json?version=1&api=v2"
    falso = conf({
        URL_ANEXOS_123: (200, _json({"results": [
            _anexo("plano.json", download, "2026-01-01"),
            _anexo("tela.png", "/download/attachments/123/tela.png", "2026-02-01")]})),
        BASE + download: (200, '{"sections": {"Home": []}}'),
    })
    r = confluence.confluence_baixar_json(URL_PAGINA_UI_123)
    assert r == {"arquivo": {"nome": "plano.json", "conteudo": '{"sections": {"Home": []}}'}}
    assert falso.chamadas == [URL_ANEXOS_123, BASE + download]


def _rotas_dois_anexos():
    return {URL_ANEXOS_123: (200, _json({"results": [
        _anexo("antigo.json", "/download/attachments/123/antigo.json", "2025-01-01"),
        _anexo("novo.json", "/download/attachments/123/novo.json", "2026-01-01")]}))}


def test_baixar_varios_anexos_devolve_opcoes(conf):
    conf(_rotas_dois_anexos())
    assert confluence.confluence_baixar_json(URL_PAGINA_UI_123) == {
        "opcoes": ["novo.json", "antigo.json"]}


def test_baixar_anexo_escolhido_pelo_nome(conf):
    rotas = _rotas_dois_anexos()
    rotas[BASE + "/download/attachments/123/antigo.json"] = (200, "[1, 2]")
    conf(rotas)
    assert confluence.confluence_baixar_json(URL_PAGINA_UI_123, nome="antigo.json") == {
        "arquivo": {"nome": "antigo.json", "conteudo": "[1, 2]"}}


def test_baixar_anexo_escolhido_inexistente(conf):
    conf(_rotas_dois_anexos())
    with pytest.raises(ValueError, match='Anexo "sumiu.json" não encontrado'):
        confluence.confluence_baixar_json(URL_PAGINA_UI_123, nome="sumiu.json")


def test_baixar_anexo_com_erro_http(conf):
    conf({URL_ANEXOS_123: (200, _json({"results": [
              _anexo("plano.json", "/download/attachments/123/plano.json")]})),
          BASE + "/download/attachments/123/plano.json": (403, "")})
    with pytest.raises(ValueError, match="HTTP 403"):
        confluence.confluence_baixar_json(URL_PAGINA_UI_123)


def test_baixar_anexo_sem_json_valido(conf):
    conf({URL_ANEXOS_123: (200, _json({"results": [
              _anexo("plano.json", "/download/attachments/123/plano.json")]})),
          BASE + "/download/attachments/123/plano.json": (200, "{quebrado")})
    with pytest.raises(ValueError, match='O anexo "plano.json" não contém JSON válido'):
        confluence.confluence_baixar_json(URL_PAGINA_UI_123)


def test_baixar_sem_anexo_monta_plano_da_pagina(conf):
    conf({URL_ANEXOS_123: (200, _json({"results": []})),
          URL_PAGINA_REST_123: (200, _json({"title": "Plano Home",
                                            "body": {"view": {"value": PAGINA_COM_CODIGO}}}))})
    r = confluence.confluence_baixar_json(URL_PAGINA_UI_123)
    assert r["arquivo"]["nome"] == "Plano Home.json"
    assert json.loads(r["arquivo"]["conteudo"])["sections"]["Login"][0]["id"] == 1


def test_baixar_sem_anexo_e_conteudo_rest_bloqueado_le_como_navegador(conf):
    falso = conf({
        URL_ANEXOS_123: (200, _json({"results": []})),
        URL_PAGINA_REST_123: (429, ""),
        URL_PAGINA_UI_123: (200, '<meta name="ajs-page-title" content="Plano UI">'
                                 + PAGINA_COM_CODIGO),
    })
    r = confluence.confluence_baixar_json(URL_PAGINA_UI_123)
    assert r["arquivo"]["nome"] == "Plano UI.json"
    assert falso.chamadas == [URL_ANEXOS_123, URL_PAGINA_REST_123, URL_PAGINA_UI_123]


def test_baixar_rest_bloqueada_lista_anexos_pela_interface(conf, capsys):
    download = "/download/attachments/123/ANTIGO.JSON"
    falso = conf({
        URL_ANEXOS_123: (429, ""),
        URL_ANEXOS_UI_123: (200, '<a href="' + download + '">ANTIGO.JSON</a>'),
        BASE + download: (200, '{"sections": {}}'),
    })
    r = confluence.confluence_baixar_json(URL_PAGINA_UI_123)
    assert r == {"arquivo": {"nome": "ANTIGO.JSON", "conteudo": '{"sections": {}}'}}
    assert falso.chamadas == [URL_ANEXOS_123, URL_ANEXOS_UI_123, BASE + download]
    assert "modo navegador" in capsys.readouterr().out


def test_baixar_rest_bloqueada_sem_anexos_le_a_pagina_como_navegador(conf):
    falso = conf({
        URL_ANEXOS_123: (429, ""),
        URL_ANEXOS_UI_123: (200, "<html>sem anexos</html>"),
        URL_PAGINA_UI_123: (200, "<title>Plano Pix - Confluence</title>" + PAGINA_COM_CODIGO),
    })
    r = confluence.confluence_baixar_json(URL_PAGINA_UI_123)
    assert r["arquivo"]["nome"] == "Plano Pix.json"
    assert URL_PAGINA_REST_123 not in falso.chamadas


def test_baixar_anexos_rest_recusados(conf):
    conf({URL_ANEXOS_123: (401, "")})
    with pytest.raises(ValueError, match="HTTP 401"):
        confluence.confluence_baixar_json(URL_PAGINA_UI_123)


def test_baixar_link_curto_resolve_e_baixa(conf):
    curto = BASE + "/x/AbC"
    download = "/download/attachments/123/plano.json"
    conf({curto: (200, "", URL_PAGINA_UI_123),
          URL_ANEXOS_123: (200, _json({"results": [_anexo("plano.json", download)]})),
          BASE + download: (200, "{}")})
    assert confluence.confluence_baixar_json(curto)["arquivo"]["nome"] == "plano.json"


def test_baixar_ponta_a_ponta_pelo_abridor(monkeypatch):
    """Sem stub de _get_confluence: exercita requisição + resposta juntas."""
    download = "/download/attachments/123/plano.json"
    abridor = _Abridor(
        _Resposta(200, _json({"results": [_anexo("plano.json", download)]}).encode(), URL_ANEXOS_123),
        _Resposta(200, b'{"sections": {}}', BASE + download))
    monkeypatch.setattr(confluence, "_ABRIR", abridor)

    r = confluence.confluence_baixar_json(URL_PAGINA_UI_123)

    assert r == {"arquivo": {"nome": "plano.json", "conteudo": '{"sections": {}}'}}
    assert [req.full_url for req, _ in abridor.pedidos] == [URL_ANEXOS_123, BASE + download]
