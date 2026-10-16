"""Confluence: baixar/montar o plano .json a partir da URL da página.

Fluxo: 1) se a página tiver anexo .json, baixa o anexo; 2) senão, lê o
conteúdo da página e monta o mesmo JSON {"sections": ...} do download manual,
extraindo os blocos de código (code_json) por seção — porte fiel do
bookmarklet "God Mode". Quando a API REST está bloqueada por rate limit do
admin (429), muda para o "modo navegador" (páginas de interface).
"""
import base64
import html
import html.parser
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import (
    CONFLUENCE_TOKEN, CONFLUENCE_USUARIO, CONFLUENCE_SENHA, CONFLUENCE_TIMEOUT,
)
from .rede import CTX_SSL
from .plano import _RE_TAG, _RE_ESPACO


def _carregar_json(corpo):
    try:
        return json.loads(corpo)
    except Exception:
        return None


def _headers_confluence():
    h = {
        "Accept": "application/json",
        # O rate-limit/WAF do Confluence costuma responder 429 para o
        # User-Agent padrão do Python (tratado como robô); nos identificamos
        # como um navegador comum, igual ao acesso normal dos usuários.
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0.0.0 Safari/537.36"),
        "Accept-Language": "pt-BR,pt;q=0.9",
    }
    if CONFLUENCE_TOKEN:
        h["Authorization"] = "Bearer " + CONFLUENCE_TOKEN
    elif CONFLUENCE_USUARIO:
        par = base64.b64encode("{}:{}".format(
            CONFLUENCE_USUARIO, CONFLUENCE_SENHA).encode("utf-8")).decode("ascii")
        h["Authorization"] = "Basic " + par
    return h


def _get_confluence(url, tentativas=3):
    """GET no Confluence (conexão direta, rede interna).
    Devolve (status, bytes, url_final_após_redirects).
    HTTP 429 (limite de requisições) é retentado com espera automática."""
    req = urllib.request.Request(url)
    for chave, valor in _headers_confluence().items():
        req.add_header(chave, valor)
    for tentativa in range(1, tentativas + 1):
        try:
            with urllib.request.urlopen(req, timeout=CONFLUENCE_TIMEOUT, context=CTX_SSL) as resp:
                return resp.status, resp.read(), resp.geturl()
        except urllib.error.HTTPError as e:
            try:
                corpo = e.read()
            except Exception:
                corpo = b""
            if e.code == 429:
                # diagnóstico: quem barrou? (rate limit do Confluence devolve
                # JSON; WAF/F5 devolve página HTML)
                try:
                    cab = {k: v for k, v in (e.headers or {}).items()
                           if k.lower() in ("retry-after", "server", "via",
                                            "x-ratelimit-limit", "x-ratelimit-remaining",
                                            "x-ratelimit-reset", "x-ratelimit-interval-seconds",
                                            "x-ratelimit-fillrate", "content-type")}
                except Exception:
                    cab = {}
                print("[confluence] HTTP 429 | headers: {} | corpo: {}".format(
                    cab, corpo[:200].decode("utf-8", errors="replace").replace("\n", " ")))
                if tentativa < tentativas:
                    try:
                        espera = int((e.headers.get("Retry-After") or "0").strip())
                    except Exception:
                        espera = 0
                    espera = min(max(espera, 2 * tentativa), 10)
                    print("[confluence] aguardo {}s e tento de novo ({}/{})...".format(
                        espera, tentativa, tentativas))
                    time.sleep(espera)
                    continue
            return e.code, corpo, url
        except Exception as e:
            raise ValueError(
                "Não consegui conectar ao Confluence ({}). O servidor precisa "
                "alcançar essa rede.".format(str(e)[:120]))
    return 429, b"", url


def _erro_confluence(status):
    if status in (401, 403):
        return ("O Confluence recusou o acesso (HTTP {}). Configure o "
                "CONFLUENCE_TOKEN no tagauditor/config.py (crie em Perfil > "
                "Personal Access Tokens) ou confira as permissões da página.".format(status))
    if status == 404:
        return "Página não encontrada no Confluence (HTTP 404). Confira a URL."
    if status == 429:
        return ("O Confluence está limitando as requisições (HTTP 429), mesmo "
                "após novas tentativas. Aguarde ~1 minuto e tente de novo; se "
                "persistir sempre, avise o desenvolvedor.")
    return "O Confluence respondeu HTTP {}.".format(status)


class _RateLimit429(Exception):
    """API REST do Confluence bloqueada por rate limit (cota zero do admin)."""


def _eh_pagina_login(html_texto):
    return 'name="os_username"' in html_texto or 'name="os_password"' in html_texto


_RE_TITLE_CONF = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _confluence_base_pagina(url):
    """Da URL colada, extrai (base_com_contexto, page_id, (espaco, titulo))."""
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError("Isso não parece uma URL do Confluence. Cole a URL completa da página.")
    caminho = u.path or "/"
    base = u.scheme + "://" + u.netloc
    for marcador in ("/pages/", "/display/", "/download/", "/spaces/", "/x/"):
        idx = caminho.find(marcador)
        if idx >= 0:
            base += caminho[:idx]
            break
    qs = urllib.parse.parse_qs(u.query)
    if qs.get("pageId"):
        return base, qs["pageId"][0], None
    m = re.search(r"/pages/(\d+)", caminho)
    if m:
        return base, m.group(1), None
    m = re.search(r"/display/([^/]+)/([^/?#]+)", caminho)
    if m:
        return base, None, (m.group(1), urllib.parse.unquote_plus(m.group(2)))
    return base, None, None


def _confluence_resolver_pagina(url):
    """Devolve (base, page_id) para a URL colada (pageId, /display/ ou link curto)."""
    base, pid, esp_tit = _confluence_base_pagina(url)
    if pid:
        return base, pid
    if esp_tit:
        consulta = "{}/rest/api/content?spaceKey={}&title={}&limit=1".format(
            base, urllib.parse.quote(esp_tit[0]), urllib.parse.quote(esp_tit[1]))
        status, corpo, _ = _get_confluence(consulta)
        if status == 200:
            dados = _carregar_json(corpo.decode("utf-8", errors="replace")) or {}
            resultados = dados.get("results") or []
            if resultados and resultados[0].get("id"):
                return base, str(resultados[0]["id"])
        elif status != 429:
            raise ValueError(_erro_confluence(status))
        # REST bloqueada (429) ou busca sem resultado: abre a própria página
        # como navegador e extrai o id dela (meta ajs-page-id)
    # Link curto (/x/abc) ou formato desconhecido: segue o redirect e reanalisa
    _status, corpo_pag, url_final = _get_confluence(url)
    if url_final and url_final != url:
        base2, pid2, _ = _confluence_base_pagina(url_final)
        if pid2:
            return base2, pid2
    m = re.search(r'name="ajs-page-id"\s+content="(\d+)"',
                  corpo_pag.decode("utf-8", errors="replace"))
    if m:
        return base, m.group(1)
    raise ValueError("Não consegui identificar a página nessa URL. Use a URL com "
                     "pageId (ex.: .../pages/viewpage.action?pageId=123456).")


def _confluence_anexos_json(base, pid):
    """Lista os anexos .json da página, do mais recente para o mais antigo."""
    url = "{}/rest/api/content/{}/child/attachment?limit=200&expand=version".format(base, pid)
    status, corpo, _ = _get_confluence(url)
    if status == 429:
        raise _RateLimit429()
    if status != 200:
        raise ValueError(_erro_confluence(status))
    dados = _carregar_json(corpo.decode("utf-8", errors="replace")) or {}
    anexos = []
    for item in dados.get("results") or []:
        titulo = str(item.get("title") or "")
        download = (item.get("_links") or {}).get("download") or ""
        if titulo.lower().endswith(".json") and download:
            anexos.append({
                "nome": titulo,
                "download": download,
                "quando": (item.get("version") or {}).get("when") or "",
            })
    anexos.sort(key=lambda a: a["quando"], reverse=True)
    return anexos


def _confluence_anexos_via_ui(base, pid):
    """Lista os anexos .json pela página de anexos da interface — caminho usado
    quando a API REST está bloqueada por rate limit (downloads não são REST)."""
    status, corpo, _ = _get_confluence(
        "{}/pages/viewpageattachments.action?pageId={}".format(base, pid))
    if status != 200:
        return []
    texto = corpo.decode("utf-8", errors="replace")
    if _eh_pagina_login(texto):
        return []
    anexos = []
    for m in re.finditer(
            r'href="([^"]*/download/attachments/' + re.escape(str(pid)) +
            r'/[^"]+\.json(?:\?[^"]*)?)"', texto, re.IGNORECASE):
        href = html.unescape(m.group(1))
        caminho = href[href.find("/download/attachments/"):]
        nome_arq = urllib.parse.unquote(caminho.split("?", 1)[0].split("/")[-1])
        if not any(a["nome"] == nome_arq for a in anexos):
            anexos.append({"nome": nome_arq, "download": caminho, "quando": ""})
    return anexos


def _js_para_json(texto):
    """Converte um literal de objeto JS (aspas simples, chaves sem aspas,
    vírgulas sobrando) em objeto Python. Devolve None se não conseguir."""
    try:
        return json.loads(texto)
    except Exception:
        pass
    t = re.sub(r"'([^']*)'", lambda m: json.dumps(m.group(1), ensure_ascii=False), texto)
    t = re.sub(r'([{\[,]\s*)([A-Za-z0-9_$]+)\s*:', r'\1"\2":', t)
    t = re.sub(r",\s*([}\]])", r"\1", t)
    try:
        return json.loads(t)
    except Exception:
        return None


def _conf_parse_code(raw):
    """Porte do parseCode do bookmarklet usado no download manual ("God Mode"):
    entende dataLayer.push({...}) em sintaxe JS, código nativo Android
    (bundle.putString/Analytics.logEvent) e, em último caso, lê linha a linha.
    Devolve sempre um dict/list para virar o campo code_json."""
    if not raw or not raw.strip():
        return {"raw_code_fallback": "Codigo vazio."}
    limpo = raw.replace(chr(160), " ").replace(chr(8203), " ")  # nbsp / zero-width
    for de, para in (("“", '"'), ("”", '"'), ("‘", "'"),
                     ("’", "'"), ("´", "'"), ("`", "'")):
        limpo = limpo.replace(de, para)
    limpo = re.sub(r"^CÓDIGO A SER IMPLEMENTADO[\s0-9]+", "", limpo).strip()

    # Código nativo (Android): extrai o evento e os pares chave/valor
    if "bundle.putString" in limpo or "Analytics.logEvent" in limpo:
        res = {"_is_native": True}
        m = re.search(r"logEvent\s*\(\s*[\"']([^\"']+)[\"']", limpo)
        if m:
            res["event"] = m.group(1)
        for k, v in re.findall(r"[\"']([^\"']+)[\"']\s*[:,]\s*[\"']([^\"']*)[\"']", limpo):
            if k not in ("event_type", "index") and k != res.get("event"):
                res[k] = v
        return res

    sem_com = re.sub(r"/\*[\s\S]*?\*/", "", limpo)
    sem_com = re.sub(r"//.*$", "", sem_com, flags=re.MULTILINE)

    m = re.search(r"dataLayer\.push\s*\(\s*(\{[\s\S]*?\})\s*\)", sem_com)
    alvo = (m.group(1) if m else sem_com).strip()
    obj = _js_para_json(alvo)
    if not isinstance(obj, (dict, list)):
        a, b = alvo.find("{"), alvo.rfind("}")
        alvo2 = alvo[a:b + 1] if (a != -1 and b > a) else alvo
        # referências soltas tipo document.title viram strings, como no original
        alvo2 = re.sub(r"\b((?:document|window|location)\.[A-Za-z0-9_.]+)\b", r'"\1"', alvo2)
        alvo2 = re.sub(r",\s*([}\]])", r"\1", alvo2)
        obj = _js_para_json(alvo2)
    if isinstance(obj, dict):
        obj.setdefault("_is_native", False)
        return obj
    if isinstance(obj, list):
        return obj

    # Último recurso: leitura linha a linha (mesma heurística do bookmarklet)
    res, atual, tem = {"_is_native": False}, None, False
    for linha in sem_com.split("\n"):
        l = linha.strip()
        if not l or l in ("[", "]"):
            continue
        if l in ("{", "},"):
            atual = None
            continue
        m = re.match(r"^['\"]?([A-Za-z0-9_]+)['\"]?\s*:\s*\{", l)
        if m:
            atual = m.group(1)
            res[atual] = {}
            continue
        m = re.match(r"^['\"]?([A-Za-z0-9_]+)['\"]?\s*[:,]\s*(.*)$", l)
        if m:
            k, v = m.group(1), m.group(2).rstrip(",").strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            if atual is not None:
                res[atual][k] = v
            else:
                res[k] = v
            tem = True
    if tem:
        return res
    return {"raw_code_fallback": sem_com.strip(), "parse_warning": "Sintaxe irreconhecivel."}


# ---- Mini-DOM (stdlib) para reproduzir a varredura do bookmarklet -----------

class _NoHTML(object):
    __slots__ = ("tag", "attrs", "pai", "filhos")

    def __init__(self, tag, attrs, pai):
        self.tag = tag
        self.attrs = {k: (v or "") for k, v in (attrs or [])}
        self.pai = pai
        self.filhos = []


class _ArvoreHTML(html.parser.HTMLParser):
    _VAZIAS = {"br", "hr", "img", "input", "meta", "link", "source",
               "col", "area", "base", "wbr"}

    def __init__(self):
        html.parser.HTMLParser.__init__(self, convert_charrefs=True)
        self.raiz = _NoHTML("__raiz__", [], None)
        self._pilha = [self.raiz]

    def handle_starttag(self, tag, attrs):
        no = _NoHTML(tag, attrs, self._pilha[-1])
        self._pilha[-1].filhos.append(no)
        if tag == "br":
            self._pilha[-1].filhos.append("\n")
        if tag not in self._VAZIAS:
            self._pilha.append(no)

    def handle_endtag(self, tag):
        for i in range(len(self._pilha) - 1, 0, -1):
            if self._pilha[i].tag == tag:
                del self._pilha[i:]
                return

    def handle_data(self, dados):
        if dados:
            self._pilha[-1].filhos.append(dados)


def _dom(html_texto):
    p = _ArvoreHTML()
    p.feed(html_texto)
    return p.raiz


def _n_atr(no, nome):
    return no.attrs.get(nome, "")


def _n_classes(no):
    return _n_atr(no, "class").split()


def _n_macro(no):
    return _n_atr(no, "data-macro-name")


def _n_els(no):
    return [f for f in no.filhos if isinstance(f, _NoHTML)]


def _n_todos(no, pred):
    achados = []

    def visita(x):
        for f in _n_els(x):
            if pred(f):
                achados.append(f)
            visita(f)
    visita(no)
    return achados


def _n_um(no, pred):
    for f in _n_els(no):
        if pred(f):
            return f
        achado = _n_um(f, pred)
        if achado is not None:
            return achado
    return None


def _n_texto(no):
    partes = []

    def visita(x):
        for f in x.filhos:
            if isinstance(f, str):
                partes.append(f)
            else:
                visita(f)
    visita(no)
    texto = re.sub(r"[ \t\r\f\v]+", " ", "".join(partes))
    return "\n".join(l.strip() for l in texto.split("\n")).strip()


def _secao_por_irmaos(no, padrao):
    """Título da seção = heading anterior no mesmo nível (como no bookmarklet)."""
    alvo = no
    if no.pai is not None and "table-wrap" in _n_classes(no.pai):
        alvo = no.pai
    if alvo.pai is None:
        return padrao
    irmaos = _n_els(alvo.pai)
    try:
        idx = irmaos.index(alvo)
    except ValueError:
        return padrao
    for irmao in reversed(irmaos[:idx]):
        if irmao.tag in ("h1", "h2", "h3", "h4", "h5"):
            t = _n_texto(irmao)
            if t:
                return t
    return padrao


_INDICIOS_CODIGO = ("dataLayer", "Bundle", "Analytics", "event_name")


def _tem_indicio_codigo(texto):
    return any(t in texto for t in _INDICIOS_CODIGO)


def _status_de(no):
    s = _n_um(no, lambda n: _n_macro(n) == "status")
    if s is None:
        return None
    return {"text": _n_texto(s), "params": _n_atr(s, "data-macro-parameters")}


def _texto_macro_codigo(macro_code):
    interno = _n_um(macro_code, lambda n: n.tag == "pre" or "codeContent" in _n_classes(n))
    return _n_texto(interno if interno is not None else macro_code)


def _codigo_de(no):
    macro = _n_um(no, lambda n: _n_macro(n) == "code")
    if macro is not None:
        return _texto_macro_codigo(macro)
    poss = _n_um(no, lambda n: n.tag in ("td", "p", "div")
                 and _tem_indicio_codigo(_n_texto(n)))
    return _n_texto(poss) if poss is not None else ""


def _linhas_diretas(tabela):
    linhas = []
    for f in _n_els(tabela):
        if f.tag == "tr":
            linhas.append(f)
        elif f.tag in ("tbody", "thead"):
            linhas.extend(g for g in _n_els(f) if g.tag == "tr")
    return linhas


def _extrair_padroes_bookmarklet(raiz):
    """Porta dos 3 layouts do bookmarklet do download manual ("God Mode"):
    Padrão 4 (.sectionColumnWrapper), Padrão 2/3 (tabela "Print da Tela") e
    Padrão 1 (macros section com status). Devolve (secoes, n_itens)."""
    secoes, n = {}, 0
    atual = "Fluxo Importado"

    def empurrar(secao, status, riches, code_text):
        nonlocal n
        if not (code_text or status or riches):
            return
        n += 1
        secoes.setdefault(secao, []).append({
            "id": n,
            "status_macro": status,
            "rich_texts": riches,
            "code_json": _conf_parse_code(code_text),
        })

    # Padrão 4: wrappers .sectionColumnWrapper com tabela th "Tela"/"Código de"
    def tabela_p4(w):
        for t in _n_todos(w, lambda x: x.tag == "table" and "confluenceTable" in _n_classes(x)):
            ths = [_n_texto(th) for th in _n_todos(t, lambda x: x.tag == "th")]
            if any(h == "Tela" or "Código de" in h for h in ths):
                return t
        return None

    wrappers = []
    for w in _n_todos(raiz, lambda x: "sectionColumnWrapper" in _n_classes(x)):
        t = tabela_p4(w)
        if t is not None:
            wrappers.append((w, t))

    if wrappers:
        for w, tabela in wrappers:
            painel = _n_um(w, lambda x: "panelContent" in _n_classes(x))
            if painel is not None:
                tit = _n_um(painel, lambda x: x.tag in ("h1", "h2", "h3", "h4", "b", "strong"))
                if tit is not None and _n_texto(tit):
                    atual = _n_texto(tit)
            status, code_text, riches = None, "", []
            for linha in _linhas_diretas(tabela):
                th = _n_um(linha, lambda x: x.tag == "th")
                td = _n_um(linha, lambda x: x.tag == "td")
                if th is None or td is None:
                    continue
                rotulo = _n_texto(th)
                rot = rotulo.lower()
                if "tela" in rot or "evidencia" in rot or "evidência" in rot:
                    continue
                if "status" in rot:
                    status = _status_de(td) or status
                elif "código" in rot or "codigo" in rot:
                    code_text = _codigo_de(td) or _n_texto(td)
                else:
                    t_td = _n_texto(td)
                    if t_td:
                        riches.append({"label": rotulo, "html": t_td})
            empurrar(atual, status, riches, code_text)
        return secoes, n

    # Padrão 2/3: tabelas wrapped.confluenceTable com coluna "Print da Tela"
    tabelas2 = [t for t in _n_todos(raiz, lambda x: x.tag == "table"
                and "wrapped" in _n_classes(x) and "confluenceTable" in _n_classes(x))
                if any("Print da Tela" in _n_texto(th)
                       for th in _n_todos(t, lambda x: x.tag == "th"))]
    if tabelas2:
        for tabela in tabelas2:
            atual = _secao_por_irmaos(tabela, atual)
            for linha in _linhas_diretas(tabela):
                cols = [f for f in _n_els(linha) if f.tag == "td"]
                if len(cols) < 2:
                    continue
                blocos = _n_todos(cols[1], lambda x: _n_macro(x) == "info") or [cols[1]]
                for bloco in blocos:
                    status = _status_de(bloco)
                    macros_code = _n_todos(bloco, lambda x: _n_macro(x) == "code")
                    if macros_code:
                        code_text = "\n\n".join(_texto_macro_codigo(c) for c in macros_code)
                    else:
                        poss = _n_um(bloco, lambda x: x.tag in ("td", "p", "div")
                                     and _tem_indicio_codigo(_n_texto(x)))
                        code_text = _n_texto(poss) if poss is not None else ""
                    obs = []
                    for p in _n_todos(bloco, lambda x: x.tag in ("p", "th", "td")):
                        t_p = _n_texto(p)
                        if t_p.startswith("Observações") or t_p.startswith("Observação"):
                            t_p = re.sub(r"^Observaç(?:ões|ão)\s*:?\s*", "", t_p).strip()
                            if t_p:
                                obs.append(t_p)
                    riches = [{"label": "Observações", "html": " ".join(obs)}] if obs else []
                    empurrar(atual, status, riches, code_text)
        return secoes, n

    # Padrão 1: macros section que contêm macro de status
    sections1 = [s for s in _n_todos(raiz, lambda x: _n_macro(x) == "section")
                 if _n_um(s, lambda y: _n_macro(y) == "status") is not None]
    if sections1:
        for sec in sections1:
            atual = _secao_por_irmaos(sec, atual)
            status = _status_de(sec)
            code_text = _codigo_de(sec)
            riches = []
            for linha in _n_todos(sec, lambda x: x.tag == "tr"):
                th = _n_um(linha, lambda x: x.tag == "th"
                           or (x.tag == "td" and "confluenceTh" in _n_classes(x)))
                td = _n_um(linha, lambda x: x.tag == "td"
                           and "confluenceTh" not in _n_classes(x))
                if th is None or td is None:
                    continue
                rotulo = _n_texto(th)
                t_td = _n_texto(td)
                tem_img = _n_um(td, lambda x: x.tag == "img") is not None
                tem_status = _n_um(td, lambda x: _n_macro(x) == "status") is not None
                if rotulo and t_td and not _tem_indicio_codigo(t_td) and not tem_img and not tem_status:
                    riches.append({"label": rotulo, "html": t_td})
            empurrar(atual, status, riches, code_text)
        return secoes, n

    return {}, 0


# Fallback genérico: títulos de seção (expand/h1-h4) e blocos <pre> soltos.
_RE_BLOCOS_CONF = re.compile(
    r'<span[^>]*class="[^"]*expand-control-text[^"]*"[^>]*>(?P<expand>.*?)</span>'
    r'|<h(?P<nh>[1-4])[^>]*>(?P<h>.*?)</h(?P=nh)>'
    r'|<pre[^>]*>(?P<pre>.*?)</pre>',
    re.IGNORECASE | re.DOTALL)
_RE_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _montar_plano_de_html(titulo, corpo_html):
    """Monta o plano {"sections": ...} a partir do HTML renderizado da página,
    reproduzindo a varredura do bookmarklet do download manual: primeiro os 3
    layouts de tabela; se nenhum casar, os blocos <pre> agrupados por seção."""
    try:
        secoes, n = _extrair_padroes_bookmarklet(_dom(corpo_html))
    except Exception as e:
        print("[confluence] Varredura de layouts falhou ({}); tento os blocos "
              "<pre> soltos.".format(str(e)[:100]))
        secoes, n = {}, 0

    if not n:
        atual = "Fluxo Importado"
        for m in _RE_BLOCOS_CONF.finditer(corpo_html):
            if m.group("pre") is None:
                bruto = m.group("expand") if m.group("expand") is not None else m.group("h")
                nome = _RE_ESPACO.sub(" ", html.unescape(_RE_TAG.sub(" ", bruto))).strip()
                if nome:
                    atual = nome
                continue
            texto = html.unescape(_RE_TAG.sub("", _RE_BR.sub("\n", m.group("pre")))).strip()
            if not texto:
                continue
            # só trata como código o que parece código de tagueamento
            if not ("{" in texto or "dataLayer" in texto or "putString" in texto
                    or "logEvent" in texto or "event" in texto.lower()):
                continue
            n += 1
            secoes.setdefault(atual, []).append({"id": n, "code_json": _conf_parse_code(texto)})

    if not n:
        raise ValueError('Não encontrei blocos de código JSON na página "{}". Se o '
                         'plano estiver em outra página ou anexo, cole a URL dele.'.format(titulo))
    conteudo = json.dumps({"sections": secoes}, ensure_ascii=False)
    return {"arquivo": {"nome": titulo + ".json", "conteudo": conteudo}}


def _confluence_pagina_para_plano(base, pid):
    """Plano montado a partir do conteúdo da página via API REST (body.view)."""
    url = "{}/rest/api/content/{}?expand=body.view,body.export_view".format(base, pid)
    status, corpo, _ = _get_confluence(url)
    if status == 429:
        raise _RateLimit429()
    if status != 200:
        raise ValueError(_erro_confluence(status))
    dados = _carregar_json(corpo.decode("utf-8", errors="replace")) or {}
    titulo = str(dados.get("title") or "pagina-confluence")
    corpo_body = dados.get("body") or {}
    corpo_html = ((corpo_body.get("view") or {}).get("value")
                  or (corpo_body.get("export_view") or {}).get("value") or "")
    if not corpo_html:
        raise ValueError("A página veio sem conteúdo pela API (body.view vazio).")
    return _montar_plano_de_html(titulo, corpo_html)


def _confluence_plano_via_ui(base, pid):
    """Plano lido da página de interface (viewpage.action), como um navegador —
    caminho usado quando o admin bloqueou a API REST por rate limit (as páginas
    de interface e os downloads de anexos ficam fora desse limite)."""
    url = "{}/pages/viewpage.action?pageId={}".format(base, pid)
    status, corpo, _ = _get_confluence(url)
    texto_html = corpo.decode("utf-8", errors="replace")
    if (status != 200 or _eh_pagina_login(texto_html)) and CONFLUENCE_USUARIO:
        # tenta forçar autenticação básica na interface (recurso do Confluence)
        status, corpo, _ = _get_confluence(url + "&os_authType=basic")
        texto_html = corpo.decode("utf-8", errors="replace")
    if status != 200:
        raise ValueError(_erro_confluence(status))
    if _eh_pagina_login(texto_html):
        raise ValueError(
            "A API REST está bloqueada por limite de taxa e o Confluence pediu "
            "login para abrir a página como navegador (o token não vale para a "
            "interface). Preencha CONFLUENCE_USUARIO e CONFLUENCE_SENHA no "
            "tagauditor/config.py, ou peça ao admin do Confluence para isentar "
            "seu usuário do rate limit da API.")
    m = re.search(r'name="ajs-page-title"\s+content="([^"]*)"', texto_html)
    if m and m.group(1).strip():
        titulo = html.unescape(m.group(1)).strip()
    else:
        m = _RE_TITLE_CONF.search(texto_html)
        titulo = (re.sub(r"\s*-\s*Confluence\s*$", "", html.unescape(m.group(1)).strip())
                  if m else "pagina-confluence")
    return _montar_plano_de_html(titulo or "pagina-confluence", texto_html)


def confluence_baixar_json(url, nome=None):
    """Busca o plano a partir da URL colada. Devolve {"arquivo": {nome, conteudo}}
    ou {"opcoes": [nomes]} quando a página tem vários anexos .json."""
    url = (url or "").strip().strip('"')
    if not url:
        raise ValueError("Cole a URL da página do Confluence.")

    # URL já é o link direto de download de um anexo
    if "/download/attachments/" in url:
        status, corpo, _ = _get_confluence(url)
        if status != 200:
            raise ValueError(_erro_confluence(status))
        nome_arq = urllib.parse.unquote(
            url.split("/download/attachments/", 1)[1].split("?", 1)[0].split("/")[-1]) or "plano.json"
        texto = corpo.decode("utf-8", errors="replace")
        if _carregar_json(texto) is None:
            raise ValueError('O arquivo "{}" não contém JSON válido.'.format(nome_arq))
        return {"arquivo": {"nome": nome_arq, "conteudo": texto}}

    base, pid = _confluence_resolver_pagina(url)
    try:
        anexos = _confluence_anexos_json(base, pid)
        modo_ui = False
    except _RateLimit429:
        print("[confluence] API REST bloqueada pelo rate limit do admin (429). "
              "Mudando para o modo navegador (páginas de interface).")
        anexos = _confluence_anexos_via_ui(base, pid)
        modo_ui = True

    if nome:
        alvo = next((a for a in anexos if a["nome"] == nome), None)
        if not alvo:
            raise ValueError('Anexo "{}" não encontrado na página.'.format(nome))
    elif len(anexos) == 1:
        alvo = anexos[0]
    elif len(anexos) > 1:
        return {"opcoes": [a["nome"] for a in anexos]}
    elif modo_ui:
        return _confluence_plano_via_ui(base, pid)
    else:
        try:
            return _confluence_pagina_para_plano(base, pid)
        except _RateLimit429:
            return _confluence_plano_via_ui(base, pid)

    status, corpo, _ = _get_confluence(base + alvo["download"])
    if status != 200:
        raise ValueError(_erro_confluence(status))
    texto = corpo.decode("utf-8", errors="replace")
    if _carregar_json(texto) is None:
        raise ValueError('O anexo "{}" não contém JSON válido.'.format(alvo["nome"]))
    return {"arquivo": {"nome": alvo["nome"], "conteudo": texto}}
