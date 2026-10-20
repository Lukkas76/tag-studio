"""Servidor HTTP da plataforma (Tag Studio): rotas da API, páginas e boot.

Páginas (HTML de ./static com a config do front e o GTM injetados):
    GET  /  e /index.html   login + Home da plataforma (escolha da ferramenta)
    GET  /auditor           Tag Auditor (chat que audita o plano de tagueamento)
    GET  /planner           Tag Planner (o que taguear a partir da tela + pergunta)
    GET  /admin             painel administrativo (restrito a EMAILS_ADMIN)
    GET  /static/<arquivo>  arquivos de ./static (inclui ./static/js)

API:
    GET  /api/me            dados do usuário da sessão (Bearer token)
    GET  /api/resumo        uso do mês (do usuário e da plataforma) para a Home
    GET  /api/admin/atividade  logs de atividade (restrito a EMAILS_ADMIN)
    GET  /api/admin/exportar   logs de um mês em CSV (restrito a EMAILS_ADMIN)
    GET  /api/admin/backup     baixa o projeto inteiro em .zip (restrito a EMAILS_ADMIN)
    GET  /health            liveness
    POST /api/login/inicio  passo 1 do login Google (código de dispositivo)
    POST /api/login/status  passo 2 do login Google (polling da autorização)
    POST /api/logout        invalida a sessão
    POST /api/feedback      👍/👎 de uma resposta (Tag Auditor ou Tag Planner)
    POST /api/confluence    busca o plano .json de uma página do Confluence
    POST /api/enviar        envia mensagem/plano para a IA auditar (Tag Auditor)
    POST /api/planner/figma     frames e camadas de um arquivo do Figma (Tag Planner)
    POST /api/planner/analisar  análise da tela ou pergunta de acompanhamento (Tag Planner)

Corpo de POST acima de LIMITE_CORPO_MB é recusado com 413 (as imagens do Tag
Planner viajam em base64 dentro do JSON).
Toda ação de usuário é registrada na trilha de auditoria (tagauditor/atividade.py).
"""
import html
import http.server
import io
import json
import os
import re
import secrets
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
from datetime import datetime

from . import atividade
from . import planner
from .config import (
    BASE_DIR, PORTA_INICIAL, MODO_ASSINCRONO, TAMANHO_MAX_PARTE,
    FILTRAR_SEM_AJUSTES, ENXUGAR_PLANO, LIMITE_VALOR_STR, LIMITE_OBS_STR,
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, AQUECER_AGENTE, EMAILS_ADMIN,
    TEMPO_MANUAL_MIN, TEMPO_COM_FERRAMENTA_MIN,
    TEMPO_MANUAL_PLANNER_MIN, TEMPO_COM_PLANNER_MIN,
    MOEDA, COTACAO_DOLAR, PRECO_ENTRADA_USD_MILHAO, PRECO_SAIDA_USD_MILHAO,
    PASTA_LOGS_BACKUP, NOME_PLATAFORMA, LIMITE_CORPO_MB,
    GTM_ID, GA4_STREAM_ID, TAG_PRODUTO, TAG_FLUXO,
    TAG_PAGE_LOCATION, TAG_PAGE_TITLE, TAG_EVENT_TYPE, TAG_DEBUG,
)
from .bridge import (
    enviar_para_agente, obter_token, renovador_token, aquecer_agente,
)
from .confluence import confluence_baixar_json
from .filtro import filtrar_falsos_positivos
from .limites import aplicar_limites_ga4
from .login_google import google_device_inicio, google_device_status, rota_google
from .plano import montar_mensagem
from .sessoes import SESSOES_ATIVAS, atualizar_registro_login

# ---- Frontend estatico (servido de ./static) --------------------------------
_STATIC_DIR = os.path.join(BASE_DIR, "static")

_MIME_ESTATICO = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def _celula_csv(valor):
    """Célula do CSV de logs. Texto digitado por usuário (comentário do 👎,
    pergunta do Planner...) que comece com = + - @ viraria FÓRMULA no Excel:
    ganha um apóstrofo na frente e abre como texto. Números ficam iguais."""
    if isinstance(valor, str) and valor[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + valor
    return valor


def _config_frontend_json():
    """Config injetada nas páginas (window.TAG_CONFIG) para o front.
    "<", ">" e "&" saem como \\u003c, \\u003e e \\u0026 (o mesmo texto em
    JSON/JS): nenhum valor do config.py ("</script>", "<!--") consegue fechar
    ou desmontar o <script> onde a config é injetada."""
    return json.dumps({
        "limiteParte": 800000 if MODO_ASSINCRONO else TAMANHO_MAX_PARTE,
        "filtrarOk": bool(FILTRAR_SEM_AJUSTES),
        "enxugar": bool(ENXUGAR_PLANO),
        "limiteValor": LIMITE_VALOR_STR,
        "limiteObs": LIMITE_OBS_STR,
        "loginGoogleConfigurado": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        # Tagueamento GA4/GTM (lido por static/js/tagueamento.js)
        "tag": {
            "gtmId": GTM_ID,
            "ga4StreamId": GA4_STREAM_ID,
            "produto": TAG_PRODUTO,
            "fluxo": TAG_FLUXO,
            "pageLocation": TAG_PAGE_LOCATION,
            "pageTitle": TAG_PAGE_TITLE,
            "eventType": TAG_EVENT_TYPE,
            "debug": bool(TAG_DEBUG),
        },
        # Nome da plataforma (TA.cfg.plataforma.nome) e limites/parâmetros de
        # imagem do Tag Planner (lidos por static/js/planner*.js)
        "plataforma": {"nome": NOME_PLATAFORMA},
        "planner": planner.config_frontend(),
    }, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


# Páginas da plataforma: caminho -> arquivo de ./static. Todas passam pela
# troca de placeholders (_trocar_placeholders).
_PAGINAS = {
    "/": "index.html", "/index.html": "index.html",
    "/auditor": "auditor.html", "/auditor/": "auditor.html",
    "/planner": "planner.html", "/planner/": "planner.html",
    "/admin": "admin.html", "/admin/": "admin.html",
}

_RE_PLACEHOLDER = re.compile(r"__(CONFIG_JSON|GTM_HEAD|GTM_BODY|NOME_PLATAFORMA)__")


# ---- Google Tag Manager (mapa "[Bot] Tag Auditor | Home v1") ------------------
# Trechos COPIADOS do documento de tagueamento, palavra por palavra: o head vai
# o mais alto possível na <head> e o noscript logo depois da <body>. O ID sai do
# GTM_ID (config.py); com GTM_ID vazio nada é injetado e o tagueamento fica off.
_GTM_HEAD_MODELO = """<!-- Google Tag Manager -->
<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':
new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
})(window,document,'script','dataLayer','__GTM_ID__');</script>
<!-- End Google Tag Manager -->"""

_GTM_BODY_MODELO = """<!-- Google Tag Manager (noscript) -->
<noscript><iframe src="https://www.googletagmanager.com/ns.html?id=__GTM_ID__"
height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>
<!-- End Google Tag Manager (noscript) -->"""


def _gtm_trechos():
    """(head, body) do GTM com o ID do config. ("", "") se GTM_ID estiver vazio."""
    if not GTM_ID:
        return "", ""
    return (_GTM_HEAD_MODELO.replace("__GTM_ID__", GTM_ID),
            _GTM_BODY_MODELO.replace("__GTM_ID__", GTM_ID))


def _trocar_placeholders(texto):
    """Troca __CONFIG_JSON__, __GTM_HEAD__, __GTM_BODY__ e __NOME_PLATAFORMA__
    (este escapado para HTML) numa passada só: o que foi injetado (o JSON da
    config, o GTM) nunca é varrido de novo atrás de outro placeholder."""
    gtm_head, gtm_body = _gtm_trechos()
    valores = {
        "CONFIG_JSON": _config_frontend_json(),
        "GTM_HEAD": gtm_head,
        "GTM_BODY": gtm_body,
        "NOME_PLATAFORMA": html.escape(NOME_PLATAFORMA),
    }
    return _RE_PLACEHOLDER.sub(lambda achado: valores[achado.group(1)], texto)


def _ler_estatico(nome):
    """Le um arquivo de ./static com seguranca (sem path traversal).
    Aceita subpastas (ex.: js/chat.js). Devolve (bytes, mime) ou (None, None)."""
    nome = (nome or "").replace("\\", "/")
    if not nome or nome.startswith(("/", ".")) or ".." in nome:
        return None, None
    caminho = os.path.normpath(os.path.join(_STATIC_DIR, nome))
    # o caminho final TEM de continuar dentro de ./static
    if os.path.commonprefix([caminho, _STATIC_DIR]) != _STATIC_DIR:
        return None, None
    if not os.path.isfile(caminho):
        return None, None
    ext = os.path.splitext(caminho)[1].lower()
    mime = _MIME_ESTATICO.get(ext, "application/octet-stream")
    with open(caminho, "rb") as f:
        return f.read(), mime


SESSION_ID = None

_RE_MES = re.compile(r"^\d{4}-\d{2}$")


def _eh_admin(usuario):
    """True se o usuário logado é administrador (EMAILS_ADMIN no config.py)."""
    return bool(usuario) and usuario.get("email", "").strip().lower() in EMAILS_ADMIN


# Lê o mês inteiro: o resumo é contagem, não pode parar nos N mais recentes.
_LIMITE_LEITURA_RESUMO = 10 ** 7


def _resumo_do_mes(email):
    """Uso do mês atual (GET /api/resumo), contado na trilha de atividade:
      auditoria  = acao "auditoria" com status ok;
      análise    = acao "planner_analise" com status ok e modo "analise"
                   (pergunta de acompanhamento não conta como análise);
      usuarios   = e-mails distintos com ao menos uma auditoria ou análise;
      ultima_atividade = "quando" mais recente do usuário entre auditorias e
                   análises do Planner (qualquer status: tentativa também é uso).
    Só leitura — abrir a Home não grava nada no log."""
    mes = datetime.now().strftime("%Y-%m")
    eventos = atividade.ler(mes=mes, limite=_LIMITE_LEITURA_RESUMO)
    meu = (email or "").strip().lower()
    usuario = {"auditorias": 0, "analises": 0, "ultima_atividade": None}
    plataforma = {"auditorias": 0, "analises": 0, "usuarios": 0}
    pessoas = set()
    for ev in eventos:
        acao = ev.get("acao")
        if acao not in ("auditoria", "planner_analise"):
            continue
        quem = str(ev.get("email") or "").strip().lower()
        eh_meu = bool(meu) and quem == meu
        quando = str(ev.get("quando") or "")
        if eh_meu and quando and (usuario["ultima_atividade"] is None
                                  or quando > usuario["ultima_atividade"]):
            usuario["ultima_atividade"] = quando      # ISO 8601: ordem de texto = ordem de data
        if ev.get("status") != "ok":
            continue
        if acao == "auditoria":
            chave = "auditorias"
        elif ev.get("modo") == "analise":
            chave = "analises"
        else:
            continue
        plataforma[chave] += 1
        if eh_meu:
            usuario[chave] += 1
        if quem:
            pessoas.add(quem)
    plataforma["usuarios"] = len(pessoas)
    return {"mes": mes, "usuario": usuario, "plataforma": plataforma}


class ManipuladorChat(http.server.BaseHTTPRequestHandler):
    def log_message(self, formato, *args):
        pass

    def _responder(self, status, corpo, tipo="application/json; charset=utf-8"):
        # "replace": meio emoji (surrogate solto vindo do navegador) não pode
        # derrubar a resposta depois do trabalho feito.
        dados = corpo.encode("utf-8", "replace")
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(dados)

    def _responder_bytes(self, status, dados, tipo):
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(dados)

    def _servir_pagina(self, nome):
        """Serve uma página HTML de ./static (index, auditor, planner, admin)
        com os placeholders trocados (config do front, GTM, nome da plataforma)."""
        dados, _mime = _ler_estatico(nome)
        if dados is None:
            self._responder(500, "static/{} não encontrado. Verifique se a "
                            "pasta 'static' está na raiz do projeto.".format(nome),
                            "text/plain; charset=utf-8")
            return
        pagina = _trocar_placeholders(dados.decode("utf-8"))
        self._responder_bytes(200, pagina.encode("utf-8"), "text/html; charset=utf-8")

    def _servir_estatico(self, nome):
        dados, mime = _ler_estatico(nome)
        if dados is None:
            self._responder(404, "Não encontrado", "text/plain; charset=utf-8")
            return
        self._responder_bytes(200, dados, mime)

    def _token_sessao(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[len("Bearer "):].strip()
        return None

    def _usuario_atual(self):
        """Usuário logado desta requisição ({email, nome, foto}) ou None."""
        return SESSOES_ATIVAS.get(self._token_sessao() or "")

    def _ip(self):
        try:
            return self.client_address[0]
        except Exception:
            return ""

    def _ler_corpo_json(self):
        tamanho = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(tamanho).decode("utf-8")) if tamanho else {}

    def do_GET(self):
        caminho = self.path.split("?", 1)[0]
        if caminho in _PAGINAS:
            self._servir_pagina(_PAGINAS[caminho])
        elif caminho.startswith("/static/"):
            self._servir_estatico(caminho[len("/static/"):])
        elif caminho == "/api/me":
            usuario = self._usuario_atual()
            if usuario:
                self._responder(200, json.dumps(
                    {"usuario": usuario, "admin": _eh_admin(usuario)}, ensure_ascii=False))
            else:
                self._responder(401, json.dumps({"erro": "Sessão inválida ou expirada."}, ensure_ascii=False))
        elif caminho == "/api/resumo":
            self._api_resumo()
        elif caminho == "/api/admin/atividade":
            self._api_admin_atividade()
        elif caminho == "/api/admin/exportar":
            self._api_admin_exportar()
        elif caminho == "/api/admin/backup":
            self._api_admin_backup()
        elif caminho in ("/health", "/healthz", "/ready"):
            self._responder(200, json.dumps({"status": "ok"}))
        else:
            self._responder(404, "Não encontrado", "text/plain; charset=utf-8")

    def _api_resumo(self):
        """Resumo de uso do mês para a Home (ver _resumo_do_mes). Sem log."""
        usuario = self._usuario_atual()
        if not usuario:
            self._responder(401, json.dumps({"erro": "Sessão inválida ou expirada. Faça login novamente."}))
            return
        try:
            self._responder(200, json.dumps(_resumo_do_mes(usuario.get("email", "")),
                                            ensure_ascii=False))
        except Exception as e:
            print("[resumo][erro] {}".format(str(e)[:200]))
            self._responder(500, json.dumps(
                {"erro": "Falha ao montar o resumo de uso: {}".format(str(e)[:200])},
                ensure_ascii=False))

# Fora do zip de backup: dados locais/temporarios que nao fazem parte do
# codigo (logs e registro ficam SO no servidor de origem).
    _ZIP_PASTAS_FORA = {"__pycache__", "logs", ".git", "node_modules"}
    _ZIP_ARQUIVOS_FORA = {"registro.txt"}

    def _api_admin_backup(self):
        """Gera e baixa o projeto inteiro em .zip (para instalar em outro PC)."""
        usuario = self._usuario_atual()
        if not usuario:
            self._responder(401, json.dumps({"erro": "Sessão inválida ou expirada. Faça login novamente."}))
            return
        if not _eh_admin(usuario):
            atividade.registrar("admin_negado", usuario, self._ip(), rota="backup")
            self._responder(403, json.dumps(
                {"erro": "Acesso restrito aos administradores do {}.".format(NOME_PLATAFORMA)},
                ensure_ascii=False))
            return
        try:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
                for pasta, subpastas, arquivos in os.walk(BASE_DIR):
                    subpastas[:] = [s for s in subpastas
                                    if s not in self._ZIP_PASTAS_FORA and not s.startswith(".")]
                    for nome in arquivos:
                        if (nome in self._ZIP_ARQUIVOS_FORA or nome.startswith("~")
                                or nome.endswith((".pyc", ".zip"))):
                            continue
                        caminho = os.path.join(pasta, nome)
                        z.write(caminho, os.path.relpath(caminho, BASE_DIR))
            dados = buffer.getvalue()
            nome_zip = "tag-auditor-projeto_{}.zip".format(datetime.now().strftime("%Y-%m-%d"))
            atividade.registrar("admin_backup", usuario, self._ip(),
                                arquivo=nome_zip, bytes=len(dados))
            print("[admin] {} baixou o projeto ({} KB).".format(
                usuario["email"], len(dados) // 1024))
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(dados)))
            self.send_header("Content-Disposition",
                             'attachment; filename="{}"'.format(nome_zip))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(dados)
        except Exception as e:
            print("[admin][erro] backup: {}".format(e))
            self._responder(500, json.dumps(
                {"erro": "Falha ao gerar o zip: {}".format(str(e)[:200])}, ensure_ascii=False))

    # Ordem das colunas do CSV (fixas primeiro; o resto entra em ordem alfabética)
    _CSV_COLS_FIXAS = ["quando", "acao", "email", "nome", "ip", "status"]

    def _api_admin_exportar(self):
        """Exporta a trilha de um mês em CSV (Excel-friendly), só para admin."""
        usuario = self._usuario_atual()
        if not usuario:
            self._responder(401, json.dumps({"erro": "Sessão inválida ou expirada. Faça login novamente."}))
            return
        if not _eh_admin(usuario):
            atividade.registrar("admin_negado", usuario, self._ip(), rota="exportar")
            self._responder(403, json.dumps(
                {"erro": "Acesso restrito aos administradores do {}.".format(NOME_PLATAFORMA)},
                ensure_ascii=False))
            return
        try:
            import csv
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            mes = (qs.get("mes") or [""])[0].strip()
            mes = mes if _RE_MES.match(mes) else None
            eventos = atividade.ler(mes=mes, limite=100000)
            # colunas = fixas + demais chaves encontradas (ordenadas)
            extras = sorted({k for ev in eventos for k in ev.keys()}
                            - set(self._CSV_COLS_FIXAS))
            colunas = self._CSV_COLS_FIXAS + extras
            buffer = io.StringIO()
            # ';' porque o Excel pt-BR usa ponto-e-vírgula como separador
            escritor = csv.writer(buffer, delimiter=";")
            escritor.writerow(colunas)
            for ev in eventos:
                escritor.writerow([_celula_csv(ev.get(c, "")) for c in colunas])
            # BOM para o Excel abrir os acentos corretamente
            dados = ("﻿" + buffer.getvalue()).encode("utf-8")
            nome_arq = "tag-auditor-logs_{}.csv".format(
                mes or datetime.now().strftime("%Y-%m"))
            atividade.registrar("admin_export", usuario, self._ip(),
                                mes=mes or "atual", eventos=len(eventos))
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(dados)))
            self.send_header("Content-Disposition",
                             'attachment; filename="{}"'.format(nome_arq))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(dados)
        except Exception as e:
            print("[admin][erro] exportar: {}".format(e))
            self._responder(500, json.dumps(
                {"erro": "Falha ao exportar: {}".format(str(e)[:200])}, ensure_ascii=False))

    def _api_admin_atividade(self):
        """Logs de atividade para o painel administrativo (EMAILS_ADMIN)."""
        usuario = self._usuario_atual()
        if not usuario:
            self._responder(401, json.dumps({"erro": "Sessão inválida ou expirada. Faça login novamente."}))
            return
        if not _eh_admin(usuario):
            atividade.registrar("admin_negado", usuario, self._ip())
            self._responder(403, json.dumps(
                {"erro": "Acesso restrito aos administradores do {}.".format(NOME_PLATAFORMA)},
                ensure_ascii=False))
            return
        try:
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            mes = (qs.get("mes") or [""])[0].strip()
            mes = mes if _RE_MES.match(mes) else None
            try:
                limite = min(max(int((qs.get("limite") or ["2000"])[0]), 1), 5000)
            except ValueError:
                limite = 2000
            eventos = atividade.ler(mes=mes, limite=limite)
            atividade.registrar("admin_logs", usuario, self._ip(),
                                mes=mes or "atual", eventos=len(eventos))
            self._responder(200, json.dumps(
                {"meses": atividade.listar_meses(), "eventos": eventos,
                 "baseline_manual_min": TEMPO_MANUAL_MIN,
                 "baseline_ferramenta_min": TEMPO_COM_FERRAMENTA_MIN,
                 # Tempo economizado do Tag Planner (minutos por análise)
                 "baseline_planner_manual_min": TEMPO_MANUAL_PLANNER_MIN,
                 "baseline_planner_ferramenta_min": TEMPO_COM_PLANNER_MIN,
                 "nome_plataforma": NOME_PLATAFORMA,
                 "moeda": MOEDA,
                 "cotacao_dolar": COTACAO_DOLAR,
                 "preco_entrada_usd_milhao": PRECO_ENTRADA_USD_MILHAO,
                 "preco_saida_usd_milhao": PRECO_SAIDA_USD_MILHAO}, ensure_ascii=False))
        except Exception as e:
            print("[admin][erro] {}".format(e))
            self._responder(500, json.dumps(
                {"erro": "Falha ao ler os logs: {}".format(str(e)[:200])}, ensure_ascii=False))

    # Descarte do corpo recusado: em blocos (sem guardar nada na memória), com
    # teto (um Content-Length absurdo não prende a thread lendo para sempre) e
    # com tempo máximo por bloco (cliente que anuncia e não manda).
    _BLOCO_DESCARTE = 64 * 1024
    _MAX_DESCARTE = 512 * 1024 * 1024
    _TIMEOUT_DESCARTE_S = 20

    def _descartar_corpo(self, tamanho):
        restante = min(tamanho, self._MAX_DESCARTE)
        try:
            timeout_antes = self.connection.gettimeout()
        except Exception:
            timeout_antes = None
        try:
            self.connection.settimeout(self._TIMEOUT_DESCARTE_S)
            while restante > 0:
                bloco = self.rfile.read(min(self._BLOCO_DESCARTE, restante))
                if not bloco:
                    break
                restante -= len(bloco)
        except (OSError, ValueError):
            pass    # conexão caiu/estourou o tempo: só falta responder
        finally:
            try:
                self.connection.settimeout(timeout_antes)
            except Exception:
                pass

    def _recusou_corpo(self, rota):
        """Confere o Content-Length antes de qualquer rota POST. Acima de
        LIMITE_CORPO_MB responde 413; inválido responde 400. Devolve True se
        já respondeu (a rota não deve continuar)."""
        bruto = (self.headers.get("Content-Length") or "").strip()
        if not bruto:
            return False
        try:
            tamanho = int(bruto)
        except ValueError:
            tamanho = -1
        if tamanho < 0:
            # Sem um tamanho confiável não dá para ler (nem descartar) o corpo
            # com segurança: responde e encerra a conexão.
            self.close_connection = True
            self._responder(400, json.dumps(
                {"erro": "Requisição inválida (cabeçalho Content-Length)."}, ensure_ascii=False))
            return True
        if tamanho <= LIMITE_CORPO_MB * 1024 * 1024:
            return False
        # Lê e descarta o corpo ANTES de responder: se o servidor responde e
        # fecha com o upload ainda chegando, o navegador mostra "conexão
        # abortada" no lugar da mensagem do 413.
        self._descartar_corpo(tamanho)
        self.close_connection = True
        mb = "{:.1f}".format(tamanho / 1048576.0).replace(".", ",")
        # rota em ASCII: o caminho vem do cliente e o console pode ser cp1252
        print("[servidor] Requisição de {} MB recusada em {} (limite: {} MB).".format(
            mb, rota[:100].encode("ascii", "replace").decode("ascii"), LIMITE_CORPO_MB))
        self._responder(413, json.dumps(
            {"erro": "O conteúdo enviado ({} MB) passou do limite de {} MB do servidor. "
                     "Envie menos telas ou imagens menores.".format(mb, LIMITE_CORPO_MB)},
            ensure_ascii=False))
        return True

    def _negar_sem_sessao(self):
        """401 de rota POST protegida. Lê e descarta o corpo ANTES de responder
        (como no 413): com um upload grande ainda chegando (imagens do Tag
        Planner), responder e fechar vira "conexão abortada" no navegador, e o
        api.js nunca vê o 401 que manda a pessoa de volta ao login. O tamanho
        já passou por _recusou_corpo (limite de LIMITE_CORPO_MB)."""
        try:
            tamanho = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            tamanho = 0
        if tamanho > 0:
            self._descartar_corpo(tamanho)
        self.close_connection = True
        self._responder(401, json.dumps({"erro": "Sessão inválida ou expirada. Faça login novamente."}))

    def _api_planner(self, rota):
        """POST /api/planner/figma e /api/planner/analisar: confere a sessão e
        repassa ao planner.py, que valida, chama Figma/IA e registra o log."""
        usuario = self._usuario_atual()
        if not usuario:
            self._negar_sem_sessao()
            return
        try:
            dados = self._ler_corpo_json()
        except Exception:
            dados = None    # corpo que não é JSON: o planner responde 400 e registra
        try:
            if rota == "/api/planner/figma":
                status, corpo = planner.rota_figma(dados, usuario, self._ip())
            else:
                status, corpo = planner.rota_analisar(dados, usuario, self._ip())
        except Exception as e:
            print("[planner][erro] {!a}".format(e)[:300])   # ascii(): seguro em console cp1252
            status, corpo = 500, {"erro": "Erro interno do Planner. Tente de novo."}
        try:
            self._responder(status, json.dumps(corpo, ensure_ascii=False))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # A análise leva minutos: se a pessoa fechou a aba, o resultado já
            # está no log e não há para quem responder.
            print("[planner] O navegador fechou a conexão antes da resposta ({}).".format(rota))

    def do_POST(self):
        # Caminho sem a query string (as rotas novas casam por ele)
        rota = self.path.split("?", 1)[0]

        # 0. Corpo grande demais (imagens do Tag Planner em base64): 413 antes
        #    de qualquer rota ler o corpo para a memória.
        if self._recusou_corpo(rota):
            return

        # 1a. Login Google - passo 1: gera o código de dispositivo (ABCD-EFGH)
        if self.path == "/api/login/inicio":
            try:
                pedido = google_device_inicio()
                self._responder(200, json.dumps(pedido, ensure_ascii=False))
            except ValueError as e:
                atividade.registrar("login_falha", ip=self._ip(), motivo=str(e)[:200])
                self._responder(401, json.dumps({"erro": str(e)}, ensure_ascii=False))
            except Exception:
                self._responder(400, json.dumps({"erro": "Requisição inválida."}, ensure_ascii=False))
            return

        # 1b. Login Google - passo 2: aguarda o usuário autorizar em google.com/device
        if self.path == "/api/login/status":
            try:
                dados = self._ler_corpo_json()
                estado, extra = google_device_status((dados.get("pedido_id") or "").strip())
                if estado == "AGUARDANDO":
                    self._responder(200, json.dumps({"status": "aguardando"}))
                elif estado == "OK":
                    usuario = extra
                    token = secrets.token_hex(16)
                    SESSOES_ATIVAS[token] = usuario

                    atualizar_registro_login(usuario["email"])
                    atividade.registrar("login", usuario, self._ip())
                    print("[login] {} <{}> entrou com a conta Google.".format(
                        usuario["nome"], usuario["email"]))

                    self._responder(200, json.dumps(
                        {"success": True, "token": token, "usuario": usuario,
                         "admin": _eh_admin(usuario)}, ensure_ascii=False))
                else:
                    atividade.registrar("login_falha", ip=self._ip(), motivo=str(extra)[:200])
                    self._responder(401, json.dumps({"erro": extra}, ensure_ascii=False))
            except Exception:
                self._responder(400, json.dumps({"erro": "Requisição inválida."}, ensure_ascii=False))
            return

        # 2. Rota de Logout: invalida a sessão no servidor
        if self.path == "/api/logout":
            token = self._token_sessao()
            usuario = SESSOES_ATIVAS.pop(token, None) if token else None
            if usuario:
                atividade.registrar("logout", usuario, self._ip())
                print("[login] {} <{}> saiu.".format(usuario["nome"], usuario["email"]))
            self._responder(200, json.dumps({"success": True}))
            return

        # 2a2. Feedback do usuário sobre uma resposta (👍 / 👎) do Tag Auditor
        #      ou do Tag Planner ("ferramenta"; ausente/desconhecida = auditor,
        #      que é quem mandava feedback antes de existir o campo)
        if self.path == "/api/feedback":
            usuario = self._usuario_atual()
            if not usuario:
                self._negar_sem_sessao()
                return
            try:
                dados = self._ler_corpo_json()
                voto = (dados.get("voto") or "").strip().lower()
                if voto not in ("like", "dislike"):
                    self._responder(400, json.dumps({"erro": "Voto inválido."}))
                    return
                ferramenta = str(dados.get("ferramenta") or "").strip().lower()
                if ferramenta not in ("auditor", "planner"):
                    ferramenta = "auditor"
                # sem_surrogates: meio emoji no comentário faria o log recusar a
                # linha inteira (o voto se perderia)
                atividade.registrar("feedback", usuario, self._ip(),
                                    voto=voto,
                                    sessao=planner.sem_surrogates(str(dados.get("session_id") or "").strip()[:80]),
                                    msg_id=planner.sem_surrogates(str(dados.get("msg_id") or "")[:60]),
                                    comentario=planner.sem_surrogates(str(dados.get("comentario") or "").strip()[:500]),
                                    ferramenta=ferramenta)
                self._responder(200, json.dumps({"success": True}))
            except Exception:
                self._responder(400, json.dumps({"erro": "Requisição inválida."}, ensure_ascii=False))
            return

        # 2b. Busca o plano .json direto do Confluence (URL colada pelo usuário)
        if self.path == "/api/confluence":
            usuario = self._usuario_atual()
            if not usuario:
                self._negar_sem_sessao()
                return
            url = ""
            try:
                dados = self._ler_corpo_json()
                url = (dados.get("url") or "").strip()
                resultado = confluence_baixar_json(url, dados.get("nome"))
                if "arquivo" in resultado:
                    atividade.registrar("confluence", usuario, self._ip(), url=url[:300],
                                        status="ok", arquivo=resultado["arquivo"]["nome"],
                                        caracteres=len(resultado["arquivo"]["conteudo"]))
                    print('[confluence] "{}" obtido ({} caracteres).'.format(
                        resultado["arquivo"]["nome"], len(resultado["arquivo"]["conteudo"])))
                else:
                    atividade.registrar("confluence", usuario, self._ip(), url=url[:300],
                                        status="opcoes", anexos=len(resultado.get("opcoes") or []))
                self._responder(200, json.dumps(resultado, ensure_ascii=False))
            except ValueError as e:
                atividade.registrar("confluence", usuario, self._ip(), url=url[:300],
                                    status="erro", erro=str(e)[:200])
                self._responder(400, json.dumps({"erro": str(e)}, ensure_ascii=False))
            except Exception as e:
                atividade.registrar("confluence", usuario, self._ip(), url=url[:300],
                                    status="erro", erro=str(e)[:200])
                print("[confluence][erro] {}".format(e))
                self._responder(502, json.dumps(
                    {"erro": "Falha ao buscar no Confluence: {}".format(str(e)[:200])}, ensure_ascii=False))
            return

        # 2c. Tag Planner: frames do Figma e análise da tela (planner.py)
        if rota in ("/api/planner/figma", "/api/planner/analisar"):
            self._api_planner(rota)
            return

        if self.path != "/api/enviar":
            self._responder(404, json.dumps({"erro": "Rota inválida."}))
            return

        # 3. Proteção da rota do Chat (/api/enviar)
        usuario = self._usuario_atual()
        if not usuario:
            self._negar_sem_sessao()
            return

        # Rota de envio de mensagem/plano para a auditoria
        inicio = time.time()
        sessao, com_anexo, tam_msg = "", False, 0
        try:
            dados = self._ler_corpo_json()
            sessao = (dados.get("session_id") or "").strip() or (SESSION_ID or "sem-sessao")
            com_anexo = bool(dados.get("json_anexo"))
            mensagem = montar_mensagem(dados.get("texto", ""), dados.get("json_anexo"))
            tam_msg = len(mensagem)
            historico = dados.get("historico")
            if not isinstance(historico, list):
                historico = None
            print("[chat] Enviando mensagem ({} caracteres, {} de histórico) - sessão {}".format(
                len(mensagem), len(historico) if historico else 0, sessao))
            resposta, uso = enviar_para_agente(mensagem, sessao, historico)
            resposta = filtrar_falsos_positivos(resposta)
            # limites do GA4 (tamanho/quantidade): contados em codigo sobre o
            # JSON bruto, nunca pela IA - ver limites.py
            resposta = aplicar_limites_ga4(resposta, dados.get("json_anexo"))
            uso = uso or {}
            atividade.registrar("auditoria", usuario, self._ip(), sessao=sessao,
                                status="ok", com_anexo=com_anexo, caracteres=tam_msg,
                                caracteres_resposta=len(resposta),
                                duracao_s=round(time.time() - inicio, 1),
                                tokens_entrada=uso.get("entrada", 0),
                                tokens_saida=uso.get("saida", 0),
                                tokens_total=uso.get("total", 0),
                                tokens_fonte=uso.get("fonte", ""))
            try:
                self._responder(200, json.dumps({"resposta": resposta, "user_content": mensagem}, ensure_ascii=False))
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # A pessoa saiu da página antes da resposta: a auditoria já foi
                # registrada como ok - não pode virar um 2º evento de "erro".
                print("[chat] O navegador fechou a conexão antes da resposta (sessão {}).".format(sessao))
        except ValueError as e:
            atividade.registrar("auditoria", usuario, self._ip(), sessao=sessao,
                                status="erro", com_anexo=com_anexo, caracteres=tam_msg,
                                erro=str(e)[:200], duracao_s=round(time.time() - inicio, 1))
            self._responder(400, json.dumps({"erro": str(e)}, ensure_ascii=False))
        except Exception as e:
            atividade.registrar("auditoria", usuario, self._ip(), sessao=sessao,
                                status="erro", com_anexo=com_anexo, caracteres=tam_msg,
                                erro=str(e)[:200], duracao_s=round(time.time() - inicio, 1))
            print("[erro] {}".format(e))
            self._responder(502, json.dumps({"erro": str(e)}, ensure_ascii=False))


class ServidorChat(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _enderecos_de_acesso(porta):
    url_nome = url_fqdn = url_ip = None
    try:
        nome = (socket.gethostname() or "").strip()
        if nome and "." not in nome:
            url_nome = "http://{}:{}".format(nome.lower(), porta)
        try:
            fqdn = (socket.getfqdn() or "").strip().rstrip(".")
            if fqdn and "." in fqdn and fqdn.lower() != nome.lower():
                url_fqdn = "http://{}:{}".format(fqdn.lower(), porta)
        except Exception:
            pass
        if url_nome is None and url_fqdn is None and nome:
            url_nome = "http://{}:{}".format(nome.lower(), porta)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            url_ip = "http://{}:{}".format(s.getsockname()[0], porta)
        finally:
            s.close()
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
            if ip and not ip.startswith("127."):
                url_ip = "http://{}:{}".format(ip, porta)
        except Exception:
            pass
    return url_nome, url_fqdn, url_ip


def _registrar_dns():
    if sys.platform != "win32":
        return
    try:
        subprocess.run(["ipconfig", "/registerdns"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30, check=False)
        print("[dns] Nome da máquina re-registrado no DNS com o IP atual "
              "(ipconfig /registerdns). A propagação leva ~1-2 min.")
    except Exception as e:
        print("[dns] Não consegui rodar 'ipconfig /registerdns' ({}). "
              "Se a URL pelo nome não abrir nos colegas, rode o comando "
              "manualmente no cmd.".format(str(e)[:80]))


def _backup_logs_periodico():
    """Copia os logs para PASTA_LOGS_BACKUP ao iniciar e a cada 24h."""
    while True:
        n = atividade.backup(PASTA_LOGS_BACKUP)
        if n:
            print("[backup] {} arquivo(s) de log copiado(s) para {}.".format(
                n, PASTA_LOGS_BACKUP))
        time.sleep(24 * 3600)


def main():
    global SESSION_ID
    SESSION_ID = "865{:09d}".format(int(time.time()) % 10 ** 9)

    servidor = None
    porta = PORTA_INICIAL
    for _ in range(20):
        try:
            servidor = ServidorChat(("0.0.0.0", porta), ManipuladorChat)
            break
        except OSError:
            porta += 1
    if servidor is None:
        print("Não encontrei uma porta livre. Feche outros programas e tente de novo.")
        sys.exit(1)

    url = "http://127.0.0.1:{}".format(porta)
    url_nome, url_fqdn, url_ip = _enderecos_de_acesso(porta)
    print("=" * 62)
    print("  {} - Auditor + Planner".format(NOME_PLATAFORMA.upper()))
    print("  (Bridge / Bradesco IA Gen)")
    print("=" * 62)
    print("  Escutando na porta {} (0.0.0.0)".format(porta))
    print("  No seu PC, abra:  {}".format(url))
    print("      Auditor:  {}/auditor".format(url))
    print("      Planner:  {}/planner".format(url))
    print()
    if url_nome or url_fqdn:
        print("  COMPARTILHE ESTA URL (nome da máquina - NÃO muda com a VPN):")
        if url_nome:
            print("      {}".format(url_nome))
        if url_fqdn:
            print("      {}  {}".format(url_fqdn,
                  "(use esta se a de cima não abrir)" if url_nome else ""))
        if url_ip:
            print("  IP atual (muda a cada VPN - evite compartilhar): {}".format(url_ip))
    elif url_ip:
        print("  Para outros na rede:  {}".format(url_ip))
        print("  (atenção: este IP muda quando a VPN reconecta)")
    else:
        print("  Para outros na rede:  http://SEU-IP:{}".format(porta))
        print("  (descubra seu IP com 'ipconfig' -> Endereço IPv4)")
    print()
    print("  Ferramentas: acrescente /auditor ou /planner ao endereço")
    print("  (painel administrativo: /admin).")
    print("  Cada usuário/aba tem a sua própria sessão.")
    print("  >>> NÃO FECHE ESTA JANELA enquanto a plataforma estiver em uso <<<")
    print("      (para encerrar, feche esta janela ou pressione Ctrl+C)")
    print("=" * 62)

    atividade.registrar("servidor_iniciado", porta=porta)

    threading.Thread(target=_registrar_dns, daemon=True).start()
    # Descobre logo a rota até o Google (direta ou proxy) para o 1º login ser rápido
    threading.Thread(target=rota_google, daemon=True).start()
    # Backup periódico dos logs (durabilidade), se PASTA_LOGS_BACKUP estiver setada
    if PASTA_LOGS_BACKUP:
        threading.Thread(target=_backup_logs_periodico, daemon=True).start()

    try:
        obter_token()
        print("[inicialização] Token pronto.")
    except Exception as e:
        print("[inicialização] Ainda sem token ({}). Vou tentar em 2º plano.".format(str(e)[:120]))

    threading.Thread(target=renovador_token, daemon=True).start()

    if AQUECER_AGENTE and not MODO_ASSINCRONO:
        threading.Thread(target=aquecer_agente, daemon=True).start()

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado. Até mais!")
