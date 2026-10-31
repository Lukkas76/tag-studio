"""HTTP básico (POST/GET de JSON) para as APIs internas do Bradesco.

TLS sempre COM verificação do certificado e do nome do servidor — CTX_SSL vale
também para o Confluence, o login Google e o Figma. As autoridades
certificadoras vêm do sistema (no Windows, as do Windows, inclusive a
corporativa); a CA interna do banco, se faltar no sistema, entra por
ARQUIVO_CA (config.py).
"""
import json
import ssl
import urllib.error
import urllib.request

from . import console
from .config import ARQUIVO_CA, TIMEOUT_SEGUNDOS

CTX_SSL = ssl.create_default_context()
if ARQUIVO_CA:
    try:
        CTX_SSL.load_verify_locations(cafile=ARQUIVO_CA)
    except (OSError, ssl.SSLError) as _erro_ca:
        console.escrever("[rede] Não consegui carregar a CA de {} ({}); sigo só com as "
                         "CAs do sistema.", ARQUIVO_CA, _erro_ca)


def post_json(url, corpo, headers_extras=None):
    dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=dados, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    for chave, valor in (headers_extras or {}).items():
        req.add_header(chave, valor)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEGUNDOS, context=CTX_SSL) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            corpo_erro = e.read().decode("utf-8", errors="replace")
        except Exception:
            corpo_erro = str(e)
        return e.code, corpo_erro
    except urllib.error.URLError as e:
        raise RuntimeError(
            "Não consegui conectar em {}. Verifique a rede/VPN corporativa. Detalhe: {}".format(url, e.reason)
        )


def get_json(url, headers_extras=None):
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    for chave, valor in (headers_extras or {}).items():
        req.add_header(chave, valor)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEGUNDOS, context=CTX_SSL) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            corpo_erro = e.read().decode("utf-8", errors="replace")
        except Exception:
            corpo_erro = str(e)
        return e.code, corpo_erro
    except urllib.error.URLError as e:
        raise RuntimeError(
            "Não consegui conectar em {}. Verifique a rede/VPN corporativa. Detalhe: {}".format(url, e.reason)
        )
