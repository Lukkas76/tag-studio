"""Login com a conta Google — fluxo de dispositivo (estilo "login de TV").

Funciona em QUALQUER URL (http://maquina:porta, IP interno, sem HTTPS) e sem
página externa: o app mostra um código curto (ex.: ABCD-EFGH), o usuário
digita esse código em google.com/device, autoriza, e o servidor recebe os
dados da conta direto do Google.

Inclui a descoberta da rota até o Google (conexão direta ou proxy corporativo
via registro do Windows/PAC/netsh), usada só no login — as APIs internas do
Bradesco seguem em conexão direta.
"""
import base64
import hashlib
import json
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import (
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, URL_GOOGLE_DEVICE_CODE,
    URL_GOOGLE_TOKEN, PROXY_GOOGLE, EMAILS_AUTORIZADOS, DOMINIOS_AUTORIZADOS,
)
from .rede import CTX_SSL


def _decodificar_jwt_payload(token_jwt):
    """Decodifica o payload (claims) de um JWT SEM validar a assinatura."""
    try:
        parte = token_jwt.split(".")[1]
        parte += "=" * (-len(parte) % 4)
        return json.loads(base64.urlsafe_b64decode(parte.encode("ascii")).decode("utf-8"))
    except Exception:
        return None


def _user_id_tagueamento(email):
    """user_id do mapa de tagueamento: MD5 em HEXA MAIÚSCULO, o mesmo formato do
    exemplo do documento ('BD07D76E9A4F636AE05382A0D10A0F21').

    O mapa define o hash como agência + conta sem dígito, que é o identificador
    dos canais do cliente. O Tag Auditor é ferramenta interna e entra com conta
    Google — não existe agência/conta aqui —, então o hash é do e-mail: mesmo
    formato, ID estável por pessoa e sem expor o e-mail no GA4.
    """
    return hashlib.md5(email.encode("utf-8")).hexdigest().upper()


def _usuario_dos_claims(claims):
    """Valida os claims do id_token (vindo direto do endpoint de token do
    Google, por conexão nossa - por isso dispensa revalidar a assinatura) e
    devolve {email, nome, foto, user_id}. Levanta ValueError com mensagem
    amigável."""
    if not isinstance(claims, dict):
        raise ValueError("O Google não devolveu os dados da conta. Tente entrar de novo.")
    if claims.get("aud") != GOOGLE_CLIENT_ID:
        raise ValueError("Esta credencial pertence a outro aplicativo (client ID não confere).")

    email = (claims.get("email") or "").strip().lower()
    verificado = str(claims.get("email_verified", "")).strip().lower() in ("true", "1")
    if not email or not verificado:
        raise ValueError("A conta Google precisa ter e-mail verificado.")

    if EMAILS_AUTORIZADOS or DOMINIOS_AUTORIZADOS:
        dominio = email.split("@")[-1]
        if email not in EMAILS_AUTORIZADOS and dominio not in DOMINIOS_AUTORIZADOS:
            raise ValueError("A conta {} não está autorizada a usar o Tag Auditor.".format(email))

    return {
        "email": email,
        "nome": (claims.get("name") or "").strip() or email.split("@")[0],
        "foto": claims.get("picture") or "",
        # Vai para o dataLayer (bloco "user" de todo evento do mapa). Só o hash
        # sai daqui para o GA4 — nunca o e-mail.
        "user_id": _user_id_tagueamento(email),
    }


# ---- Rota ate o Google (direta ou via proxy corporativo) ---------------------
# Em rede corporativa a saida para a internet costuma exigir proxy; o navegador
# usa o PAC sozinho, mas o Python nao. Aqui a rota e descoberta e testada 1x.
_trava_proxy = threading.Lock()
_proxy_google = {"testado_em": 0.0, "proxy": None, "ok": False}


def _candidatos_proxy():
    """Lista candidatos a proxy: sistema/env, ProxyServer e PAC do registro do
    Windows e netsh winhttp. Devolve URLs unicas na ordem de descoberta."""
    cands = []
    try:
        px = urllib.request.getproxies()
        for chave in ("https", "http"):
            if px.get(chave):
                cands.append(px[chave])
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as chave:
                try:
                    fixo = str(winreg.QueryValueEx(chave, "ProxyServer")[0])
                except OSError:
                    fixo = ""
                try:
                    pac = str(winreg.QueryValueEx(chave, "AutoConfigURL")[0])
                except OSError:
                    pac = ""
            for parte in fixo.split(";"):
                parte = parte.strip()
                if not parte:
                    continue
                if "=" in parte:
                    esquema, endereco = parte.split("=", 1)
                    if esquema.strip().lower() in ("http", "https"):
                        cands.append(endereco.strip())
                else:
                    cands.append(parte)
            if pac:
                try:
                    with urllib.request.urlopen(pac, timeout=6, context=CTX_SSL) as r:
                        js = r.read().decode("utf-8", errors="replace")
                    cands.extend(re.findall(r"PROXY\s+([A-Za-z0-9_.-]+:\d+)", js))
                except Exception:
                    pass
        except Exception:
            pass
        try:
            saida = subprocess.run(["netsh", "winhttp", "show", "proxy"],
                                   capture_output=True, text=True, timeout=10).stdout or ""
            cands.extend(re.findall(r"([A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+:\d+)", saida))
        except Exception:
            pass
    vistos, unicos = set(), []
    for c in cands:
        c = (c or "").strip()
        if c and "://" not in c:
            c = "http://" + c
        if c and c not in vistos:
            vistos.add(c)
            unicos.append(c)
    return unicos


def _abridor_google(proxy):
    """Opener com a rota dada (proxy ou direta), ignorando o proxy do sistema
    para o resultado ser deterministico."""
    mapa = {"http": proxy, "https": proxy} if proxy else {}
    return urllib.request.build_opener(
        urllib.request.ProxyHandler(mapa),
        urllib.request.HTTPSHandler(context=CTX_SSL))


def _testar_rota_google(proxy):
    """True se o Google responde por esta rota (qualquer resposta HTTP serve)."""
    try:
        _abridor_google(proxy).open(URL_GOOGLE_DEVICE_CODE, timeout=6)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def rota_google():
    """Devolve o proxy a usar com o Google (None = conexão direta).
    Descobre e testa 1x; se nada funcionar, tenta de novo a cada 60s."""
    if PROXY_GOOGLE:
        return PROXY_GOOGLE if "://" in PROXY_GOOGLE else "http://" + PROXY_GOOGLE
    agora = time.time()
    with _trava_proxy:
        if _proxy_google["ok"] or agora - _proxy_google["testado_em"] < 60:
            return _proxy_google["proxy"]
        _proxy_google["testado_em"] = agora

    proxy_ok, ok = None, _testar_rota_google(None)
    if not ok:
        candidatos = _candidatos_proxy()[:8]
        for cand in candidatos:
            if _testar_rota_google(cand):
                proxy_ok, ok = cand, True
                break
    with _trava_proxy:
        _proxy_google["proxy"], _proxy_google["ok"] = proxy_ok, ok
    if ok:
        print("[google] Rota até o Google: {}".format(proxy_ok or "conexão direta"))
    else:
        print("[google] AVISO: Google inalcançável (testei conexão direta e os "
              "proxies do sistema/PAC). Preencha PROXY_GOOGLE no "
              "tagauditor/config.py (ex.: http://proxy.bradesco.com.br:8080).")
    return proxy_ok


def _post_form_google(url, campos, timeout=20):
    """POST x-www-form-urlencoded nos endpoints OAuth do Google.
    Devolve (status, dict); erros HTTP também viram dict (error/error_description).
    Falhas de rede propagam como exceção para o chamador tratar."""
    corpo = urllib.parse.urlencode(campos).encode("ascii")
    req = urllib.request.Request(url, data=corpo, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with _abridor_google(rota_google()).open(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", errors="replace"))
        except Exception:
            return e.code, {}


# Pedidos de login em andamento (fluxo de dispositivo): pedido_id -> dados
_trava_device = threading.Lock()
_pedidos_device = {}
MAX_PEDIDOS_DEVICE = 200


def google_device_inicio():
    """Pede ao Google um código de dispositivo (user_code curto, ex. ABCD-EFGH).
    Guarda o pedido para o polling e devolve o que o navegador precisa mostrar."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise ValueError(
            "Login Google não configurado no servidor: preencha GOOGLE_CLIENT_ID e "
            "GOOGLE_CLIENT_SECRET (cliente 'TVs e dispositivos de entrada limitada') "
            "no tagauditor/config.py.")
    try:
        status, dados = _post_form_google(URL_GOOGLE_DEVICE_CODE, {
            "client_id": GOOGLE_CLIENT_ID,
            "scope": "openid email profile",
        })
    except Exception as e:
        raise ValueError(
            "Não consegui falar com o Google ({}). Veja a linha [google] na "
            "janela preta do servidor; se preciso, preencha PROXY_GOOGLE no "
            "tagauditor/config.py.".format(str(e)[:120]))
    if status != 200 or "device_code" not in dados:
        detalhe = str(dados.get("error_description") or dados.get("error") or "")
        if status in (401, 403) or "invalid_client" in detalhe.lower():
            raise ValueError(
                "O Google não aceitou as chaves. Confirme que o cliente OAuth é do "
                "tipo 'TVs e dispositivos de entrada limitada' e que ID/secret estão "
                "corretos. Detalhe: {}".format(detalhe[:120]))
        raise ValueError("O Google recusou o pedido de código (HTTP {}): {}".format(
            status, detalhe[:150]))

    pedido_id = secrets.token_hex(12)
    agora = time.time()
    with _trava_device:
        for pid in [p for p, d in _pedidos_device.items() if agora > d["expira_em"]]:
            _pedidos_device.pop(pid, None)
        while len(_pedidos_device) >= MAX_PEDIDOS_DEVICE:
            _pedidos_device.pop(next(iter(_pedidos_device)))
        _pedidos_device[pedido_id] = {
            "device_code": dados["device_code"],
            "interval": int(dados.get("interval") or 5),
            "ultimo_poll": 0.0,
            "expira_em": agora + int(dados.get("expires_in") or 1800),
        }
    return {
        "pedido_id": pedido_id,
        "user_code": dados.get("user_code", ""),
        "verification_url": dados.get("verification_url") or "https://www.google.com/device",
    }


def google_device_status(pedido_id):
    """Consulta o andamento de um pedido de login no Google.
    Devolve ("AGUARDANDO", None) | ("OK", usuario) | ("ERRO", mensagem)."""
    agora = time.time()
    with _trava_device:
        pedido = _pedidos_device.get(pedido_id or "")
        if not pedido:
            return "ERRO", "Pedido de login não encontrado. Clique de novo em 'Autenticar no Google'."
        if agora > pedido["expira_em"]:
            _pedidos_device.pop(pedido_id, None)
            return "ERRO", "O código expirou. Clique de novo em 'Autenticar no Google' para gerar outro."
        if agora - pedido["ultimo_poll"] < pedido["interval"]:
            return "AGUARDANDO", None   # respeita o ritmo mínimo exigido pelo Google
        pedido["ultimo_poll"] = agora
        device_code = pedido["device_code"]

    try:
        status, dados = _post_form_google(URL_GOOGLE_TOKEN, {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
    except Exception:
        return "AGUARDANDO", None       # oscilação de rede: tenta na próxima

    if status == 200 and dados.get("id_token"):
        with _trava_device:
            _pedidos_device.pop(pedido_id, None)
        try:
            return "OK", _usuario_dos_claims(_decodificar_jwt_payload(dados["id_token"]))
        except ValueError as e:
            return "ERRO", str(e)

    erro = str(dados.get("error") or "")
    if erro == "authorization_pending":
        return "AGUARDANDO", None
    if erro == "slow_down":
        with _trava_device:
            p = _pedidos_device.get(pedido_id)
            if p:
                p["interval"] += 5
        return "AGUARDANDO", None

    with _trava_device:
        _pedidos_device.pop(pedido_id, None)
    if erro == "access_denied":
        return "ERRO", "Você negou o acesso na página do Google. Tente de novo."
    if erro in ("expired_token", "invalid_grant"):
        return "ERRO", "O código expirou. Clique de novo em 'Autenticar no Google'."
    return "ERRO", "O Google respondeu um erro inesperado ({}).".format(
        str(dados.get("error_description") or erro or "HTTP {}".format(status))[:150])
