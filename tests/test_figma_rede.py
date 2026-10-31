"""Figma: rota até a API (direta ou proxy), descoberta de proxy e opener."""
import socket
import ssl
import types
import urllib.error
import urllib.request

import pytest

from tagauditor import figma, login_google

_ABRIR_ORIGINAL = figma._abrir
_ABRIDOR_ORIGINAL = figma._abridor


@pytest.fixture(autouse=True)
def isolar(monkeypatch):
    monkeypatch.setattr(figma, "_rota_estado",
                        {"testado_em": 0.0, "proxy": None, "ok": False, "anunciada": None})
    monkeypatch.setattr(figma, "PROXY_FIGMA", "")
    monkeypatch.setattr(figma, "FIGMA_API", "https://api.figma.com")

    def sem_rede(*_a, **_k):
        raise AssertionError("teste tentou abrir conexão de verdade")
    monkeypatch.setattr(figma, "_abridor", sem_rede)
    monkeypatch.setattr(login_google, "rota_google", lambda: None)
    monkeypatch.setattr(login_google, "_candidatos_proxy", lambda: [])


@pytest.fixture
def console(monkeypatch):
    linhas = []

    def escrever(modelo="", *valores):
        linhas.append(modelo.format(*valores) if valores else modelo)
    monkeypatch.setattr(figma.console, "escrever", escrever)
    return linhas


@pytest.fixture
def relogio(monkeypatch):
    agora = {"t": 1000.0}
    monkeypatch.setattr(figma, "time", types.SimpleNamespace(time=lambda: agora["t"]))
    return agora


class _Abridor:
    """Opener falso: `comportamento(proxy)` devolve a resposta ou a exceção."""

    def __init__(self, comportamento, chamadas):
        self.comportamento = comportamento
        self.chamadas = chamadas

    def __call__(self, proxy, seguro=False):
        self.proxy, self.seguro = proxy, seguro
        return self

    def open(self, req, timeout=None):
        self.chamadas.append((self.proxy, req.full_url, timeout))
        resultado = self.comportamento(self.proxy)
        if isinstance(resultado, BaseException):
            raise resultado
        return resultado


class _Fechavel:
    def __init__(self):
        self.fechado = False

    def close(self):
        self.fechado = True


def _usar_abridor(monkeypatch, comportamento):
    chamadas = []
    monkeypatch.setattr(figma, "_abridor", _Abridor(comportamento, chamadas))
    return chamadas


# ---- Utilidades ----------------------------------------------------------------

def test_proxy_configurado_completa_o_esquema(monkeypatch):
    assert figma._proxy_configurado() is None
    monkeypatch.setattr(figma, "PROXY_FIGMA", "  proxy.empresa:8080 ")
    assert figma._proxy_configurado() == "http://proxy.empresa:8080"
    monkeypatch.setattr(figma, "PROXY_FIGMA", "https://proxy.empresa:8443")
    assert figma._proxy_configurado() == "https://proxy.empresa:8443"
    monkeypatch.setattr(figma, "PROXY_FIGMA", None)
    assert figma._proxy_configurado() is None


def test_mascarar_esconde_usuario_e_senha_do_proxy():
    assert figma._mascarar("http://joao:s3nha@proxy:8080") == "http://***@proxy:8080"
    assert figma._mascarar("http://proxy:8080") == "http://proxy:8080"
    assert figma._mascarar(None) == ""


def test_host_api(monkeypatch):
    assert figma._host_api() == "api.figma.com"
    monkeypatch.setattr(figma, "FIGMA_API", "https://api.exemplo.com:8443/base")
    assert figma._host_api() == "api.exemplo.com"
    monkeypatch.setattr(figma, "FIGMA_API", "")
    assert figma._host_api() == "api.figma.com"
    monkeypatch.setattr(figma, "FIGMA_API", "https://[api.figma.com")
    assert figma._host_api() == "api.figma.com"


@pytest.mark.parametrize("exc, esperado", [
    (ssl.SSLCertVerificationError("falhou"), True),
    (urllib.error.URLError(ssl.SSLCertVerificationError("x")), True),
    (urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"), True),
    (OSError("CERTIFICATE_VERIFY_FAILED"), True),
    (urllib.error.URLError("Connection refused"), False),
    (socket.timeout("timed out"), False),
])
def test_eh_erro_certificado(exc, esperado):
    assert figma._eh_erro_certificado(exc) is esperado


# ---- Opener --------------------------------------------------------------------

def _handler(opener, classe):
    return [h for h in opener.handlers if isinstance(h, classe)]


def test_abridor_direto_ignora_o_proxy_do_sistema(monkeypatch):
    # Mesmo com proxy no ambiente, o ProxyHandler vazio substitui o padrão
    # (que leria HTTPS_PROXY) e, sem nenhum proxy, nem fica registrado.
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy-do-sistema:3128")
    monkeypatch.setenv("https_proxy", "http://proxy-do-sistema:3128")
    opener = _ABRIDOR_ORIGINAL(None)
    assert _handler(opener, urllib.request.ProxyHandler) == []
    assert not _handler(opener, figma._RedirecionamentoSeguro)
    https = _handler(opener, urllib.request.HTTPSHandler)
    assert https and https[0]._context is figma._CTX_FIGMA


def test_abridor_com_proxy_e_redirecionamento_seguro():
    opener = _ABRIDOR_ORIGINAL("http://proxy:8080", seguro=True)
    proxies = _handler(opener, urllib.request.ProxyHandler)
    assert proxies[0].proxies == {"http": "http://proxy:8080", "https": "http://proxy:8080"}
    assert len(_handler(opener, figma._RedirecionamentoSeguro)) == 1


def test_redirecionamento_para_host_de_imagem_e_seguido():
    req = urllib.request.Request("https://figma-alpha-api.s3.us-west-2.amazonaws.com/img/1")
    novo = figma._RedirecionamentoSeguro().redirect_request(
        req, None, 302, "Found", {}, "https://s3-alpha.figma.com/img/2")
    assert novo.full_url == "https://s3-alpha.figma.com/img/2"


@pytest.mark.parametrize("destino", [
    "http://169.254.169.254/latest/meta-data",
    "https://intranet.bradesco.com.br/x",
    "https://amazonaws.com.exemplo.com/x",
])
def test_redirecionamento_para_fora_dos_hosts_de_imagem_e_recusado(destino):
    req = urllib.request.Request("https://figma-alpha-api.s3.us-west-2.amazonaws.com/img/1")
    with pytest.raises(urllib.error.HTTPError) as exc:
        figma._RedirecionamentoSeguro().redirect_request(req, None, 302, "Found", {}, destino)
    exc.value.close()
    assert exc.value.code == 302


# ---- Teste de rota ---------------------------------------------------------------

def test_testar_rota_responde_true_com_qualquer_resposta(monkeypatch):
    resp = _Fechavel()
    chamadas = _usar_abridor(monkeypatch, lambda proxy: resp)
    assert figma._testar_rota("http://proxy:1") is True
    assert resp.fechado
    assert chamadas == [("http://proxy:1", "https://api.figma.com/v1/me", 6)]


def test_testar_rota_http_403_tambem_e_rota_valida(monkeypatch):
    erro = urllib.error.HTTPError("https://api.figma.com/v1/me", 403, "Forbidden", {}, None)
    _usar_abridor(monkeypatch, lambda proxy: erro)
    assert figma._testar_rota(None) is True


def test_testar_rota_certificado_recusado_e_rota_existente(monkeypatch):
    _usar_abridor(monkeypatch,
                  lambda proxy: urllib.error.URLError(ssl.SSLCertVerificationError("x")))
    assert figma._testar_rota(None) is True


def test_testar_rota_sem_conexao_e_false(monkeypatch):
    _usar_abridor(monkeypatch, lambda proxy: urllib.error.URLError("Connection refused"))
    assert figma._testar_rota(None) is False
    _usar_abridor(monkeypatch, lambda proxy: socket.timeout("timed out"))
    assert figma._testar_rota(None) is False
    _usar_abridor(monkeypatch, lambda proxy: ValueError("qualquer coisa"))
    assert figma._testar_rota(None) is False


# ---- Descoberta --------------------------------------------------------------------

def test_descobrir_rota_prefere_conexao_direta(monkeypatch):
    chamadas = _usar_abridor(monkeypatch, lambda proxy: _Fechavel())
    assert figma._descobrir_rota() == (None, True)
    assert [c[0] for c in chamadas] == [None]


def test_descobrir_rota_usa_proxy_do_login_google_e_do_sistema(monkeypatch):
    monkeypatch.setattr(login_google, "rota_google", lambda: "http://google:8080")
    monkeypatch.setattr(login_google, "_candidatos_proxy",
                        lambda: ["http://google:8080", "http://pac:3128", "http://netsh:80"])
    funciona = "http://pac:3128"
    chamadas = _usar_abridor(
        monkeypatch,
        lambda proxy: _Fechavel() if proxy == funciona else urllib.error.URLError("recusado"))
    assert figma._descobrir_rota() == (funciona, True)
    assert [c[0] for c in chamadas] == [None, "http://google:8080", "http://pac:3128"]


def test_descobrir_rota_sobrevive_a_falhas_do_login_google(monkeypatch):
    def quebra():
        raise RuntimeError("sem registro do Windows")
    monkeypatch.setattr(login_google, "rota_google", quebra)
    monkeypatch.setattr(login_google, "_candidatos_proxy", quebra)
    chamadas = _usar_abridor(monkeypatch, lambda proxy: urllib.error.URLError("recusado"))
    assert figma._descobrir_rota() == (None, False)
    assert [c[0] for c in chamadas] == [None]


def test_descobrir_rota_testa_no_maximo_nove_candidatos(monkeypatch):
    monkeypatch.setattr(login_google, "_candidatos_proxy",
                        lambda: ["http://p{}:80".format(i) for i in range(15)])
    chamadas = _usar_abridor(monkeypatch, lambda proxy: urllib.error.URLError("recusado"))
    assert figma._descobrir_rota() == (None, False)
    assert len(chamadas) == 1 + 9


# ---- Rota (com estado) -------------------------------------------------------------

def test_rota_com_proxy_fixo_anuncia_uma_vez_sem_senha(monkeypatch, console):
    monkeypatch.setattr(figma, "PROXY_FIGMA", "http://joao:s3nha@proxy:8080")
    assert figma._rota() == "http://joao:s3nha@proxy:8080"
    assert figma._rota() == "http://joao:s3nha@proxy:8080"
    assert len(console) == 1
    assert "***@proxy:8080" in console[0] and "s3nha" not in console[0]


def test_rota_descoberta_fica_guardada(monkeypatch, console, relogio):
    descobertas = []

    def descobrir():
        descobertas.append(1)
        return "http://pac:3128", True
    monkeypatch.setattr(figma, "_descobrir_rota", descobrir)
    assert figma._rota() == "http://pac:3128"
    assert figma._rota() == "http://pac:3128"
    assert descobertas == [1]
    assert any("proxy http://pac:3128" in linha for linha in console)


def test_rota_direta_e_anunciada(monkeypatch, console, relogio):
    monkeypatch.setattr(figma, "_descobrir_rota", lambda: (None, True))
    assert figma._rota() is None
    assert any("conexão direta" in linha for linha in console)


def test_rota_inalcancavel_so_testa_de_novo_depois_de_um_minuto(monkeypatch, console, relogio):
    descobertas = []

    def descobrir():
        descobertas.append(1)
        return None, False
    monkeypatch.setattr(figma, "_descobrir_rota", descobrir)
    with pytest.raises(figma.ErroFigma, match="PROXY_FIGMA") as exc:
        figma._rota()
    assert exc.value.status == 502
    assert any("AVISO" in linha for linha in console)

    relogio["t"] += 30          # dentro da janela: nem testa
    with pytest.raises(figma.ErroFigma) as exc:
        figma._rota()
    assert exc.value.status == 502 and descobertas == [1]

    relogio["t"] += 31          # passou de 60s: testa de novo
    with pytest.raises(figma.ErroFigma):
        figma._rota()
    assert descobertas == [1, 1]


def test_esquecer_rota_obriga_nova_descoberta(monkeypatch, relogio, console):
    figma._rota_estado.update(ok=True, proxy="http://pac:3128", testado_em=999.0)
    figma._esquecer_rota()
    assert figma._rota_estado["ok"] is False and figma._rota_estado["testado_em"] == 0.0
    # Sem rota boa guardada, esquecer não mexe no relógio do reteste.
    figma._rota_estado.update(testado_em=500.0)
    figma._esquecer_rota()
    assert figma._rota_estado["testado_em"] == 500.0


def test_abrir_usa_a_rota_atual(monkeypatch):
    usados = []

    class Opener:
        def open(self, req, timeout=None):
            usados.append((req.full_url, timeout))
            return "resposta"

    def abridor(proxy, seguro=False):
        usados.append((proxy, seguro))
        return Opener()
    monkeypatch.setattr(figma, "_rota", lambda: "http://pac:3128")
    monkeypatch.setattr(figma, "_abridor", abridor)
    req = urllib.request.Request("https://api.figma.com/v1/me")
    assert _ABRIR_ORIGINAL(req, 15, seguro=True) == "resposta"
    assert usados == [("http://pac:3128", True), ("https://api.figma.com/v1/me", 15)]
