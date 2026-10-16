"""Tag Planner — telas e camadas direto do Figma (API REST oficial).

A pessoa cola o link do Figma; o servidor devolve a imagem (PNG) de cada frame
e o inventário de camadas dele (textos, botões, campos... com a posição exata),
que vai para a IA junto com a imagem: texto lido do arquivo de design é exato,
OCR não.

Cota: as três rotas usadas aqui (arquivo, nós e imagens) são "Tier 1" no Figma
— poucas chamadas por minuto num assento Dev/Full e poucas por MÊS num assento
View/Collab, contadas pela conta dona do token. Por isso o módulo economiza:
  - link sem node-id (arquivo todo): 1 chamada (arquivo com depth=2) e a lista
    dos frames das páginas para a pessoa escolher;
  - link de página ou seção: 1 chamada (nós) e a lista das telas de dentro
    (página/seção quase nunca é "a tela" que a pessoa quer);
  - frames (do link ou escolhidos na lista): 2 chamadas — camadas (nós) e UMA
    renderização para todos os frames juntos (imagens); os PNGs vêm do S3,
    fora da cota;
  - resultado em cache por FIGMA_CACHE_MIN minutos: repetir a busca é grátis.

Rede: PROXY_FIGMA, se preenchido; senão a conexão direta e, se ela falhar, os
mesmos proxies descobertos para o login Google (linha [figma] no console).

O token (do servidor ou o pessoal digitado na tela) só viaja no cabeçalho
X-Figma-Token das chamadas à API: nunca em mensagens, prints ou logs. Quem
registra a busca na trilha de auditoria é o planner/servidor, não este módulo.
"""
import base64
import copy
import hashlib
import json
import math
import re
import socket
import ssl
import struct
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from .config import (
    FIGMA_TOKEN, FIGMA_API, PROXY_FIGMA, FIGMA_TIMEOUT, FIGMA_ESCALA_MAX,
    FIGMA_LARGURA_ALVO, FIGMA_MAX_FRAMES, FIGMA_MAX_OPCOES, FIGMA_MAX_ELEMENTOS,
    FIGMA_CACHE_MIN, FIGMA_VERIFICAR_TLS,
)
from .rede import CTX_SSL
from . import login_google

# TLS do Figma COM verificação de certificado (o rede.CTX_SSL desliga a
# verificação para as APIs internas do Bradesco, de CA própria). O token do
# Figma não pode ser entregue a quem atender a conexão no meio do caminho.
_CTX_FIGMA = ssl.create_default_context() if FIGMA_VERIFICAR_TLS else CTX_SSL


class ErroFigma(Exception):
    """Erro já com mensagem amigável (pt-BR), pronta para mostrar na tela.

    .status  = código HTTP que o NOSSO servidor deve responder (400, 429, 502, 503)
    .http    = status original devolvido pelo Figma (None se nem chegou lá)
    .detalhe = texto "err" original do Figma (decisões internas, ex.: "too large")
    """

    def __init__(self, mensagem, status=400, http=None, detalhe=None):
        super().__init__(mensagem)
        self.status = status
        self.http = http
        self.detalhe = detalhe


def configurado():
    """True se há token do Figma no servidor (FIGMA_TOKEN). Sem ele a busca
    ainda funciona com o token pessoal que a pessoa digitar na tela."""
    return bool(str(FIGMA_TOKEN or "").strip())


# ---- Limites internos (não são configuração do time) ------------------------
# O Figma reduz sozinho imagens acima de 32 megapixels; pedindo no máximo 30 MP,
# a escala que mandamos é a escala que volta (e as coordenadas batem).
_MAX_PIXELS_IMAGEM = 30e6
# Abaixo de 0,5x os textos de um frame largo ficam ilegíveis para a IA.
_ESCALA_MIN = 0.5
_ESCALA_TETO_API = 4.0           # máximo aceito pela rota de imagens
_MAX_BYTES_JSON = 100 * 1024 * 1024
_MAX_BYTES_ERRO = 256 * 1024
_MAX_BYTES_PNG = 40 * 1024 * 1024
_MAX_BYTES_PNGS = 150 * 1024 * 1024   # soma das imagens de uma busca
_MIN_LADO_ITEM = 2.0             # px do design: menor que isso é fio/divisória
_TOL_LINHA = 8.0                 # px do design: mesma "linha" na ordem de leitura
_MAX_NOME = 80
_MAX_TEXTO = 140
_TEXTOS_ROTULO = 3               # textos internos que viram o rótulo de um botão/card
_CACHE_MAX_ITENS = 20
_CACHE_MAX_BYTES = 150 * 1024 * 1024
_MEMO_MAX_ARQUIVOS = 50
_MEMO_MAX_IDS = 5000
_RETESTE_ROTA_SEG = 60
_USER_AGENT = "TagStudio-TagPlanner/1.0 (Python urllib)"

_TIPOS_OPCAO = frozenset(("FRAME", "COMPONENT", "COMPONENT_SET", "INSTANCE", "SECTION", "GROUP"))
_TIPOS_PAGINA = frozenset(("DOCUMENT", "CANVAS"))
_TIPOS_CONTEINER = frozenset(("DOCUMENT", "CANVAS", "SECTION"))
_TIPOS_POR_NOME = frozenset(("INSTANCE", "COMPONENT", "FRAME", "GROUP"))
_TIPOS_QUE_CORTAM = frozenset(("FRAME", "COMPONENT", "COMPONENT_SET", "INSTANCE"))
# Gatilhos que disparam sozinhos (tela de abertura que avança, fim de vídeo):
# não fazem do elemento algo em que a pessoa toca.
_GATILHOS_AUTOMATICOS = frozenset(("AFTER_TIMEOUT", "ON_MEDIA_HIT", "ON_MEDIA_END"))
# Para onde o Figma manda baixar o PNG renderizado (links do S3/CDN). Qualquer
# outro host é recusado: o servidor não pode virar ponte para a rede interna.
_HOSTS_IMAGEM = (".amazonaws.com", ".figma.com", ".figmausercontent.com", ".cloudfront.net")
_ASSINATURA_PNG = b"\x89PNG\r\n\x1a\n"


# ---- Textos ------------------------------------------------------------------

def _limpar(texto, limite):
    """Uma linha só (quebras e espaços repetidos viram um espaço), cortada em
    `limite` caracteres com reticências."""
    t = " ".join(str(texto or "").split())
    if len(t) > limite:
        t = t[:max(1, limite - 1)].rstrip() + "…"
    return t


def _sem_token(texto, token):
    """Garante que o token nunca vaze num texto montado a partir de exceções."""
    texto = str(texto or "")
    if token and len(token) >= 4 and token in texto:
        texto = texto.replace(token, "***")
    return texto


def _plural(n, singular, plural):
    return "{} {}".format(n, singular if n == 1 else plural)


_DICA_FRAME = ("Cole o link de um frame específico em vez do arquivo ou da página "
               "inteira (selecione o frame no Figma e copie o link da seleção).")
_DICA_UPLOAD = ('Enquanto isso, exporte a tela do Figma como PNG e use a aba '
                '"Enviar imagem".')
_MSG_RESPOSTA_ESTRANHA = (
    "O Figma (ou o proxy da rede) devolveu uma resposta inesperada. Tente de novo; "
    "se persistir, veja a linha [figma] na janela do servidor.")


# ---- Link do Figma -> chave do arquivo + ids dos nós -------------------------
_MSG_URL = "Cole o link de um arquivo do Figma (figma.com/design/...)."
# Id de nó: "12:34"; camada dentro de instância: "I12:34;56:78" (mesma regra
# do planner.py, para as duas camadas não discordarem sobre o mesmo id).
_RE_ID_NO = re.compile(r"^I?\d+:\d+(?:;I?\d+:\d+)*$")
# "0:1" é a 1ª página do arquivo — o node-id que vem no link copiado da barra
# do navegador quando nada está selecionado (o caso mais comum de link).
_RE_ID_PAGINA = re.compile(r"^0:\d+$")
# A doc oficial cita 22-128 caracteres, mas o próprio exemplo dela tem 17.
_RE_CHAVE = re.compile(r"^[0-9A-Za-z]{10,128}$")
_RE_TIPO_URL = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")
_RE_SEM_ESQUEMA = re.compile(r"^(?:[a-z0-9-]+\.)*figma\.com(?:[/?#]|$)", re.IGNORECASE)
# Caminhos de figma.com que não são arquivo (navegação, times, perfis...). Tipo
# desconhecido fora desta lista é aceito: o Figma avisa que surgem tipos novos.
_CAMINHOS_NAO_ARQUIVO = frozenset((
    "files", "file-browser", "team", "teams", "project", "projects", "settings",
    "plugins", "plugin", "widget", "widgets", "developers", "developer", "exit",
    "login", "signup", "logout", "pricing", "blog", "api", "oauth", "embed",
    "resource", "resources", "templates", "template", "education", "downloads",
    "download", "community", "c", "search", "notifications", "admin", "org",
))
# Tipos cujo conteúdo a API REST pode não entregar (só figma/figjam é garantido).
_TIPOS_SEM_API = frozenset(("make", "site", "sites", "slides", "deck", "buzz"))
_RE_TOKEN = re.compile(r"^[\x21-\x7e]{8,512}$")


def _normalizar_id(bruto):
    """node-id do link -> id da API: decodifica %3A (às vezes vem codificado
    duas vezes) e troca o hífen do link ("12-34") pelos dois-pontos ("12:34")."""
    texto = str(bruto or "").strip()
    for _ in range(2):
        if "%" not in texto:
            break
        texto = urllib.parse.unquote(texto)
    texto = texto.replace("-", ":")
    return texto if _RE_ID_NO.match(texto) else None


def _analisar(url, _aninhado=False):
    """Devolve (chave, [ids], tipo) — tipo = design|file|proto|board|...
    Levanta ErroFigma(400) com mensagem amigável para qualquer outra coisa."""
    texto = str(url or "").strip().strip("'\"<>").strip()
    if not texto:
        raise ErroFigma("Cole o link do Figma (figma.com/design/...).", 400)
    if len(texto) > 4096:
        raise ErroFigma(_MSG_URL, 400)
    if "://" not in texto and _RE_SEM_ESQUEMA.match(texto):
        # Colado sem o "https://": o Figma só existe em https, então completa.
        # Este link nunca é baixado — só extraímos dele a chave e os ids.
        texto = "https://" + texto
    try:
        partes = urllib.parse.urlsplit(texto)
        host = (partes.hostname or "").lower().rstrip(".")
    except ValueError:
        raise ErroFigma(_MSG_URL, 400)
    if not (host == "figma.com" or host.endswith(".figma.com")):
        raise ErroFigma(_MSG_URL, 400)
    if partes.scheme.lower() != "https":
        raise ErroFigma("Use o link https:// do Figma (figma.com/design/...).", 400)

    segmentos = [urllib.parse.unquote(s) for s in partes.path.split("/") if s]
    consulta = urllib.parse.parse_qs(partes.query)
    # Embed antigo: figma.com/embed?embed_host=...&url=<link do arquivo>
    if not _aninhado and segmentos[:1] == ["embed"] and consulta.get("url"):
        return _analisar(consulta["url"][0], True)
    if len(segmentos) < 2:
        raise ErroFigma(_MSG_URL, 400)
    tipo = segmentos[0].lower()
    if tipo == "community":
        raise ErroFigma(
            "Arquivos da Comunidade do Figma não são lidos direto pela API: abra o "
            "arquivo, duplique-o para a sua conta e cole o link da cópia.", 400)
    if tipo in _CAMINHOS_NAO_ARQUIVO or not _RE_TIPO_URL.match(tipo):
        raise ErroFigma(_MSG_URL, 400)
    chave = segmentos[1]
    # Branch: figma.com/design/:chave/branch/:chaveDaBranch/:nome -> a API
    # aceita a chave da branch no lugar da chave do arquivo.
    if len(segmentos) >= 4 and segmentos[2].lower() == "branch":
        chave = segmentos[3]
    # Chave é base62 aleatória; só dígitos é id de time/projeto, não arquivo.
    if not _RE_CHAVE.match(chave) or chave.isdigit():
        raise ErroFigma(_MSG_URL, 400)

    brutos = consulta.get("node-id", []) + consulta.get("starting-point-node-id", [])
    if not brutos and "node-id=" in partes.fragment:
        frag = urllib.parse.parse_qs(partes.fragment)
        brutos = frag.get("node-id", []) + frag.get("starting-point-node-id", [])
    ids = []
    for bruto in brutos:
        id_no = _normalizar_id(bruto)
        if not id_no:
            raise ErroFigma(
                'O node-id do link ("{}") não é válido. Copie de novo o link do frame '
                'no Figma.'.format(_limpar(bruto, 40)), 400)
        if id_no not in ids:
            ids.append(id_no)
    return chave, ids, tipo


def analisar_url(url):
    """Link do Figma -> (chave_do_arquivo, [ids_dos_nós]).

    Aceita design/file/proto/board/slides/deck e tipos futuros, links de branch
    (usa a chave da branch), embed (embed.figma.com e o embed antigo com ?url=)
    e node-id/starting-point-node-id ("12-34" ou "12%3A34" -> "12:34").
    Levanta ErroFigma(400) com mensagem amigável para qualquer outra coisa."""
    chave, ids, _tipo = _analisar(url)
    return chave, ids


def _validar_ids(ids):
    """Ids escolhidos na lista (explícitos): normaliza, tira repetidos e
    confere o limite de frames por busca."""
    if ids is None or ids == "":
        return []
    if isinstance(ids, str):
        brutos = ids.split(",")
    elif isinstance(ids, (list, tuple)):
        brutos = list(ids)
    else:
        raise ErroFigma("Lista de frames inválida. Busque a lista de frames de novo.", 400)
    saida, invalidos = [], []
    for bruto in brutos:
        if isinstance(bruto, str) and not bruto.strip():
            continue
        id_no = _normalizar_id(bruto) if isinstance(bruto, str) else None
        if not id_no:
            invalidos.append(_limpar(bruto, 30))
        elif id_no not in saida:
            saida.append(id_no)
    if invalidos:
        raise ErroFigma(
            "Identificador de frame inválido ({}). Busque a lista de frames de "
            "novo.".format(", ".join(invalidos[:3])), 400)
    if len(saida) > FIGMA_MAX_FRAMES:
        raise ErroFigma("Escolha no máximo {} por busca (você marcou {}).".format(
            _plural(FIGMA_MAX_FRAMES, "frame", "frames"), len(saida)), 400)
    return saida


def _escolher_token(token_usuario):
    """Token pessoal digitado na tela ou, na falta dele, o do servidor.
    Devolve (token, True se é o pessoal)."""
    pessoal = str(token_usuario or "").strip()
    token = pessoal or str(FIGMA_TOKEN or "").strip()
    if not token:
        raise ErroFigma(
            'A busca no Figma precisa de um token. Informe o seu token pessoal no '
            'campo "Seu token do Figma" (no Figma: Settings > Security > Personal '
            'access tokens, escopo "File content: Read only") ou peça ao '
            'administrador para preencher o FIGMA_TOKEN no tagauditor/config.py. '
            'Você também pode exportar a tela do Figma como PNG e usar a aba '
            '"Enviar imagem".', 503)
    if not _RE_TOKEN.match(token):
        # Não ecoa o valor: além de segredo, caractere de controle no cabeçalho
        # faria o http.client levantar um erro COM o token no texto.
        if pessoal:
            raise ErroFigma(
                'O token do Figma informado não é válido: copie de novo o token '
                'completo (ele costuma começar com "figd_"), sem espaços.', 400)
        raise ErroFigma(
            "O FIGMA_TOKEN configurado no servidor não é válido (tem espaços ou "
            "caracteres estranhos). Peça ao administrador para conferir o "
            "tagauditor/config.py, ou informe o seu próprio token.", 503)
    return token, bool(pessoal)


# ---- Rota até a API do Figma (direta ou via proxy corporativo) ---------------
# Mesma ideia da rota do login Google: o navegador usa o PAC sozinho, o Python
# não. A rota é descoberta e testada 1x; enquanto nada funciona, só testa de
# novo depois de 60s (para não travar cada busca em vários timeouts).
_trava_rota = threading.Lock()
_trava_descoberta = threading.Lock()
_rota_estado = {"testado_em": 0.0, "proxy": None, "ok": False, "anunciada": None}


def _proxy_configurado():
    p = str(PROXY_FIGMA or "").strip()
    if not p:
        return None
    return p if "://" in p else "http://" + p


def _mascarar(proxy):
    """Proxy para exibir (console/mensagem) sem usuário e senha."""
    return re.sub(r"//[^/@]*@", "//***@", str(proxy or ""))


def _host_api():
    try:
        return urllib.parse.urlsplit(FIGMA_API).hostname or "api.figma.com"
    except ValueError:
        return "api.figma.com"


class _RedirecionamentoSeguro(urllib.request.HTTPRedirectHandler):
    """No download do PNG, só segue redirecionamento para os hosts de imagem
    permitidos (o mesmo filtro do link original)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _url_imagem_permitida(newurl):
            raise urllib.error.HTTPError(newurl, code, "redirecionamento recusado", headers, fp)
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl)


def _abridor(proxy, seguro=False):
    """Opener com a rota dada (proxy ou direta), ignorando o proxy do sistema
    para o resultado ser determinístico."""
    mapa = {"http": proxy, "https": proxy} if proxy else {}
    handlers = [urllib.request.ProxyHandler(mapa),
                urllib.request.HTTPSHandler(context=_CTX_FIGMA)]
    if seguro:
        handlers.append(_RedirecionamentoSeguro())
    return urllib.request.build_opener(*handlers)


def _testar_rota(proxy):
    """True se a API do Figma responde por esta rota (qualquer resposta HTTP
    serve — sem token, /v1/me devolve 403 na hora e não gasta cota)."""
    req = urllib.request.Request(FIGMA_API.rstrip("/") + "/v1/me", method="GET")
    req.add_header("User-Agent", _USER_AGENT)
    try:
        _abridor(proxy).open(req, timeout=6).close()
        return True
    except urllib.error.HTTPError as e:
        e.close()
        return True
    except urllib.error.URLError as e:
        # Certificado recusado = a rota EXISTE; quem explica o problema de TLS
        # é a chamada de verdade (_erro_rede), não "rede inalcançável".
        return _eh_erro_certificado(e)
    except Exception:
        return False


def _eh_erro_certificado(exc):
    razao = getattr(exc, "reason", None)
    if isinstance(exc, ssl.SSLCertVerificationError) or isinstance(razao, ssl.SSLCertVerificationError):
        return True
    return "CERTIFICATE_VERIFY_FAILED" in str(razao or exc)


def _descobrir_rota():
    """Conexão direta; senão o proxy que funcionou no login Google; senão os
    candidatos do sistema (registro do Windows, PAC, netsh). (proxy, ok)."""
    if _testar_rota(None):
        return None, True
    candidatos = []
    try:
        proxy_google = login_google.rota_google()
        if proxy_google:
            candidatos.append(proxy_google)
    except Exception:
        pass
    try:
        for cand in login_google._candidatos_proxy():
            if cand not in candidatos:
                candidatos.append(cand)
    except Exception:
        pass
    for cand in candidatos[:9]:
        if _testar_rota(cand):
            return cand, True
    return None, False


def _msg_sem_rota():
    return ("Não consegui alcançar a API do Figma ({}) a partir do servidor: testei a "
            "conexão direta e os proxies do sistema. Se a rede exigir proxy, peça ao "
            "administrador para preencher PROXY_FIGMA no tagauditor/config.py (ex.: "
            "http://proxy.bradesco.com.br:8080). {}".format(_host_api(), _DICA_UPLOAD))


def _rota():
    """Proxy a usar com o Figma (None = conexão direta)."""
    fixo = _proxy_configurado()
    if fixo:
        with _trava_rota:
            anunciar = _rota_estado["anunciada"] != fixo
            _rota_estado["anunciada"] = fixo
        if anunciar:
            print("[figma] Rota até a API do Figma: proxy {} (PROXY_FIGMA)".format(_mascarar(fixo)))
        return fixo
    # Uma descoberta por vez: buscas simultâneas esperam o resultado da
    # primeira em vez de testarem todos os proxies em paralelo.
    with _trava_descoberta:
        with _trava_rota:
            if _rota_estado["ok"]:
                return _rota_estado["proxy"]
            if time.time() - _rota_estado["testado_em"] < _RETESTE_ROTA_SEG:
                raise ErroFigma(_msg_sem_rota(), 502)
        proxy, ok = _descobrir_rota()
        with _trava_rota:
            _rota_estado.update(testado_em=time.time(), proxy=proxy, ok=ok)
    if ok:
        print("[figma] Rota até a API do Figma: {}".format(
            "proxy " + _mascarar(proxy) if proxy else "conexão direta"))
        return proxy
    print("[figma] AVISO: API do Figma inalcançável (testei conexão direta, o proxy do "
          "login Google e os proxies do sistema/PAC). Preencha PROXY_FIGMA no "
          "tagauditor/config.py (ex.: http://proxy.bradesco.com.br:8080).")
    raise ErroFigma(_msg_sem_rota(), 502)


def _esquecer_rota():
    """Falha de rede numa chamada: a próxima busca testa a rota de novo (a VPN
    ou o proxy podem ter mudado desde a descoberta)."""
    with _trava_rota:
        if _rota_estado["ok"]:
            _rota_estado.update(ok=False, testado_em=0.0)


def _abrir(req, timeout, seguro=False):
    """Único ponto de rede do módulo: abre a requisição pela rota até o Figma.
    (Os testes trocam esta função por respostas simuladas.)"""
    return _abridor(_rota(), seguro).open(req, timeout=timeout)


# ---- HTTP: leitura, erros amigáveis -----------------------------------------

class _GrandeDemais(Exception):
    """Corpo da resposta acima do limite aceito."""


def _descomprimir(dados, limite):
    """gzip com teto no tamanho DESCOMPRIMIDO (zlib.error se vier corrompido)."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    saida = d.decompress(dados, limite + 1)
    if len(saida) > limite or d.unconsumed_tail:
        raise _GrandeDemais()
    saida += d.flush()
    if len(saida) > limite:
        raise _GrandeDemais()
    return saida


def _ler_corpo(resp, limite):
    """Lê a resposta em blocos, sem passar de `limite` bytes."""
    cab = getattr(resp, "headers", None) or {}
    tamanho = str(cab.get("Content-Length") or "").strip()
    if tamanho.isdigit() and int(tamanho) > limite:
        raise _GrandeDemais()
    partes, total = [], 0
    while True:
        bloco = resp.read(1024 * 1024)
        if not bloco:
            break
        total += len(bloco)
        if total > limite:
            raise _GrandeDemais()
        partes.append(bloco)
    dados = b"".join(partes)
    if "gzip" in str(cab.get("Content-Encoding") or "").lower():
        dados = _descomprimir(dados, limite)
    return dados


def _quem(ctx):
    return "o seu token" if ctx.get("proprio") else "o token do servidor"


def _msg_token(err, ctx):
    """403/401: token inválido/expirado ou conta sem acesso ao arquivo."""
    proprio = ctx.get("proprio")
    baixo = (err or "").lower()
    if "token" in baixo or "expired" in baixo:
        if proprio:
            acao = ('Gere um novo em Figma > Settings > Security > Personal access '
                    'tokens (escopo "File content: Read only") e cole no campo "Seu '
                    'token do Figma".')
        else:
            acao = ('Peça ao administrador para renovar o FIGMA_TOKEN no '
                    'tagauditor/config.py ou informe o seu próprio token no campo "Seu '
                    'token do Figma".')
        return ("O Figma recusou {} (\"{}\"): ele é inválido ou expirou — tokens "
                "pessoais do Figma valem no máximo 90 dias. {}").format(_quem(ctx), err, acao)
    if proprio:
        acao = ("Peça acesso ao arquivo para a conta do seu token, ou gere um token "
                "novo com o escopo certo.")
    else:
        acao = ('Peça ao administrador para dar acesso ao arquivo à conta do '
                'FIGMA_TOKEN (ou renovar o token), ou informe o seu próprio token no '
                'campo "Seu token do Figma".')
    return ("O Figma negou o acesso a este arquivo com {}{}. A conta dona do token "
            "precisa conseguir abrir o arquivo no Figma, e o token precisa estar "
            "dentro da validade (no máximo 90 dias) e ter o escopo \"File content: "
            "Read only\". {}").format(_quem(ctx), ' ("{}")'.format(err) if err else "", acao)


def _dica_tipo(ctx):
    if ctx.get("tipo") in _TIPOS_SEM_API:
        return (" Links de Figma Slides, Sites, Make e Buzz podem não ser lidos pela "
                "API: use o link de um arquivo de design (figma.com/design/...).")
    return ""


def _formatar_espera(valor):
    """Retry-After (segundos) -> "40 segundos", "5 minutos", "3 horas", "12 dias"."""
    try:
        seg = int(float(str(valor).strip()))
    except (TypeError, ValueError):
        seg = 0
    if seg <= 0:
        return "alguns minutos"
    if seg < 90:
        return _plural(seg, "segundo", "segundos")
    if seg < 90 * 60:
        return _plural(int(math.ceil(seg / 60.0)), "minuto", "minutos")
    if seg < 48 * 3600:
        return _plural(int(math.ceil(seg / 3600.0)), "hora", "horas")
    return _plural(int(math.ceil(seg / 86400.0)), "dia", "dias")


def _msg_limite(cabecalhos, ctx):
    """429: cota da API do Figma. O tipo do limite diz se a conta tem assento
    View/Collab ("low": poucas chamadas por MÊS) ou Dev/Full ("high")."""
    espera = cabecalhos.get("Retry-After")
    tipo_limite = str(cabecalhos.get("X-Figma-Rate-Limit-Type") or "").strip().lower()
    plano = str(cabecalhos.get("X-Figma-Plan-Tier") or "").strip().lower()
    print("[figma] HTTP 429 (limite da API) | Retry-After: {} | plano: {} | tipo: {}".format(
        espera or "-", plano or "-", tipo_limite or "-"))
    msg = "O Figma atingiu o limite de chamadas da API para {}. Tente de novo em {}.".format(
        _quem(ctx), _formatar_espera(espera))
    if tipo_limite == "low":
        msg += (" A conta dona do token tem assento View ou Collab: nessa API o Figma "
                "libera só poucas chamadas por MÊS. Para usar a busca no dia a dia, essa "
                "conta precisa de um assento Dev ou Full no plano pago dono do arquivo.")
    elif plano == "starter":
        msg += (" O arquivo está num plano Starter (gratuito), que libera só poucas "
                "chamadas por MÊS nessa API: o arquivo precisa estar no plano pago da equipe.")
    if not ctx.get("proprio"):
        msg += " O token do servidor é um só: todos os usuários dividem essa cota."
    return msg + " " + _DICA_UPLOAD


def _erro_da_resposta(codigo, corpo, cabecalhos, ctx):
    """Resposta de erro do Figma -> ErroFigma com mensagem amigável."""
    texto = corpo.decode("utf-8", errors="replace") if corpo else ""
    try:
        dados = json.loads(texto) if texto else None
    except ValueError:
        dados = None
    err = ""
    if isinstance(dados, dict):
        err = _limpar(dados.get("err") or dados.get("message") or dados.get("error") or "", 200)
    tipo_conteudo = str(cabecalhos.get("Content-Type") or "").lower()
    eh_html = not isinstance(dados, dict) and (
        "html" in tipo_conteudo or "<html" in texto[:3000].lower())

    if codigo == 407:
        return ErroFigma(
            "O proxy da rede pediu autenticação (HTTP 407) para acessar o Figma. Peça "
            "ao administrador para preencher PROXY_FIGMA no tagauditor/config.py com "
            "um proxy liberado para {}.".format(_host_api()), 502, codigo, err)
    if codigo in (401, 403, 404) and eh_html:
        # O Figma responde erro em JSON; página HTML é bloqueio do proxy/firewall.
        return ErroFigma(
            "A rede bloqueou o acesso à API do Figma (HTTP {} com página do proxy). "
            "Peça a liberação de {} ou preencha PROXY_FIGMA no tagauditor/config.py "
            "com um proxy liberado. {}".format(codigo, _host_api(), _DICA_UPLOAD),
            502, codigo, err)
    if codigo in (401, 403):
        return ErroFigma(_msg_token(err, ctx), 400, codigo, err)
    if codigo == 404:
        return ErroFigma(
            "Arquivo não encontrado no Figma (HTTP 404). Confira se o link está "
            "completo e se a conta dona d{} consegue abrir o arquivo.{}".format(
                _quem(ctx), _dica_tipo(ctx)), 400, codigo, err)
    if codigo in (400, 413):
        detalhe = err or "HTTP {}".format(codigo)
        msg = "O Figma recusou o pedido: {}.".format(detalhe.rstrip(". "))
        if codigo == 413 or "too large" in detalhe.lower():
            msg += " O conteúdo é grande demais para uma leitura só. " + _DICA_FRAME
        else:
            msg += _dica_tipo(ctx)
        return ErroFigma(msg, 400, codigo, err)
    if codigo == 429:
        return ErroFigma(_msg_limite(cabecalhos, ctx), 429, codigo, err)
    if codigo >= 500:
        if ctx.get("etapa") == "imagens":
            msg = ("O Figma não conseguiu gerar a imagem (erro interno, HTTP {}). Frames "
                   "muito grandes ou complexos podem falhar: tente um frame menor. "
                   "{}".format(codigo, _DICA_UPLOAD))
        else:
            msg = ("O Figma está instável no momento (HTTP {}). Tente de novo em "
                   "instantes.".format(codigo))
        return ErroFigma(msg, 502, codigo, err)
    return ErroFigma("O Figma respondeu HTTP {}{}. Tente de novo em instantes.".format(
        codigo, ": " + err if err else ""), 502, codigo, err)


def _erro_rede(exc, token=None):
    """Falha de conexão/proxy/timeout -> ErroFigma(502) com a dica do PROXY_FIGMA."""
    razao = getattr(exc, "reason", None) or exc
    if _eh_erro_certificado(exc):
        return ErroFigma(
            "Não consegui confirmar o certificado de segurança (TLS) da API do Figma. Em "
            "rede com inspeção de TLS, a autoridade certificadora da empresa precisa estar "
            "instalada no Windows do servidor (peça à TI). Só se o time de segurança aprovar, "
            "dá para desligar a verificação com FIGMA_VERIFICAR_TLS = False no "
            "tagauditor/config.py. {}".format(_DICA_UPLOAD), 502)
    # Mascara ANTES de cortar: um token cortado no meio escaparia da troca.
    detalhe = _limpar(_sem_token(razao, token), 150) or type(exc).__name__
    if (isinstance(exc, socket.timeout) or isinstance(razao, socket.timeout)
            or "timed out" in detalhe.lower()):
        return ErroFigma(
            "O Figma demorou mais de {}s para responder. Tente de novo; se o frame for "
            "muito grande, cole o link de um frame menor.".format(FIGMA_TIMEOUT), 502)
    if "407" in detalhe:
        return ErroFigma(
            "O proxy da rede pediu autenticação (HTTP 407) para acessar o Figma. Peça "
            "ao administrador para preencher PROXY_FIGMA no tagauditor/config.py com "
            "um proxy liberado para {}.".format(_host_api()), 502)
    fixo = _proxy_configurado()
    if fixo:
        dica = "Confira o PROXY_FIGMA configurado ({}) no tagauditor/config.py.".format(
            _mascarar(fixo))
    else:
        dica = ("Veja a linha [figma] na janela do servidor; se a rede exigir proxy, "
                "preencha PROXY_FIGMA no tagauditor/config.py (ex.: "
                "http://proxy.bradesco.com.br:8080).")
    return ErroFigma("Não consegui conectar à API do Figma ({}). {}".format(detalhe, dica), 502)


def _api_get(caminho, token, params, ctx):
    """GET na API REST do Figma -> dict. Erros viram ErroFigma amigável.
    ctx: {"proprio": token pessoal?, "tipo": tipo do link, "etapa": arquivo|nos|imagens}."""
    url = FIGMA_API.rstrip("/") + caminho
    if params:
        url += "?" + urllib.parse.urlencode(params, safe=":,;")
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    # As respostas de camadas chegam a vários MB de JSON: gzip corta ~90% do
    # tráfego (o urllib não descomprime sozinho — ver _ler_corpo).
    req.add_header("Accept-Encoding", "gzip")
    req.add_header("User-Agent", _USER_AGENT)
    if token:
        # "unredirected": num redirecionamento (30x) o urllib NÃO repassa o
        # token para o novo host.
        req.add_unredirected_header("X-Figma-Token", token)
    try:
        resp = _abrir(req, FIGMA_TIMEOUT)
    except ErroFigma:
        raise
    except urllib.error.HTTPError as e:
        try:
            corpo = _ler_corpo(e, _MAX_BYTES_ERRO)
        except Exception:
            corpo = b""
        finally:
            try:
                e.close()
            except Exception:
                pass
        raise _erro_da_resposta(e.code, corpo, e.headers or {}, ctx)
    except Exception as e:
        _esquecer_rota()
        raise _erro_rede(e, token)
    try:
        with resp:
            corpo = _ler_corpo(resp, _MAX_BYTES_JSON)
    except _GrandeDemais:
        raise ErroFigma(
            "O conteúdo pedido ao Figma passou de {} MB (grande demais para uma leitura "
            "só). {}".format(_MAX_BYTES_JSON // (1024 * 1024), _DICA_FRAME),
            400, 413, "too large")
    except zlib.error:
        raise ErroFigma(_MSG_RESPOSTA_ESTRANHA, 502)
    except Exception as e:
        _esquecer_rota()
        raise _erro_rede(e, token)
    try:
        dados = json.loads(corpo.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ErroFigma(_MSG_RESPOSTA_ESTRANHA, 502)
    if not isinstance(dados, dict):
        raise ErroFigma(_MSG_RESPOSTA_ESTRANHA, 502)
    status = dados.get("status")
    if isinstance(status, int) and not isinstance(status, bool) and status >= 400 and dados.get("err"):
        raise _erro_da_resposta(status, corpo, {}, ctx)
    return dados


def _grande_demais(erro):
    """O Figma (ou o nosso teto) recusou por tamanho ("Request too large")."""
    return isinstance(erro, ErroFigma) and (
        erro.http == 413 or "too large" in str(erro.detalhe or "").lower())


# ---- Download do PNG renderizado ---------------------------------------------

def _url_imagem_permitida(url):
    try:
        partes = urllib.parse.urlsplit(str(url or ""))
        host = (partes.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if partes.scheme.lower() != "https" or not host:
        return False
    return any(host.endswith(sufixo) for sufixo in _HOSTS_IMAGEM)


def _dimensoes_png(dados):
    """(largura, altura) lidas do cabeçalho IHDR, ou None se não for PNG."""
    if len(dados) < 24 or dados[:8] != _ASSINATURA_PNG or dados[12:16] != b"IHDR":
        return None
    largura, altura = struct.unpack(">II", dados[16:24])
    if not largura or not altura:
        return None
    return largura, altura


def _baixar_png(url):
    """Baixa o PNG que a API renderizou. Link pré-assinado do S3: vai SEM o
    X-Figma-Token (o token nunca sai para outro host). Devolve (bytes, w, h)."""
    if not _url_imagem_permitida(url):
        raise ErroFigma("O Figma devolveu um endereço de imagem inesperado; por "
                        "segurança, a imagem não foi baixada.", 502)
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", _USER_AGENT)
    try:
        # Prazo menor que o da API: proxy que barra o S3 costuma pendurar a
        # conexão, e a tela pode seguir "só com camadas" logo.
        resp = _abrir(req, min(FIGMA_TIMEOUT, 20), seguro=True)
        with resp:
            dados = _ler_corpo(resp, _MAX_BYTES_PNG)
    except ErroFigma:
        raise
    except _GrandeDemais:
        raise ErroFigma("A imagem gerada pelo Figma passou de {} MB. Escolha um frame "
                        "menor.".format(_MAX_BYTES_PNG // (1024 * 1024)), 400)
    except urllib.error.HTTPError as e:
        try:
            e.close()
        except Exception:
            pass
        raise ErroFigma("Não consegui baixar a imagem gerada pelo Figma (HTTP {}). Tente "
                        "de novo.".format(e.code), 502)
    except zlib.error:
        raise ErroFigma(_MSG_RESPOSTA_ESTRANHA, 502)
    except Exception as e:
        # Sem _esquecer_rota(): o S3 barrado não diz nada sobre a rota da API
        # (que acabou de funcionar); esquecer faria cada frame refazer a
        # descoberta de proxy (segundos cada).
        raise _erro_rede(e)
    dimensoes = _dimensoes_png(dados)
    if not dimensoes:
        raise ErroFigma("A imagem baixada do Figma não é um PNG válido. Tente de novo.", 502)
    return dados, dimensoes[0], dimensoes[1]


# ---- Escala da exportação ----------------------------------------------------

def _calcular_escala(dimensoes):
    """Uma escala só para todos os frames (UMA chamada de imagens — a cota é
    escassa): por frame, FIGMA_LARGURA_ALVO/largura limitada a
    [0,5 ; FIGMA_ESCALA_MAX] e a no máximo 30 MP; vale a menor. Arredonda
    PARA BAIXO em 2 casas, para nunca passar dos tetos."""
    escala = None
    for largura, altura in dimensoes:
        try:
            largura, altura = float(largura), float(altura)
        except (TypeError, ValueError):
            continue
        if not (largura > 0 and altura > 0) or math.isinf(largura) or math.isinf(altura):
            continue
        s = FIGMA_LARGURA_ALVO / largura
        s = min(max(s, _ESCALA_MIN), float(FIGMA_ESCALA_MAX))
        s = min(s, math.sqrt(_MAX_PIXELS_IMAGEM / (largura * altura)))
        escala = s if escala is None else min(escala, s)
    if escala is None:
        escala = min(1.0, float(FIGMA_ESCALA_MAX))
    escala = math.floor(escala * 100 + 1e-9) / 100.0
    return min(max(escala, 0.01), _ESCALA_TETO_API)


def _texto_escala(escala):
    return ("%.2f" % escala).rstrip("0").rstrip(".")


def _numero_escala(escala):
    return int(escala) if float(escala).is_integer() else escala


# ---- Inventário de camadas ---------------------------------------------------
# Palavras (pt/en) que, no nome da camada ou do componente, indicam elemento
# em que a pessoa toca. Comparadas sem acento, em minúsculas, como palavra
# inteira (camelCase separado), aceitando plural: "Background" não casa com
# "back", "Selected" não casa com "select", "Table" não casa com "tab".
_PALAVRAS_INTERATIVAS = (
    "button", "btn", "botao", "botoes", "cta", "fab", "link", "tab", "aba", "chip",
    "toggle", "switch", "check ?box", "radio", "input", "campo", "field", "textfield",
    "select", "selector", "seletor", "drop ?down", "menu", "nav", "navbar",
    "navigation", "navegacao", "card", "item", "itens", "banner", "carousel",
    "carrossel", "carrosseis", "accordion", "acordeao", "close", "fechar", "voltar",
    "back", "search", "busca", "buscar", "pesquisa", "pesquisar", "stepper", "slider",
)
_RE_NOME_INTERATIVO = re.compile(
    r"(?<![a-z])(?:" + "|".join(_PALAVRAS_INTERATIVAS) + r")(?:e?s)?(?![a-z])")
# Ícone solto (fechar, voltar, menu) costuma ser tocável; ícone DENTRO de um
# botão/card é só decoração do elemento de fora — por isso fica à parte.
_RE_NOME_ICONE = re.compile(r"(?<![a-z])(?:icon|icone)(?:e?s)?(?![a-z])")


def _normalizar_nome(texto):
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", t)      # "BTNPrimary" -> "BTN Primary"
    t = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", t)          # "iconButton" -> "icon Button"
    return t.lower()


def _caixa(no, campo="absoluteBoundingBox"):
    """(x, y, largura, altura) absolutos do nó, ou None."""
    c = no.get(campo) if isinstance(no, dict) else None
    if not isinstance(c, dict):
        return None
    try:
        valores = (float(c["x"]), float(c["y"]), float(c["width"]), float(c["height"]))
    except (KeyError, TypeError, ValueError):
        return None
    if any(math.isnan(v) or math.isinf(v) for v in valores) or valores[2] < 0 or valores[3] < 0:
        return None
    return valores


def _caixa_referencia(raiz, largura_png, altura_png):
    """Área que o PNG cobre. Com use_absolute_bounds=true é a caixa do frame;
    se a proporção do PNG bater claramente melhor com a área de renderização
    (sombra/conteúdo vazando), as coordenadas passam a valer nessa área."""
    caixa = _caixa(raiz)
    render = _caixa(raiz, "absoluteRenderBounds")
    if caixa is None or caixa[2] <= 0 or caixa[3] <= 0:
        return render if render and render[2] > 0 and render[3] > 0 else None
    if render is None or render[2] <= 0 or render[3] <= 0 or not largura_png or not altura_png:
        return caixa
    proporcao = float(largura_png) / float(altura_png)

    def desvio(c):
        return abs(c[2] / c[3] - proporcao)
    return render if desvio(render) + 0.01 < desvio(caixa) else caixa


def _tem_interacao(no):
    """Interação de protótipo (clique, hover, arrastar...), transição legada
    ou hyperlink num texto."""
    for inter in no.get("interactions") or []:
        if not isinstance(inter, dict):
            continue
        gatilho = inter.get("trigger")
        tipo = gatilho.get("type") if isinstance(gatilho, dict) else None
        if tipo and tipo not in _GATILHOS_AUTOMATICOS:
            return True
    if no.get("transitionNodeID"):
        return True
    if no.get("type") == "TEXT":
        estilo = no.get("style")
        if isinstance(estilo, dict) and estilo.get("hyperlink"):
            return True
        tabela = no.get("styleOverrideTable")
        if isinstance(tabela, dict):
            for est in tabela.values():
                if isinstance(est, dict) and est.get("hyperlink"):
                    return True
    return False


def _opacidade_zero(no):
    op = no.get("opacity")
    return isinstance(op, (int, float)) and not isinstance(op, bool) and op <= 0.001


def _nomes_do_no(no, componentes, conjuntos):
    """Nome da camada e, numa instância, o do componente e o do conjunto de
    variantes (a camada pode ter sido renomeada; o componente diz o que é)."""
    nomes = [no.get("name") or ""]
    if no.get("type") == "INSTANCE":
        comp = componentes.get(no.get("componentId") or "") or {}
        if isinstance(comp, dict):
            nomes.append(comp.get("name") or "")
            conj = conjuntos.get(comp.get("componentSetId") or "") or {}
            if isinstance(conj, dict):
                nomes.append(conj.get("name") or "")
    return [str(n) for n in nomes if n]


def _casa_nome(no, componentes, conjuntos, dentro):
    if no.get("type") not in _TIPOS_POR_NOME:
        return False
    nomes = " | ".join(_normalizar_nome(n) for n in _nomes_do_no(no, componentes, conjuntos))
    if _RE_NOME_INTERATIVO.search(nomes):
        return True
    return (not dentro) and bool(_RE_NOME_ICONE.search(nomes))


def _nome_item(no, componentes, conjuntos):
    """Nome da camada; numa instância renomeada, acrescenta o componente
    ("Contratar · Button/Primary") para a IA saber o que é."""
    nome = _limpar(no.get("name"), _MAX_NOME)
    if no.get("type") == "INSTANCE":
        comp = componentes.get(no.get("componentId") or "") or {}
        conj = (conjuntos.get(comp.get("componentSetId") or "") or {}) if isinstance(comp, dict) else {}
        base = ""
        if isinstance(conj, dict) and conj.get("name"):
            base = conj["name"]
        elif isinstance(comp, dict) and comp.get("name"):
            base = comp["name"]
        base = _limpar(base, _MAX_NOME)
        if base and base.lower() not in nome.lower():
            nome = _limpar("{} · {}".format(nome, base) if nome else base, _MAX_NOME)
    return nome


def _textos_internos(no, limite=_TEXTOS_ROTULO):
    """Até `limite` textos distintos das camadas TEXT visíveis dentro do nó, na
    ordem das camadas. Devolve (textos, ids das camadas usadas)."""
    textos, ids = [], []
    pilha = list(reversed(no.get("children") or []))
    while pilha and len(textos) < limite:
        atual = pilha.pop()
        if not isinstance(atual, dict) or atual.get("visible") is False or _opacidade_zero(atual):
            continue
        if atual.get("type") == "TEXT":
            texto = _limpar(atual.get("characters"), _MAX_TEXTO)
            if texto:
                ids.append(atual.get("id"))
                if texto not in textos:
                    textos.append(texto)
            continue
        pilha.extend(reversed(atual.get("children") or []))
    return textos, ids


def _em_ordem_de_leitura(brutos):
    """De cima para baixo e, na mesma linha (topos a até _TOL_LINHA px), da
    esquerda para a direita."""
    ordenados = sorted(brutos, key=lambda b: (b["y0"], b["x0"], b["ordem"]))
    linhas, linha, topo = [], [], 0.0
    for b in ordenados:
        if linha and b["y0"] - topo > _TOL_LINHA:
            linhas.append(linha)
            linha = []
        if not linha:
            topo = b["y0"]
        linha.append(b)
    if linha:
        linhas.append(linha)
    saida = []
    for linha in linhas:
        saida.extend(sorted(linha, key=lambda b: (b["x0"], b["y0"], b["ordem"])))
    return saida


def _norm(valor, total):
    return round(min(max(valor / total, 0.0), 1.0), 4)


def _inventario(raiz, ref, componentes=None, conjuntos=None, limite=None):
    """Camadas do frame para a IA: elementos interativos (com rótulo) e textos.

    ref = (x, y, largura, altura) absolutos da área que a imagem cobre.
    Devolve (itens, truncado). Coordenadas normalizadas 0..1 em relação a ref,
    recortadas ao frame e aos contêineres que cortam o conteúdo (carrossel,
    rolagem); camadas ocultas (e tudo dentro delas) ficam de fora."""
    componentes = componentes if isinstance(componentes, dict) else {}
    conjuntos = conjuntos if isinstance(conjuntos, dict) else {}
    limite = FIGMA_MAX_ELEMENTOS if limite is None else max(0, int(limite))
    if not isinstance(raiz, dict) or not ref or ref[2] <= 0 or ref[3] <= 0:
        return [], False
    rx, ry, rw, rh = ref
    brutos = []
    usados = set()   # textos já mostrados no rótulo de um elemento interativo

    def registrar(no, interativo, texto, prioridade, corte):
        caixa = _caixa(no)
        if caixa is None:
            return
        x0, y0 = max(caixa[0], corte[0]), max(caixa[1], corte[1])
        x1, y1 = min(caixa[0] + caixa[2], corte[2]), min(caixa[1] + caixa[3], corte[3])
        if x1 - x0 < _MIN_LADO_ITEM or y1 - y0 < _MIN_LADO_ITEM:
            return   # fio/divisória, ou fora da área visível
        brutos.append({"no": no, "interativo": interativo, "texto": texto,
                       "prioridade": prioridade, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                       "ordem": len(brutos)})

    def visitar(no, dentro, corte):
        if not isinstance(no, dict) or no.get("visible") is False:
            return
        tipo = no.get("type")
        forte = _tem_interacao(no)
        transparente = _opacidade_zero(no)
        if transparente and not forte:
            return
        candidato = forte or _casa_nome(no, componentes, conjuntos, dentro)
        if candidato:
            if tipo == "TEXT":
                rotulo = _limpar(no.get("characters"), _MAX_TEXTO)
                usados.add(no.get("id"))
            elif transparente:
                rotulo = ""   # área de clique invisível: nada dentro aparece
            else:
                textos, ids = _textos_internos(no)
                usados.update(ids)
                rotulo = _limpar(" / ".join(textos), _MAX_TEXTO)
            # Prioridade no corte: interação de protótipo > nome com texto >
            # nome sem texto > texto comum.
            registrar(no, True, rotulo, 0 if forte else (1 if rotulo else 2), corte)
        elif tipo == "TEXT" and no.get("id") not in usados:
            texto = _limpar(no.get("characters"), _MAX_TEXTO)
            if texto:
                registrar(no, False, texto, 3, corte)
        if transparente:
            return
        filhos = no.get("children")
        if not filhos:
            return
        novo = corte
        if no.get("clipsContent") is True and tipo in _TIPOS_QUE_CORTAM:
            caixa = _caixa(no)
            if caixa is not None:
                novo = (max(corte[0], caixa[0]), max(corte[1], caixa[1]),
                        min(corte[2], caixa[0] + caixa[2]), min(corte[3], caixa[1] + caixa[3]))
                if novo[2] <= novo[0] or novo[3] <= novo[1]:
                    return
        for filho in filhos:
            visitar(filho, dentro or candidato, novo)

    corte_inicial = (rx, ry, rx + rw, ry + rh)
    for filho in raiz.get("children") or []:
        visitar(filho, False, corte_inicial)

    truncado = len(brutos) > limite
    if truncado:
        brutos.sort(key=lambda b: (b["prioridade"], b["y0"], b["x0"], b["ordem"]))
        brutos = brutos[:limite]
    itens = []
    for n, b in enumerate(_em_ordem_de_leitura(brutos), 1):
        no = b["no"]
        itens.append({
            "ref": "F{}".format(n),
            "id": str(no.get("id") or ""),
            "tipo": str(no.get("type") or ""),
            "nome": _nome_item(no, componentes, conjuntos),
            "texto": b["texto"],
            "interativo": b["interativo"],
            "bbox": {
                "x": _norm(b["x0"] - rx, rw),
                "y": _norm(b["y0"] - ry, rh),
                "largura": _norm(b["x1"] - b["x0"], rw),
                "altura": _norm(b["y1"] - b["y0"], rh),
            },
        })
    return itens, truncado


# ---- Lista de frames para escolher -------------------------------------------

def _juntar(*partes):
    texto = " / ".join(p for p in partes if p)
    return texto or None


def _opcao(no, pagina):
    caixa = _caixa(no) or (0.0, 0.0, 0.0, 0.0)
    id_no = str(no.get("id") or "")
    return {"id": id_no, "nome": _limpar(no.get("name"), 120) or id_no,
            "pagina": pagina or None, "tipo": str(no.get("type") or ""),
            "largura": int(round(caixa[2])), "altura": int(round(caixa[3]))}


def _nos_em_ordem_de_leitura(nos):
    """Frames na ordem em que aparecem no canvas (linhas de cima para baixo,
    cada linha da esquerda para a direita) — é como o time desenha o fluxo."""
    itens = [((_caixa(no) or (0.0, 0.0, 0.0, 0.0)), i, no) for i, no in enumerate(nos)]
    itens.sort(key=lambda t: (t[0][1], t[0][0], t[1]))
    linhas, linha, topo, tolerancia = [], [], 0.0, 0.0
    for item in itens:
        if linha and item[0][1] - topo > tolerancia:
            linhas.append(linha)
            linha = []
        if not linha:
            topo, tolerancia = item[0][1], max(_TOL_LINHA, item[0][3] * 0.5)
        linha.append(item)
    if linha:
        linhas.append(linha)
    return [t[2] for l in linhas for t in sorted(l, key=lambda t: (t[0][0], t[1]))]


def _tem_telas(no):
    return any(isinstance(f, dict) and f.get("visible") is not False
               and f.get("type") in _TIPOS_OPCAO for f in no.get("children") or [])


def _coletar_opcoes(conteiner, pagina, saida):
    """Filhos visíveis de uma página/seção que podem ser uma tela. Seção que
    veio aberta na resposta (com filhos) é trocada pelas telas de dentro."""
    filhos = [f for f in conteiner.get("children") or []
              if isinstance(f, dict) and f.get("visible") is not False
              and f.get("type") in _TIPOS_OPCAO]
    for filho in _nos_em_ordem_de_leitura(filhos):
        if filho.get("type") == "SECTION" and _tem_telas(filho):
            _coletar_opcoes(filho, _juntar(pagina, _limpar(filho.get("name"), 60)), saida)
        else:
            saida.append(_opcao(filho, pagina))


def _resposta_opcoes(chave, nome_arquivo, opcoes, tk, msg_vazio):
    unicas, vistos = [], set()
    for opcao in opcoes:
        if opcao["id"] and opcao["id"] not in vistos:
            vistos.add(opcao["id"])
            unicas.append(opcao)
    if not unicas:
        raise ErroFigma(msg_vazio, 400)
    _memo_guardar(tk, chave, unicas)
    avisos = []
    if len(unicas) > FIGMA_MAX_OPCOES:
        avisos.append("Este link tem {} frames; a lista mostra os {} primeiros. Para "
                      "buscar outro, cole o link do próprio frame (selecione o frame no "
                      "Figma e copie o link da seleção).".format(len(unicas), FIGMA_MAX_OPCOES))
        unicas = unicas[:FIGMA_MAX_OPCOES]
    if any(o["tipo"] == "SECTION" for o in unicas):
        avisos.append("Itens do tipo SECTION são seções que agrupam várias telas: para "
                      "escolher uma tela de dentro de uma seção, cole o link da seção.")
    return {"arquivo": {"key": chave, "nome": nome_arquivo or ""},
            "opcoes": unicas, "aviso": " ".join(avisos) or None}


# ---- Memória de páginas (nome da página de cada frame já listado) -----------
# A rota de nós não diz em que página o frame está; quando ele veio de uma
# lista, já sabemos — assim o frame exportado sai com "pagina" preenchida.
_trava_memo = threading.Lock()
_memo_paginas = OrderedDict()


def _memo_guardar(tk, chave, opcoes):
    with _trava_memo:
        mapa = _memo_paginas.pop((tk, chave), None) or {}
        for opcao in opcoes:
            if opcao.get("pagina"):
                mapa[opcao["id"]] = opcao["pagina"]
        if len(mapa) > _MEMO_MAX_IDS:
            mapa = dict(list(mapa.items())[-_MEMO_MAX_IDS:])
        _memo_paginas[(tk, chave)] = mapa
        while len(_memo_paginas) > _MEMO_MAX_ARQUIVOS:
            _memo_paginas.popitem(last=False)


def _memo_obter(tk, chave):
    with _trava_memo:
        return dict(_memo_paginas.get((tk, chave)) or {})


# ---- Cache de resultados ------------------------------------------------------
# Chave: (hash do token, arquivo, modo, alvos) — o hash separa o que cada token
# pode ver; o token em si nunca fica guardado. Limitado em itens e em bytes
# (cada resultado carrega os PNGs em base64).
_trava_cache = threading.Lock()
_cache = OrderedDict()


def _ttl_cache():
    try:
        return max(0.0, float(FIGMA_CACHE_MIN or 0)) * 60.0
    except (TypeError, ValueError):
        return 0.0


def _tamanho_resposta(resposta):
    return 4096 + sum(len(f.get("base64") or "") for f in resposta.get("frames") or [])


def _cache_obter(chave_cache):
    if _ttl_cache() <= 0:
        return None
    agora = time.time()
    with _trava_cache:
        item = _cache.get(chave_cache)
        if item is None:
            return None
        if item[0] < agora:
            del _cache[chave_cache]
            return None
        _cache.move_to_end(chave_cache)
        return copy.deepcopy(item[2])


def _cache_guardar(chave_cache, resposta):
    ttl = _ttl_cache()
    if ttl <= 0:
        return
    tamanho = _tamanho_resposta(resposta)
    if tamanho > _CACHE_MAX_BYTES:
        return
    agora = time.time()
    with _trava_cache:
        for k in [k for k, v in _cache.items() if v[0] < agora]:
            del _cache[k]
        _cache[chave_cache] = (agora + ttl, tamanho, copy.deepcopy(resposta))
        _cache.move_to_end(chave_cache)
        while _cache and (len(_cache) > _CACHE_MAX_ITENS
                          or sum(v[1] for v in _cache.values()) > _CACHE_MAX_BYTES):
            _cache.popitem(last=False)


def _limpar_cache():
    with _trava_cache:
        _cache.clear()
    with _trava_memo:
        _memo_paginas.clear()


def _na_ordem(resposta, alvos):
    """O cache é por conjunto de frames; a resposta segue a ordem pedida."""
    posicao = {a: i for i, a in enumerate(alvos)}
    resposta["frames"] = sorted(resposta.get("frames") or [],
                                key=lambda f: posicao.get(f.get("id"), len(posicao)))
    return resposta


# ---- Busca --------------------------------------------------------------------

def _ler_nos(chave, alvos, token, ctx, profundidade):
    params = {"ids": ",".join(alvos)}
    if profundidade:
        params["depth"] = str(profundidade)
    dados = _api_get("/v1/files/{}/nodes".format(chave), token, params, ctx)
    if not isinstance(dados.get("nodes"), dict):
        err = _limpar(dados.get("err"), 200)
        if err:
            raise ErroFigma("O Figma recusou o pedido: {}.".format(err.rstrip(". ")), 400, 400, err)
        raise ErroFigma(_MSG_RESPOSTA_ESTRANHA, 502)
    return dados


def _documentos(dados, alvos, nome_arquivo, chave):
    """Nó (árvore) de cada alvo, na ordem pedida; id sem nó -> erro nomeando o id."""
    nos = dados.get("nodes") or {}
    docs, faltando = [], []
    for alvo in alvos:
        entrada = nos.get(alvo)
        doc = entrada.get("document") if isinstance(entrada, dict) else None
        if not isinstance(doc, dict):
            faltando.append(alvo)
            continue
        docs.append({"id": alvo, "doc": doc,
                     "componentes": entrada.get("components") or {},
                     "conjuntos": entrada.get("componentSets") or {}})
    if faltando:
        arquivo = nome_arquivo or chave
        if len(faltando) == 1:
            raise ErroFigma(
                'Não encontrei o item {} no arquivo "{}": ele pode ter sido apagado ou '
                'movido. Copie de novo o link do frame no Figma (ou busque a lista de '
                'frames outra vez).'.format(faltando[0], arquivo), 400)
        raise ErroFigma(
            'Não encontrei os itens {} no arquivo "{}": eles podem ter sido apagados ou '
            'movidos. Copie de novo o link do frame no Figma (ou busque a lista de '
            'frames outra vez).'.format(", ".join(faltando), arquivo), 400)
    return docs


def _buscar_lista(chave, token, ctx, tk):
    """Link sem node-id: frames da raiz de todas as páginas (1 chamada)."""
    chave_cache = (tk, chave, "opcoes")
    em_cache = _cache_obter(chave_cache)
    if em_cache is not None:
        return em_cache
    try:
        # depth=2 = páginas + objetos de topo: o bastante para a lista, sem
        # baixar as camadas de todas as telas do arquivo.
        dados = _api_get("/v1/files/{}".format(chave), token, {"depth": "2"},
                         dict(ctx, etapa="arquivo"))
    except ErroFigma as e:
        if _grande_demais(e):
            raise ErroFigma(
                "O arquivo é grande demais para listar todos os frames de uma vez. Abra "
                "no Figma a página que tem a tela, copie o link dela (ou do próprio "
                "frame) e cole aqui.", 400, e.http, e.detalhe)
        raise
    documento = dados.get("document")
    if not isinstance(documento, dict):
        raise ErroFigma(_MSG_RESPOSTA_ESTRANHA, 502)
    opcoes = []
    for pagina in documento.get("children") or []:
        if isinstance(pagina, dict) and pagina.get("type") == "CANVAS":
            _coletar_opcoes(pagina, _limpar(pagina.get("name"), 60) or None, opcoes)
    resposta = _resposta_opcoes(
        chave, _limpar(dados.get("name"), 120), opcoes, tk,
        "Não encontrei frames visíveis neste arquivo do Figma (as páginas estão vazias "
        "ou só têm camadas soltas/ocultas). " + _DICA_FRAME)
    _cache_guardar(chave_cache, resposta)
    return resposta


def _opcoes_dos_alvos(chave, nome_arquivo, docs, tk):
    """Link de página/seção: lista as telas de dentro em vez de exportar a
    página inteira (frames que vieram junto no link também entram na lista)."""
    paginas = _memo_obter(tk, chave)
    opcoes, onde = [], []
    for d in docs:
        doc, tipo = d["doc"], d["doc"].get("type")
        nome = _limpar(doc.get("name"), 60)
        if tipo == "DOCUMENT":
            onde.append("este arquivo")
            for pagina in doc.get("children") or []:
                if isinstance(pagina, dict) and pagina.get("type") == "CANVAS":
                    _coletar_opcoes(pagina, _limpar(pagina.get("name"), 60) or None, opcoes)
        elif tipo == "CANVAS":
            onde.append('a página "{}"'.format(nome))
            _coletar_opcoes(doc, nome or None, opcoes)
        elif tipo == "SECTION":
            onde.append('a seção "{}"'.format(nome))
            _coletar_opcoes(doc, _juntar(paginas.get(d["id"]), nome), opcoes)
        else:
            opcoes.append(_opcao(doc, paginas.get(d["id"])))
    return _resposta_opcoes(
        chave, nome_arquivo, opcoes, tk,
        "Não encontrei frames visíveis em {}. Cole o link de outra página ou de um "
        "frame específico.".format(" e ".join(onde) or "este link"))


def _renderizar(chave, nome_arquivo, docs, token, ctx, tk):
    """UMA chamada de imagens para todos os frames, download dos PNGs e o
    inventário de camadas de cada um."""
    escala = _calcular_escala([(_caixa(d["doc"]) or (0.0, 0.0, 0.0, 0.0))[2:] for d in docs])
    ids = [d["id"] for d in docs]
    dados = _api_get("/v1/images/{}".format(chave), token, {
        "ids": ",".join(ids), "format": "png", "scale": _texto_escala(escala),
        # A imagem cobre exatamente a caixa do frame: as coordenadas das
        # camadas (relativas a essa caixa) caem no lugar certo do PNG.
        "use_absolute_bounds": "true",
    }, dict(ctx, etapa="imagens"))
    imagens = dados.get("images")
    if not isinstance(imagens, dict):
        err = _limpar(dados.get("err"), 200)
        if err:
            raise ErroFigma("O Figma não gerou as imagens: {}.".format(err.rstrip(". ")), 400, 400, err)
        raise ErroFigma(_MSG_RESPOSTA_ESTRANHA, 502)
    sem_imagem = [d for d in docs if not isinstance(imagens.get(d["id"]), str) or not imagens.get(d["id"])]
    if sem_imagem:
        nomes = ", ".join('"{}" ({})'.format(_limpar(d["doc"].get("name"), 60) or d["id"], d["id"])
                          for d in sem_imagem)
        raise ErroFigma(
            "O Figma não conseguiu gerar a imagem de {}: o frame pode estar oculto, "
            "vazio ou com opacidade zero. Confira no Figma ou escolha outro "
            "frame.".format(nomes), 400)

    # Downloads em paralelo (vários frames = vários PNGs no S3, fora da cota).
    with ThreadPoolExecutor(max_workers=min(4, len(docs))) as executor:
        baixados = list(executor.map(lambda d: _baixar_png_ou_falha(imagens[d["id"]]), docs))
    total = sum(len(b[0]) for b in baixados if b[0] is not None)
    if total > _MAX_BYTES_PNGS:
        raise ErroFigma(
            "As imagens dos frames somaram {} MB, demais para uma busca só. Busque "
            "menos frames por vez.".format(total // (1024 * 1024)), 400)

    paginas = _memo_obter(tk, chave)
    frames, sem_png = [], []
    for d, (png, largura, altura, falha) in zip(docs, baixados):
        if png is None:
            # A API respondeu, mas o PNG (outro host, no S3) não veio - típico de
            # proxy corporativo que libera api.figma.com e barra o S3. As camadas
            # já chegaram: a tela segue "só com camadas" em vez de perder a busca.
            caixa = _caixa(d["doc"]) or (0.0, 0.0, 0.0, 0.0)
            largura = max(1, int(round(caixa[2] * escala)))
            altura = max(1, int(round(caixa[3] * escala)))
            sem_png.append(_limpar(d["doc"].get("name"), 60) or d["id"])
        ref = _caixa_referencia(d["doc"], largura if png is not None else None,
                                altura if png is not None else None)
        inventario, truncado = _inventario(d["doc"], ref, d["componentes"], d["conjuntos"])
        frames.append({
            "id": d["id"],
            "nome": _limpar(d["doc"].get("name"), 120) or d["id"],
            "pagina": paginas.get(d["id"]),
            "largura": largura,
            "altura": altura,
            "escala": _numero_escala(escala),
            "mime": "image/png" if png is not None else None,
            "base64": base64.b64encode(png).decode("ascii") if png is not None else None,
            "sem_imagem": png is None,
            "imagem_erro": falha,
            "inventario": inventario,
            "inventario_truncado": truncado,
        })
    for frame in frames:
        if frame["sem_imagem"] and not frame["inventario"]:
            # Sem imagem E sem camadas não há o que analisar: melhor o erro real.
            raise ErroFigma(frame["imagem_erro"], 502)
    resposta = {"arquivo": {"key": chave, "nome": nome_arquivo or ""}, "frames": frames}
    if sem_png:
        resposta["aviso"] = (
            "A imagem de {} não pôde ser baixada (a rede/proxy bloqueou o servidor de "
            "imagens do Figma). A análise usa as camadas do arquivo, com textos e posições "
            "exatos, e a tela aparece como prévia desenhada a partir delas. Para usar a "
            "imagem de verdade, copie o frame no Figma (Ctrl+Shift+C) e cole na aba "
            "\"Enviar imagem\".".format(", ".join('"{}"'.format(n) for n in sem_png)))
    return resposta


def _baixar_png_ou_falha(url):
    """(png, largura, altura, None) ou, se a rede/proxy barrar o download,
    (None, None, None, mensagem). Imagem grande demais continua sendo erro:
    aí a culpa é do frame escolhido, não da rede."""
    try:
        png, largura, altura = _baixar_png(url)
        return png, largura, altura, None
    except ErroFigma as e:
        if e.status != 502:
            raise
        print("[figma] Download da imagem falhou ({}); a tela segue só com as "
              "camadas.".format(str(e)[:160]))
        return None, None, None, str(e)


def _buscar_alvos(chave, alvos, origem, token, ctx, tk):
    """Frames (ou a lista de telas, se o link for de página/seção).
    origem: "url" (ids do link) | "ids" (escolhidos na lista)."""
    ordem = tuple(sorted(alvos))
    # Os mesmos ids podem dar respostas diferentes conforme a origem: seção
    # pelo link vira lista; seção escolhida na lista é exportada. Frames comuns
    # dão a mesma resposta nos dois casos (cache compartilhado).
    chave_frames = (tk, chave, "frames", ordem)
    chave_secao = (tk, chave, "frames-secao", ordem)
    chave_lista = (tk, chave, "opcoes-url", ordem)
    em_cache = _cache_obter(chave_frames)
    if em_cache is None:
        em_cache = _cache_obter(chave_lista if origem == "url" else chave_secao)
    if em_cache is not None:
        return _na_ordem(em_cache, alvos) if "frames" in em_cache else em_cache

    ctx_nos = dict(ctx, etapa="nos")
    # Link de página ("0:1"): 2 níveis bastam para listar as telas (inclusive
    # as de dentro das seções) e evitam baixar a página inteira, camada por
    # camada. Se o palpite errar e for um frame, busca a árvore completa depois.
    parcial = origem == "url" and all(_RE_ID_PAGINA.match(a) for a in alvos)
    recuo = False
    try:
        dados = _ler_nos(chave, alvos, token, ctx_nos, 2 if parcial else None)
    except ErroFigma as e:
        if origem != "url" or parcial or not _grande_demais(e):
            raise
        # Página/seção enorme pedida pelo link: lista as telas com 2 níveis.
        dados = _ler_nos(chave, alvos, token, ctx_nos, 2)
        parcial = recuo = True
    nome_arquivo = _limpar(dados.get("name"), 120)
    docs = _documentos(dados, alvos, nome_arquivo, chave)

    if origem == "url" and any(d["doc"].get("type") in _TIPOS_CONTEINER for d in docs):
        resposta = _opcoes_dos_alvos(chave, nome_arquivo, docs, tk)
        _cache_guardar(chave_lista, resposta)
        return resposta
    paginas = [d for d in docs if d["doc"].get("type") in _TIPOS_PAGINA]
    if paginas:
        nome = _limpar(paginas[0]["doc"].get("name"), 60) or paginas[0]["id"]
        raise ErroFigma(
            '"{}" é uma página inteira do Figma, não uma tela. Escolha um ou mais '
            'frames da lista.'.format(nome), 400)
    if recuo:
        raise ErroFigma(
            "O frame é grande demais para o Figma devolver as camadas de uma vez. "
            "Escolha um frame menor (ou exporte a tela como PNG e use a aba \"Enviar "
            "imagem\").", 400, 413, "too large")
    if parcial:
        dados = _ler_nos(chave, alvos, token, ctx_nos, None)
        docs = _documentos(dados, alvos, nome_arquivo, chave)
    resposta = _renderizar(chave, nome_arquivo, docs, token, ctx, tk)
    tem_secao = any(d["doc"].get("type") == "SECTION" for d in docs)
    # Resposta "só camadas" (download do PNG falhou) não entra no cache: a
    # falha pode ser passageira e a próxima busca tem de tentar a imagem de novo.
    if not any(f.get("sem_imagem") for f in resposta.get("frames") or []):
        _cache_guardar(chave_secao if tem_secao else chave_frames, resposta)
    return resposta


def buscar(url, ids=None, token_usuario=None):
    """Busca telas no Figma (formas do SPEC §5.3).

    url           link do Figma (arquivo, página, seção ou frame)
    ids           frames escolhidos na lista (ex.: ["1:2", "1:3"]); vazio = os do link
    token_usuario token pessoal digitado na tela; vazio = FIGMA_TOKEN do servidor

    Devolve {"arquivo", "opcoes", "aviso"} (lista para escolher: link sem
    frame, ou de página/seção) ou {"arquivo", "frames"} (PNG em base64 +
    inventário de camadas). Erros: ErroFigma com .status 400/429/502/503."""
    chave, ids_url, tipo = _analisar(url)
    escolhidos = _validar_ids(ids)
    token, proprio = _escolher_token(token_usuario)
    ctx = {"proprio": proprio, "tipo": tipo}
    tk = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    try:
        if escolhidos:
            return _buscar_alvos(chave, escolhidos, "ids", token, ctx, tk)
        if ids_url:
            return _buscar_alvos(chave, ids_url[:FIGMA_MAX_FRAMES], "url", token, ctx, tk)
        return _buscar_lista(chave, token, ctx, tk)
    except ErroFigma:
        raise
    except Exception:
        print("[figma] Falha inesperada na busca:\n" + _sem_token(traceback.format_exc(), token))
        raise ErroFigma("Falha inesperada ao ler o Figma. Tente de novo; se persistir, "
                        "avise o desenvolvedor.", 502)
