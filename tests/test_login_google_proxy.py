"""Rota até o Google: candidatos a proxy (env, registro do Windows, PAC,
netsh), opener, teste de rota e o cache de rota_google(). Sem rede real:
winreg, urlopen, subprocess.run e o opener são stubs."""
import sys
import types
import urllib.error
import urllib.request

import pytest

from tagauditor import login_google

_CHAVE_INTERNET = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


class _ChaveRegistro:
    def __enter__(self):
        return self

    def __exit__(self, *erro):
        return False


def _winreg_falso(valores, erro_ao_abrir=None):
    """Módulo winreg de mentira (o pipeline roda em Linux, onde ele não existe)."""
    modulo = types.ModuleType("winreg")
    modulo.HKEY_CURRENT_USER = object()
    modulo.aberturas = []

    def abrir(raiz, caminho):
        modulo.aberturas.append((raiz, caminho))
        if erro_ao_abrir:
            raise erro_ao_abrir
        return _ChaveRegistro()

    def consultar(chave, nome):
        if nome not in valores:
            raise FileNotFoundError(2, "valor não existe", nome)
        return valores[nome], 1

    modulo.OpenKey = abrir
    modulo.QueryValueEx = consultar
    return modulo


class _Pac:
    def __init__(self, js):
        self.js = js

    def read(self):
        return self.js.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *erro):
        return False


@pytest.fixture
def ambiente(monkeypatch):
    """Sem proxy no sistema, sem PAC e netsh que não deve ser chamado."""
    estado = {"urlopen": [], "netsh": []}
    monkeypatch.setattr(login_google.urllib.request, "getproxies", lambda: {})

    def urlopen(url, timeout=None, context=None):
        estado["urlopen"].append((url, timeout, context))
        raise AssertionError("PAC não deveria ser baixado")
    monkeypatch.setattr(login_google.urllib.request, "urlopen", urlopen)

    def netsh(*args, **kwargs):
        estado["netsh"].append((args, kwargs))
        return types.SimpleNamespace(stdout="")
    monkeypatch.setattr(login_google.subprocess, "run", netsh)
    return estado


# ---- candidatos -------------------------------------------------------------

def test_fora_do_windows_so_vale_o_proxy_do_ambiente(ambiente, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(login_google.urllib.request, "getproxies", lambda: {
        "https": "proxy.a:8080", "http": "http://proxy.b:3128", "ftp": "proxy.c:21"})
    assert login_google._candidatos_proxy() == ["http://proxy.a:8080", "http://proxy.b:3128"]
    assert ambiente["netsh"] == []


def test_proxy_do_ambiente_com_erro_e_ignorado(ambiente, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def quebra():
        raise RuntimeError("registro corrompido")
    monkeypatch.setattr(login_google.urllib.request, "getproxies", quebra)
    assert login_google._candidatos_proxy() == []


def test_windows_junta_registro_pac_e_netsh_sem_repetir(ambiente, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(login_google.urllib.request, "getproxies",
                        lambda: {"http": "proxy.http:80"})
    winreg = _winreg_falso({
        "ProxyServer": "http=proxy.http:80;https=proxy.https:443;ftp=proxy.ftp:21; ;http=;proxy.fixo:8080",
        "AutoConfigURL": "http://pac.interno/proxy.pac",
    })
    monkeypatch.setitem(sys.modules, "winreg", winreg)

    def urlopen(url, timeout=None, context=None):
        ambiente["urlopen"].append((url, timeout, context))
        return _Pac('function FindProxyForURL(u, h) {'
                    ' return "PROXY proxy.pac:8080; PROXY proxy.http:80; DIRECT"; }')
    monkeypatch.setattr(login_google.urllib.request, "urlopen", urlopen)

    def netsh(args, **kwargs):
        ambiente["netsh"].append((args, kwargs))
        return types.SimpleNamespace(
            stdout="Current WinHTTP proxy settings:\n    Proxy Server(s) :  proxy.netsh.corp:3128\n")
    monkeypatch.setattr(login_google.subprocess, "run", netsh)

    assert login_google._candidatos_proxy() == [
        "http://proxy.http:80",
        "http://proxy.https:443",
        "http://proxy.fixo:8080",
        "http://proxy.pac:8080",
        "http://proxy.netsh.corp:3128",
    ]
    assert winreg.aberturas == [(winreg.HKEY_CURRENT_USER, _CHAVE_INTERNET)]
    assert ambiente["urlopen"] == [("http://pac.interno/proxy.pac", 6, login_google.CTX_SSL)]
    args, kwargs = ambiente["netsh"][0]
    assert args == ["netsh", "winhttp", "show", "proxy"]
    assert kwargs["capture_output"] and kwargs["text"] and kwargs["timeout"] == 10


def test_windows_sem_valores_no_registro_e_netsh_sem_saida(ambiente, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _winreg_falso({}))
    monkeypatch.setattr(login_google.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout=None))
    assert login_google._candidatos_proxy() == []
    assert ambiente["urlopen"] == []          # sem AutoConfigURL, sem download de PAC


def test_windows_pac_e_netsh_com_falha_nao_derrubam(ambiente, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _winreg_falso({
        "ProxyServer": "proxy.fixo:8080", "AutoConfigURL": "http://pac.fora/proxy.pac"}))

    def urlopen(url, timeout=None, context=None):
        raise urllib.error.URLError("PAC fora do ar")
    monkeypatch.setattr(login_google.urllib.request, "urlopen", urlopen)

    def netsh(*args, **kwargs):
        raise FileNotFoundError("netsh não existe")
    monkeypatch.setattr(login_google.subprocess, "run", netsh)
    assert login_google._candidatos_proxy() == ["http://proxy.fixo:8080"]


@pytest.mark.parametrize("winreg", [
    None,                                              # import winreg falha
    _winreg_falso({}, erro_ao_abrir=PermissionError("acesso negado")),
])
def test_windows_com_registro_inacessivel_ainda_consulta_o_netsh(ambiente, monkeypatch, winreg):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    monkeypatch.setattr(login_google.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout="Servidor(es) proxy: proxy.netsh.corp:8080"))
    assert login_google._candidatos_proxy() == ["http://proxy.netsh.corp:8080"]


def test_candidato_com_esquema_e_espacos_e_normalizado(ambiente, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(login_google.urllib.request, "getproxies", lambda: {
        "https": "  https://proxy.seguro:443  ", "http": "https://proxy.seguro:443"})
    assert login_google._candidatos_proxy() == ["https://proxy.seguro:443"]


# ---- opener e teste de rota -------------------------------------------------

def _handler(abridor, classe):
    return [h for h in abridor.handlers if isinstance(h, classe)]


def test_abridor_com_proxy_usa_so_ele():
    abridor = login_google._abridor_google("http://proxy.teste:8080")
    proxies = _handler(abridor, urllib.request.ProxyHandler)
    assert len(proxies) == 1
    assert proxies[0].proxies == {"http": "http://proxy.teste:8080",
                                  "https": "http://proxy.teste:8080"}
    assert _handler(abridor, urllib.request.HTTPSHandler)[0]._context is login_google.CTX_SSL


def test_abridor_sem_proxy_ignora_o_proxy_do_sistema():
    # ProxyHandler({}) não abre nada e substitui o ProxyHandler padrão (que
    # leria o proxy do sistema): o opener fica sem nenhum handler de proxy.
    abridor = login_google._abridor_google(None)
    assert _handler(abridor, urllib.request.ProxyHandler) == []
    assert _handler(abridor, urllib.request.HTTPSHandler)[0]._context is login_google.CTX_SSL


class _Abridor:
    def __init__(self, resultado):
        self.resultado = resultado
        self.aberturas = []

    def open(self, url, timeout=None):
        self.aberturas.append((url, timeout))
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


@pytest.mark.parametrize("resultado, esperado", [
    (object(), True),
    (urllib.error.HTTPError("https://x", 405, "Method Not Allowed", None, None), True),
    (urllib.error.URLError("timed out"), False),
    (OSError("sem rede"), False),
])
def test_testar_rota(monkeypatch, resultado, esperado):
    falso = _Abridor(resultado)
    rotas = []
    monkeypatch.setattr(login_google, "_abridor_google",
                        lambda proxy: rotas.append(proxy) or falso)
    assert login_google._testar_rota_google("http://proxy:1") is esperado
    assert rotas == ["http://proxy:1"]
    assert falso.aberturas == [(login_google.URL_GOOGLE_DEVICE_CODE, 6)]


# ---- rota_google ------------------------------------------------------------

@pytest.fixture
def rota(monkeypatch):
    """Cache de rota zerado, relógio controlado e testes de rota anotados."""
    monkeypatch.setattr(login_google, "PROXY_GOOGLE", "")
    monkeypatch.setattr(login_google, "_proxy_google",
                        {"testado_em": 0.0, "proxy": None, "ok": False})
    estado = {"agora": 1_000_000.0, "funcionam": set(), "testadas": [],
              "candidatos": ["http://c{}:80".format(i) for i in range(10)]}
    monkeypatch.setattr(login_google, "time", types.SimpleNamespace(
        time=lambda: estado["agora"], sleep=lambda segundos: None))

    def testar(proxy):
        estado["testadas"].append(proxy)
        return proxy in estado["funcionam"]
    monkeypatch.setattr(login_google, "_testar_rota_google", testar)
    monkeypatch.setattr(login_google, "_candidatos_proxy", lambda: list(estado["candidatos"]))
    return estado


@pytest.mark.parametrize("configurado, esperado", [
    ("proxy.bradesco.com.br:8080", "http://proxy.bradesco.com.br:8080"),
    ("https://proxy.bradesco.com.br:8443", "https://proxy.bradesco.com.br:8443"),
])
def test_proxy_configurado_vence_a_descoberta(rota, monkeypatch, configurado, esperado):
    monkeypatch.setattr(login_google, "PROXY_GOOGLE", configurado)
    assert login_google.rota_google() == esperado
    assert rota["testadas"] == []


def test_conexao_direta_e_guardada(rota, capsys):
    rota["funcionam"] = {None}
    assert login_google.rota_google() is None
    assert "conexão direta" in capsys.readouterr().out
    rota["agora"] += 3600
    assert login_google.rota_google() is None
    assert rota["testadas"] == [None]            # 2a chamada veio do cache


def test_sem_conexao_direta_usa_o_primeiro_proxy_que_responde(rota, capsys):
    rota["funcionam"] = {"http://c2:80", "http://c5:80"}
    assert login_google.rota_google() == "http://c2:80"
    assert rota["testadas"] == [None, "http://c0:80", "http://c1:80", "http://c2:80"]
    assert "Rota até o Google: http://c2:80" in capsys.readouterr().out
    assert login_google.rota_google() == "http://c2:80"
    assert len(rota["testadas"]) == 4


def test_google_inalcancavel_avisa_e_so_tenta_de_novo_depois_de_60s(rota, capsys):
    assert login_google.rota_google() is None
    assert rota["testadas"] == [None] + rota["candidatos"][:8]   # no máximo 8 proxies
    assert "AVISO: Google inalcançável" in capsys.readouterr().out

    rota["agora"] += 30
    assert login_google.rota_google() is None
    assert len(rota["testadas"]) == 9            # dentro dos 60s: não testa de novo

    rota["agora"] += 31
    rota["funcionam"] = {None}
    assert login_google.rota_google() is None
    assert len(rota["testadas"]) == 10
    assert login_google._proxy_google["ok"] is True
