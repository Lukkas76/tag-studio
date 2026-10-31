"""Figma: chamadas à API (respostas simuladas), erros amigáveis e download do PNG."""
import gzip
import io
import json
import socket
import ssl
import struct
import urllib.error
import zlib

import pytest

from tagauditor import figma

TOKEN = "figd_TOKEN_de_teste_1234"
URL_PNG = "https://figma-alpha-api.s3.us-west-2.amazonaws.com/images/abc"


def _png(largura=2, altura=3):
    """PNG RGB válido, gerado em memória."""
    def bloco(tipo, dados):
        crc = zlib.crc32(tipo + dados) & 0xFFFFFFFF
        return struct.pack(">I", len(dados)) + tipo + dados + struct.pack(">I", crc)
    ihdr = struct.pack(">IIBBBBB", largura, altura, 8, 2, 0, 0, 0)
    linhas = b"".join(b"\x00" + b"\x00\x00\x00" * largura for _ in range(altura))
    return (b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", ihdr)
            + bloco(b"IDAT", zlib.compress(linhas)) + bloco(b"IEND", b""))


class Resposta(io.BytesIO):
    """Resposta HTTP simulada (corpo + cabeçalhos, usável em `with`)."""

    def __init__(self, corpo=b"", headers=None):
        super().__init__(corpo)
        self.headers = headers or {}


class RespostaQuebrada(Resposta):
    def read(self, *_a):
        raise ConnectionResetError("conexão caiu no meio da leitura")


class HTTPErrorQueNaoFecha(urllib.error.HTTPError):
    """close() quebra para o módulo; o teste fecha de verdade com fechar()."""

    def close(self):
        raise OSError("já fechado")

    def fechar(self):
        urllib.error.HTTPError.close(self)


def _http_erro(codigo, corpo=b"", headers=None, classe=urllib.error.HTTPError):
    return classe("https://api.figma.com/x", codigo, "erro", headers or {}, io.BytesIO(corpo))


def _json(dados):
    return json.dumps(dados).encode("utf-8")


@pytest.fixture(autouse=True)
def isolar(monkeypatch):
    monkeypatch.setattr(figma, "FIGMA_TOKEN", "")
    monkeypatch.setattr(figma, "PROXY_FIGMA", "")
    monkeypatch.setattr(figma, "FIGMA_API", "https://api.figma.com")
    monkeypatch.setattr(figma, "FIGMA_TIMEOUT", 60)
    monkeypatch.setattr(figma, "_rota_estado",
                        {"testado_em": 0.0, "proxy": None, "ok": False, "anunciada": None})

    def sem_rede(*_a, **_k):
        raise AssertionError("teste tentou abrir conexão de verdade")
    monkeypatch.setattr(figma, "_abrir", sem_rede)


@pytest.fixture
def console(monkeypatch):
    linhas = []

    def escrever(modelo="", *valores):
        linhas.append(modelo.format(*valores) if valores else modelo)
    monkeypatch.setattr(figma.console, "escrever", escrever)
    return linhas


@pytest.fixture
def servidor(monkeypatch):
    """Troca _abrir: cada chamada consome o próximo item da fila (resposta ou
    exceção) e fica registrada em `pedidos`."""
    estado = {"fila": [], "pedidos": []}

    def abrir(req, timeout, seguro=False):
        estado["pedidos"].append({"req": req, "timeout": timeout, "seguro": seguro})
        item = estado["fila"].pop(0)
        if isinstance(item, BaseException):
            raise item
        return item
    monkeypatch.setattr(figma, "_abrir", abrir)
    return estado


# ---- Leitura do corpo ----------------------------------------------------------

def test_ler_corpo_le_em_blocos():
    assert figma._ler_corpo(Resposta(b"abc" * 10), 100) == b"abc" * 10


def test_ler_corpo_recusa_content_length_acima_do_limite():
    with pytest.raises(figma._GrandeDemais):
        figma._ler_corpo(Resposta(b"x", {"Content-Length": "101"}), 100)


def test_ler_corpo_para_de_ler_ao_passar_do_limite():
    with pytest.raises(figma._GrandeDemais):
        figma._ler_corpo(Resposta(b"x" * 101, {"Content-Length": "  "}), 100)


def test_ler_corpo_descomprime_gzip():
    dados = gzip.compress(b'{"ok": true}')
    assert figma._ler_corpo(Resposta(dados, {"Content-Encoding": "GZIP"}), 100) == b'{"ok": true}'


def test_ler_corpo_sem_cabecalhos():
    class SemCabecalhos(io.BytesIO):
        pass
    assert figma._ler_corpo(SemCabecalhos(b"oi"), 10) == b"oi"


def test_descomprimir_respeita_o_tamanho_descomprimido():
    bomba = gzip.compress(b"0" * 10000)
    assert len(bomba) < 1000
    with pytest.raises(figma._GrandeDemais):
        figma._descomprimir(bomba, 1000)
    assert figma._descomprimir(gzip.compress(b"0" * 1000), 1000) == b"0" * 1000


def test_descomprimir_gzip_corrompido_levanta_zlib_error():
    with pytest.raises(zlib.error):
        figma._descomprimir(b"isto nao e gzip", 1000)


def test_descomprimir_confere_o_que_sobra_no_flush(monkeypatch):
    class Descompressor:
        unconsumed_tail = b""

        def decompress(self, dados, maximo):
            return b"a" * 5

        def flush(self):
            return b"b" * 10
    monkeypatch.setattr(figma.zlib, "decompressobj", lambda *_a: Descompressor())
    with pytest.raises(figma._GrandeDemais):
        figma._descomprimir(b"", 10)


# ---- Mensagens de erro ---------------------------------------------------------

@pytest.mark.parametrize("valor, esperado", [
    (None, "alguns minutos"),
    ("abc", "alguns minutos"),
    ("0", "alguns minutos"),
    ("1", "1 segundo"),
    (" 40 ", "40 segundos"),
    ("89.9", "89 segundos"),
    (300, "5 minutos"),
    (5399, "90 minutos"),
    (3 * 3600, "3 horas"),
    (47 * 3600, "47 horas"),
    (12 * 86400, "12 dias"),
])
def test_formatar_espera(valor, esperado):
    assert figma._formatar_espera(valor) == esperado


def test_dica_tipo_so_para_links_sem_api():
    assert "Slides" in figma._dica_tipo({"tipo": "slides"})
    assert figma._dica_tipo({"tipo": "design"}) == ""
    assert figma._dica_tipo({}) == ""


def test_msg_token_expirado_pessoal_e_do_servidor():
    proprio = figma._msg_token("Invalid token", {"proprio": True})
    assert "o seu token" in proprio and "Gere um novo" in proprio and '"Invalid token"' in proprio
    servidor = figma._msg_token("Token expired", {"proprio": False})
    assert "o token do servidor" in servidor and "renovar o FIGMA_TOKEN" in servidor


def test_msg_token_sem_acesso_ao_arquivo():
    proprio = figma._msg_token("Forbidden", {"proprio": True})
    assert "negou o acesso" in proprio and '("Forbidden")' in proprio
    assert "Peça acesso ao arquivo" in proprio
    servidor = figma._msg_token("", {})
    assert "negou o acesso a este arquivo com o token do servidor." in servidor
    assert "dar acesso ao arquivo à conta do FIGMA_TOKEN" in servidor


def test_erro_407_e_autenticacao_do_proxy():
    e = figma._erro_da_resposta(407, b"", {}, {})
    assert e.status == 502 and e.http == 407 and "HTTP 407" in str(e)


@pytest.mark.parametrize("codigo", [401, 403, 404])
@pytest.mark.parametrize("corpo, cabecalhos", [
    (b"<html><body>Acesso bloqueado pela politica</body></html>", {}),
    (b"bloqueado", {"Content-Type": "text/html; charset=utf-8"}),
])
def test_pagina_html_do_proxy_e_bloqueio_da_rede(codigo, corpo, cabecalhos):
    e = figma._erro_da_resposta(codigo, corpo, cabecalhos, {})
    assert e.status == 502 and e.http == codigo and "bloqueou" in str(e)


def test_403_em_json_e_problema_de_token():
    e = figma._erro_da_resposta(403, _json({"status": 403, "err": "Invalid token"}),
                                {"Content-Type": "application/json"}, {"proprio": True})
    assert e.status == 400 and e.http == 403 and e.detalhe == "Invalid token"
    assert "inválido ou expirou" in str(e)


def test_404_cita_a_conta_e_o_tipo_do_link():
    e = figma._erro_da_resposta(404, _json({"message": "Not found"}), {}, {"tipo": "site"})
    assert e.status == 400 and e.detalhe == "Not found"
    assert "não encontrado" in str(e) and "Figma Slides, Sites" in str(e)
    assert "token do servidor" in str(e)


def test_400_e_413_por_tamanho_sugerem_um_frame():
    e = figma._erro_da_resposta(400, _json({"error": "Request too large"}), {}, {})
    assert e.status == 400 and "Request too large" in str(e) and "grande demais" in str(e)
    e = figma._erro_da_resposta(413, b"", {}, {})
    assert "HTTP 413" in str(e) and "grande demais" in str(e) and e.detalhe == ""


def test_400_comum_traz_o_motivo_do_figma():
    e = figma._erro_da_resposta(400, _json({"err": "Invalid parameter."}), {}, {"tipo": "deck"})
    assert str(e).startswith("O Figma recusou o pedido: Invalid parameter.")
    assert "grande demais" not in str(e) and "Figma Slides" in str(e)


def test_429_explica_a_cota(console):
    cab = {"Retry-After": "120", "X-Figma-Rate-Limit-Type": "LOW", "X-Figma-Plan-Tier": "pro"}
    e = figma._erro_da_resposta(429, b"", cab, {"proprio": False})
    assert e.status == 429 and e.http == 429
    msg = str(e)
    assert "2 minutos" in msg and "View ou Collab" in msg and "todos os usuários" in msg
    assert console and "429" in console[0]


def test_429_em_plano_starter_com_token_pessoal(console):
    e = figma._erro_da_resposta(429, b"", {"X-Figma-Plan-Tier": "starter"}, {"proprio": True})
    msg = str(e)
    assert "Starter" in msg and "alguns minutos" in msg and "todos os usuários" not in msg


def test_429_sem_cabecalhos_extras(console):
    msg = str(figma._erro_da_resposta(429, b"", {"X-Figma-Rate-Limit-Type": "high"},
                                      {"proprio": True}))
    assert "View ou Collab" not in msg and "Starter" not in msg


def test_5xx_na_renderizacao_e_nas_outras_etapas():
    e = figma._erro_da_resposta(500, b"", {}, {"etapa": "imagens"})
    assert e.status == 502 and "gerar a imagem" in str(e)
    e = figma._erro_da_resposta(503, b"nao json", {}, {"etapa": "nos"})
    assert e.status == 502 and "instável" in str(e)


def test_outros_codigos_http():
    e = figma._erro_da_resposta(418, _json({"err": "teapot"}), {}, {})
    assert e.status == 502 and str(e).startswith("O Figma respondeu HTTP 418: teapot.")
    e = figma._erro_da_resposta(302, _json([1, 2]), {}, {})
    assert str(e).startswith("O Figma respondeu HTTP 302. Tente")


# ---- Erros de rede ----------------------------------------------------------------

def test_erro_de_certificado_explica_a_ca_da_empresa():
    for exc in (ssl.SSLCertVerificationError("x"),
                urllib.error.URLError(ssl.SSLCertVerificationError("x"))):
        e = figma._erro_rede(exc)
        assert e.status == 502 and "certificado" in str(e) and "ARQUIVO_CA" in str(e)


@pytest.mark.parametrize("exc", [
    socket.timeout("timed out"),
    urllib.error.URLError(socket.timeout("x")),
    OSError("The read operation timed out"),
])
def test_timeout(exc):
    e = figma._erro_rede(exc)
    assert e.status == 502 and "demorou mais de 60s" in str(e)


def test_407_vindo_da_conexao():
    e = figma._erro_rede(OSError("Tunnel connection failed: 407 Proxy Authentication Required"))
    assert "HTTP 407" in str(e) and e.status == 502


def test_erro_de_conexao_sugere_proxy_e_nunca_mostra_o_token(monkeypatch):
    e = figma._erro_rede(urllib.error.URLError("falhou com " + TOKEN), TOKEN)
    assert TOKEN not in str(e) and "***" in str(e)
    assert "preencha PROXY_FIGMA" in str(e)
    monkeypatch.setattr(figma, "PROXY_FIGMA", "http://joao:s3nha@proxy:8080")
    e = figma._erro_rede(ConnectionRefusedError("recusada"))
    assert "Confira o PROXY_FIGMA configurado (http://***@proxy:8080)" in str(e)
    assert "s3nha" not in str(e)


def test_erro_sem_texto_usa_o_nome_da_excecao():
    assert "(ConnectionResetError)" in str(figma._erro_rede(ConnectionResetError()))


# ---- _api_get ---------------------------------------------------------------------

def test_api_get_monta_o_pedido_e_devolve_o_json(servidor):
    servidor["fila"].append(Resposta(_json({"name": "Arquivo", "status": 200})))
    dados = figma._api_get("/v1/files/CHAVE/nodes", TOKEN, {"ids": "1:2,I3:4;5:6"},
                           {"etapa": "nos"})
    assert dados == {"name": "Arquivo", "status": 200}
    pedido = servidor["pedidos"][0]
    req = pedido["req"]
    assert req.full_url == "https://api.figma.com/v1/files/CHAVE/nodes?ids=1:2,I3:4;5:6"
    assert req.get_method() == "GET" and pedido["timeout"] == 60 and pedido["seguro"] is False
    # Token só no cabeçalho que não é repassado em redirecionamento.
    assert req.unredirected_hdrs.get("X-figma-token") == TOKEN
    assert "X-figma-token" not in req.headers
    assert req.headers.get("Accept-encoding") == "gzip"


def test_api_get_sem_token_e_sem_parametros(servidor):
    servidor["fila"].append(Resposta(gzip.compress(_json({"ok": 1})),
                                     {"Content-Encoding": "gzip"}))
    assert figma._api_get("/v1/me", "", None, {}) == {"ok": 1}
    req = servidor["pedidos"][0]["req"]
    assert req.full_url == "https://api.figma.com/v1/me"
    assert "X-figma-token" not in req.unredirected_hdrs


def test_api_get_repassa_erro_figma_da_rota(servidor):
    servidor["fila"].append(figma.ErroFigma("sem rota", 502))
    with pytest.raises(figma.ErroFigma, match="sem rota"):
        figma._api_get("/v1/me", TOKEN, None, {})


def test_api_get_http_error_vira_mensagem_amigavel(servidor):
    servidor["fila"].append(_http_erro(
        404, _json({"status": 404, "err": "Not found"}), {"Content-Type": "application/json"}))
    with pytest.raises(figma.ErroFigma, match="não encontrado") as exc:
        figma._api_get("/v1/files/X", TOKEN, None, {"proprio": True})
    assert exc.value.http == 404 and exc.value.detalhe == "Not found"


def test_api_get_http_error_com_corpo_grande_e_close_quebrado(servidor):
    erro = _http_erro(403, b"x", {"Content-Length": str(10 * 1024 * 1024)},
                      classe=HTTPErrorQueNaoFecha)
    servidor["fila"].append(erro)
    with pytest.raises(figma.ErroFigma) as exc:
        figma._api_get("/v1/files/X", TOKEN, None, {})
    erro.fechar()
    assert exc.value.http == 403 and exc.value.status == 400


def test_api_get_http_error_sem_cabecalhos(servidor):
    erro = urllib.error.HTTPError("https://api.figma.com/x", 500, "erro", None, io.BytesIO(b""))
    servidor["fila"].append(erro)
    with pytest.raises(figma.ErroFigma, match="instável") as exc:
        figma._api_get("/v1/files/X", TOKEN, None, {})
    assert exc.value.http == 500


def test_api_get_falha_de_conexao_esquece_a_rota(servidor):
    figma._rota_estado.update(ok=True, proxy=None, testado_em=123.0)
    servidor["fila"].append(urllib.error.URLError("Connection refused " + TOKEN))
    with pytest.raises(figma.ErroFigma) as exc:
        figma._api_get("/v1/me", TOKEN, None, {})
    assert exc.value.status == 502 and TOKEN not in str(exc.value)
    assert figma._rota_estado["ok"] is False


def test_api_get_resposta_grande_demais(servidor):
    servidor["fila"].append(Resposta(b"{}", {"Content-Length": str(200 * 1024 * 1024)}))
    with pytest.raises(figma.ErroFigma) as exc:
        figma._api_get("/v1/files/X", TOKEN, None, {})
    assert exc.value.status == 400 and exc.value.http == 413
    assert figma._grande_demais(exc.value)


def test_api_get_gzip_corrompido(servidor):
    servidor["fila"].append(Resposta(b"lixo", {"Content-Encoding": "gzip"}))
    with pytest.raises(figma.ErroFigma) as exc:
        figma._api_get("/v1/me", TOKEN, None, {})
    assert exc.value.status == 502 and "resposta inesperada" in str(exc.value)


def test_api_get_conexao_cai_durante_a_leitura(servidor):
    figma._rota_estado.update(ok=True)
    servidor["fila"].append(RespostaQuebrada(b""))
    with pytest.raises(figma.ErroFigma, match="conexão caiu") as exc:
        figma._api_get("/v1/me", TOKEN, None, {})
    assert exc.value.status == 502 and figma._rota_estado["ok"] is False


@pytest.mark.parametrize("corpo", [b"<html>proxy</html>", b"\xff\xfe\x00", b"[1, 2]", b"null"])
def test_api_get_corpo_que_nao_e_objeto_json(servidor, corpo):
    servidor["fila"].append(Resposta(corpo))
    with pytest.raises(figma.ErroFigma) as exc:
        figma._api_get("/v1/me", TOKEN, None, {})
    assert exc.value.status == 502 and "resposta inesperada" in str(exc.value)


def test_api_get_erro_dentro_de_resposta_200(servidor):
    servidor["fila"].append(Resposta(_json({"status": 400, "err": "Request too large"})))
    with pytest.raises(figma.ErroFigma) as exc:
        figma._api_get("/v1/files/X", TOKEN, None, {})
    assert exc.value.http == 400 and figma._grande_demais(exc.value)


def test_api_get_status_que_nao_e_erro_passa(servidor):
    servidor["fila"].extend([Resposta(_json({"status": True, "err": "x"})),
                             Resposta(_json({"status": 500}))])
    assert figma._api_get("/v1/me", TOKEN, None, {})["status"] is True
    assert figma._api_get("/v1/me", TOKEN, None, {})["status"] == 500


def test_grande_demais():
    assert figma._grande_demais(figma.ErroFigma("x", 400, 413))
    assert figma._grande_demais(figma.ErroFigma("x", 400, 400, "Request Too Large"))
    assert not figma._grande_demais(figma.ErroFigma("x", 400, 400, "outro"))
    assert not figma._grande_demais(ValueError("too large"))


# ---- PNG ----------------------------------------------------------------------------

@pytest.mark.parametrize("url, permitido", [
    (URL_PNG, True),
    ("https://s3-alpha.figma.com/img/1", True),
    ("https://abc.cloudfront.net/x.png", True),
    ("https://x.figmausercontent.com/y", True),
    ("http://figma-alpha-api.s3.amazonaws.com/x", False),
    ("https://intranet.bradesco.com.br/x", False),
    ("https://amazonaws.com.exemplo.net/x", False),
    ("https://[::1/x", False),
    ("https:///semhost", False),
    (None, False),
])
def test_url_imagem_permitida(url, permitido):
    assert figma._url_imagem_permitida(url) is permitido


def test_dimensoes_png():
    assert figma._dimensoes_png(_png(7, 5)) == (7, 5)
    assert figma._dimensoes_png(b"curto") is None
    assert figma._dimensoes_png(b"GIF89a" + b"\x00" * 30) is None
    zerado = bytearray(_png(7, 5))
    zerado[16:20] = b"\x00\x00\x00\x00"
    assert figma._dimensoes_png(bytes(zerado)) is None


def test_baixar_png_sem_token_e_com_redirecionamento_seguro(servidor):
    servidor["fila"].append(Resposta(_png(4, 2)))
    dados, largura, altura = figma._baixar_png(URL_PNG)
    assert dados == _png(4, 2) and (largura, altura) == (4, 2)
    pedido = servidor["pedidos"][0]
    assert pedido["seguro"] is True and pedido["timeout"] == 20
    assert not any(k.lower() == "x-figma-token" for k, _ in pedido["req"].header_items())


def test_baixar_png_usa_o_prazo_menor(servidor, monkeypatch):
    monkeypatch.setattr(figma, "FIGMA_TIMEOUT", 5)
    servidor["fila"].append(Resposta(_png()))
    figma._baixar_png(URL_PNG)
    assert servidor["pedidos"][0]["timeout"] == 5


def test_baixar_png_de_host_estranho_nem_abre_conexao(servidor):
    with pytest.raises(figma.ErroFigma, match="endereço de imagem inesperado") as exc:
        figma._baixar_png("https://169.254.169.254/latest")
    assert exc.value.status == 502 and servidor["pedidos"] == []


def test_baixar_png_erros(servidor):
    nao_fecha = _http_erro(404, b"", classe=HTTPErrorQueNaoFecha)
    servidor["fila"].extend([
        figma.ErroFigma("sem rota", 502),
        Resposta(b"x", {"Content-Length": str(41 * 1024 * 1024)}),
        _http_erro(403, b"AccessDenied"),
        nao_fecha,
        Resposta(b"lixo", {"Content-Encoding": "gzip"}),
        urllib.error.URLError("Connection reset"),
        Resposta(b"<html>bloqueado pelo proxy</html>"),
    ])
    with pytest.raises(figma.ErroFigma, match="sem rota"):
        figma._baixar_png(URL_PNG)
    with pytest.raises(figma.ErroFigma, match="passou de 40 MB") as exc:
        figma._baixar_png(URL_PNG)
    assert exc.value.status == 400
    with pytest.raises(figma.ErroFigma, match="HTTP 403") as exc:
        figma._baixar_png(URL_PNG)
    assert exc.value.status == 502
    with pytest.raises(figma.ErroFigma, match="HTTP 404"):
        figma._baixar_png(URL_PNG)
    nao_fecha.fechar()
    with pytest.raises(figma.ErroFigma, match="resposta inesperada"):
        figma._baixar_png(URL_PNG)
    with pytest.raises(figma.ErroFigma, match="Connection reset"):
        figma._baixar_png(URL_PNG)
    with pytest.raises(figma.ErroFigma, match="não é um PNG válido"):
        figma._baixar_png(URL_PNG)


def test_baixar_png_ou_falha(servidor, console):
    servidor["fila"].extend([
        Resposta(_png(3, 3)),
        urllib.error.URLError("proxy barrou o S3"),
        Resposta(b"x", {"Content-Length": str(41 * 1024 * 1024)}),
    ])
    assert figma._baixar_png_ou_falha(URL_PNG) == (_png(3, 3), 3, 3, None)
    png, largura, altura, falha = figma._baixar_png_ou_falha(URL_PNG)
    assert (png, largura, altura) == (None, None, None) and "proxy barrou o S3" in falha
    assert any("Download da imagem falhou" in linha for linha in console)
    # Imagem grande demais é culpa do frame: continua sendo erro.
    with pytest.raises(figma.ErroFigma) as exc:
        figma._baixar_png_ou_falha(URL_PNG)
    assert exc.value.status == 400
