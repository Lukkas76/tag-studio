import urllib.request

import pytest

from tagauditor import confluence

BASE = "https://confluence.bradesco.com.br:8443"


@pytest.mark.parametrize("url", [
    BASE + "/pages/viewpage.action?pageId=583205611",
    BASE + "/display/ECIS/Lista+de+Imagem+Base+v2",
    "HTTPS://Confluence.Bradesco.com.br:8443/x/AbC",
    "https://confluence.bradesco.com.br/pages/viewpage.action?pageId=1",
])
def test_origem_do_banco_e_aceita(url):
    assert confluence._origem_permitida(url).startswith("https://confluence.bradesco.com.br")


@pytest.mark.parametrize("url", [
    "https://exemplo.com/pages/viewpage.action?pageId=1",
    "http://169.254.169.254/metadata/instance",
    "http://confluence.bradesco.com.br:8443/pages/viewpage.action?pageId=1",
    "https://confluence.bradesco.com.br.exemplo.com/x/abc",
    "https://usuario@confluence.bradesco.com.br:8443/x/abc",
    "https://confluence.bradesco.com.br:9999/x/abc",
    "file:///etc/passwd",
])
def test_outra_origem_e_recusada(url):
    with pytest.raises(ValueError):
        confluence._origem_permitida(url)


def test_url_remontada_sobre_a_origem_oficial():
    url = "https://CONFLUENCE.bradesco.com.br:8443/display/ECIS/Lista+de+Imagem?x=1#topo"
    assert confluence._url_confluence(url) == BASE + "/display/ECIS/Lista+de+Imagem?x=1"


def test_url_de_fora_nem_chega_a_ser_buscada():
    with pytest.raises(ValueError, match="não é do Confluence"):
        confluence.confluence_baixar_json("https://exemplo.com/download/attachments/1/plano.json")


def test_redirect_para_fora_e_bloqueado():
    req = urllib.request.Request(BASE + "/x/abc")
    with pytest.raises(ValueError):
        confluence._RedirecionamentoSeguro().redirect_request(
            req, None, 302, "Found", {}, "https://exemplo.com/roubar-token")


def test_redirect_dentro_do_confluence_e_seguido():
    req = urllib.request.Request(BASE + "/x/abc")
    novo = confluence._RedirecionamentoSeguro().redirect_request(
        req, None, 302, "Found", {}, BASE + "/pages/viewpage.action?pageId=1")
    assert novo.full_url == BASE + "/pages/viewpage.action?pageId=1"
