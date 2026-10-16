"""Conversa com a IA pela Bridge (Bradesco IA Gen).

Cuida de: token de serviço (com cache e renovação), montagem do system prompt
(regras + documentação oficial completa), disparo assíncrono no textgenerator
ou workflow, polling do resultado, extração do texto da resposta (JSON/SSE em
formatos variados) e retries do modo síncrono legado.

enviar_para_agente() é o caminho do Tag Auditor; gerar_texto() é a geração
genérica (system prompt próprio, conteúdo com imagem) usada pelo Tag Planner.
"""
import json
import os
import re
import threading
import time

from .config import (
    URL_LOGIN, URL_AGENTE, URL_WORKFLOW_START, URL_WORKFLOW_STATUS, URL_TEXTGEN,
    MODO_ASSINCRONO, ASYNC_VIA_TEXTGEN, MODELO_LLM, OPENAI_API_VERSION,
    REASONING_EFFORT, MAX_TOKENS_RESPOSTA, TEMPERATURA,
    WORKFLOW_CONFIG_CODE, WORKFLOW_FORMATO, WORKFLOW_INPUT_PATH,
    ENVIAR_SYSTEM_PROMPT, SYSTEM_PROMPT_REGRAS, ARQUIVO_SYSTEM_PROMPT,
    ARQUIVO_DOCUMENTACAO, POLL_INTERVALO_SEG, POLL_TIMEOUT_SEG,
    MODO_STREAM, STREAM_NO_PAYLOAD, ASYNC_NO_PAYLOAD,
    IDENTIFICADOR, SENHA, TOKEN_VALIDADE_SEGUNDOS,
    TENTATIVAS_GATEWAY, PAUSA_ENTRE_TENTATIVAS, BASE_DIR,
)
from .rede import post_json, get_json
from .filtro import filtrar_falsos_positivos
from .historico import historico_sessao, anexar_historico

# ---- Token de serviço (login-servico) ----------------------------------------
_trava_token = threading.Lock()
_token_cache = {"valor": None, "obtido_em": 0.0}

_CHAVES_TOKEN = (
    "access_token", "accessToken", "token", "jwt", "id_token",
    "idToken", "bearer", "bearerToken",
)


def _achar_token(obj):
    if isinstance(obj, dict):
        for chave, valor in obj.items():
            if isinstance(valor, str) and chave in _CHAVES_TOKEN and len(valor) > 20:
                return valor
        for valor in obj.values():
            achado = _achar_token(valor)
            if achado:
                return achado
    elif isinstance(obj, list):
        for item in obj:
            achado = _achar_token(item)
            if achado:
                return achado
    return None


def obter_token(forcar=False):
    with _trava_token:
        agora = time.time()
        if (
            not forcar
            and _token_cache["valor"]
            and (agora - _token_cache["obtido_em"]) < TOKEN_VALIDADE_SEGUNDOS
        ):
            return _token_cache["valor"]

        print("[token] Fazendo login para obter novo Bearer token...")
        status, corpo = post_json(URL_LOGIN, {"identificador": IDENTIFICADOR, "senha": SENHA})
        if status < 200 or status >= 300:
            raise RuntimeError("Falha no login ({}): {}".format(status, corpo[:400]))

        token = None
        try:
            token = _achar_token(json.loads(corpo))
        except json.JSONDecodeError:
            texto = corpo.strip().strip('"')
            if len(texto) > 20 and " " not in texto:
                token = texto

        if not token:
            raise RuntimeError(
                "Login respondeu {}, mas não encontrei o token. Resposta: {}".format(status, corpo[:400])
            )

        _token_cache["valor"] = token
        _token_cache["obtido_em"] = agora
        print("[token] OK - token renovado ({}...)".format(token[:24]))
        return token


def renovador_token():
    """Loop de 2º plano: mantém o token sempre fresco (rode numa thread daemon)."""
    while True:
        time.sleep(60)
        try:
            idade = time.time() - _token_cache["obtido_em"]
            if _token_cache["valor"] is None or idade > (TOKEN_VALIDADE_SEGUNDOS - 5 * 60):
                obter_token(forcar=True)
        except Exception as e:
            print("[token] Falha na renovação em 2º plano ({}). Tento de novo em 1 min.".format(
                str(e)[:120]))


# ---- Extração do texto da resposta (JSON/SSE em formatos variados) -----------

def _desescapar(texto):
    if not isinstance(texto, str):
        return texto
    if "\\n" in texto or "\\t" in texto or "\\r" in texto or '\\"' in texto:
        texto = texto.replace("\\r\\n", "\n").replace("\\\\n", "\n").replace("\\n", "\n")
        texto = texto.replace("\\\\t", "\t").replace("\\t", "\t")
        texto = texto.replace("\\r", "\n")
        texto = texto.replace('\\"', '"')
    return texto


def _maior_string(obj):
    melhor = ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        candidatos = list(obj.values())
    elif isinstance(obj, list):
        candidatos = obj
    else:
        return ""
    for item in candidatos:
        s = _maior_string(item)
        if len(s) > len(melhor):
            melhor = s
    return melhor


def _coletar_textos(obj, saida):
    if isinstance(obj, dict):
        for chave in ("text", "texto", "message", "answer", "output", "output_text",
                      "response", "content", "resposta", "markdown", "conteudo", "value"):
            valor = obj.get(chave)
            if isinstance(valor, str) and valor.strip():
                saida.append(valor.strip())
        for valor in obj.values():
            if isinstance(valor, (dict, list)):
                _coletar_textos(valor, saida)
    elif isinstance(obj, list):
        for item in obj:
            _coletar_textos(item, saida)


def extrair_texto_resposta(corpo):
    corpo = (corpo or "").strip()
    if not corpo:
        return "(o agente respondeu vazio)"

    if (corpo.startswith("event:") or corpo.startswith("data:")
            or "\ndata: {" in corpo or "\ndata:{" in corpo):
        pedacos = [l[len("data:"):].strip() for l in corpo.splitlines() if l.strip().startswith("data:")]
        textos = []
        for pedaco in pedacos:
            if not pedaco or pedaco == "[DONE]":
                continue
            try:
                obj = json.loads(pedaco)
            except Exception:
                textos.append(pedaco)
                continue
            if (isinstance(obj, dict)
                    and str(obj.get("status", "")).strip().lower() in _ESTADOS_ANDAMENTO):
                continue  # heartbeat de progresso - não é conteúdo
            _coletar_textos(obj, textos)
        textos = list(dict.fromkeys(t for t in textos if t and not _eh_telemetria(t)))
        if textos:
            return _desescapar("\n\n".join(textos))
        corpo = "\n".join(pedacos) or corpo

    try:
        dados = json.loads(corpo)
    except json.JSONDecodeError:
        return _desescapar(corpo)

    textos = []
    _coletar_textos(dados, textos)
    textos = list(dict.fromkeys(t for t in textos if t and not _eh_telemetria(t)))
    if textos:
        return _desescapar("\n\n".join(textos))

    fallback = _maior_string(dados)
    if fallback.strip() and not _eh_telemetria(fallback):
        return _desescapar(fallback.strip())

    return json.dumps(dados, ensure_ascii=False, indent=2)


_ERROS_HTTP_TRANSITORIOS = (429, 500, 502, 503, 504)

_PADROES_ERRO_INTERNO = (
    "erro 500", "error 500", "http 500", "status 500",
    "temporariamente indispon", "temporarily unavailable",
    "internal server error", "internal error", "erro interno",
    "service unavailable", "serviço indispon", "servico indispon",
    "falha ao executar a auditoria",
)


def _resposta_eh_erro_interno(texto):
    t = (texto or "").strip().lower()
    if not t or len(t) > 400 or "|" in t:
        return False
    return any(p in t for p in _PADROES_ERRO_INTERNO)


_RE_TELEMETRIA = re.compile(
    r"^\s*(elapsed\s*time|tempo\s+(decorrido|de\s+processamento))\b", re.IGNORECASE)


def _eh_telemetria(texto):
    """True para mensagens de progresso do stream ("Elapsed Time: 6.45s")."""
    return bool(texto) and bool(_RE_TELEMETRIA.match(texto))


def _texto_de_data(data):
    if isinstance(data, str):
        return "" if _eh_telemetria(data) else data
    if isinstance(data, dict):
        for chave in ("output_text", "outputtext", "output", "text", "texto",
                      "result", "resultado", "resposta", "answer", "response", "content"):
            valor = data.get(chave)
            if isinstance(valor, str) and valor.strip():
                return valor
        fallback = _maior_string(data)
        return "" if _eh_telemetria(fallback) else fallback
    return ""


def _coletar_por_chaves(valor, chaves, saida):
    if isinstance(valor, dict):
        for chave, item in valor.items():
            if str(chave).lower() in chaves and isinstance(item, str) and item.strip():
                saida.append(item)
            else:
                _coletar_por_chaves(item, chaves, saida)
    elif isinstance(valor, list):
        for item in valor:
            _coletar_por_chaves(item, chaves, saida)


def _texto_de_saida(obj):
    """Extrai o texto gerado de uma resposta de status CONCLUÍDA. O laudo pode
    vir em obj['data'] (stream/OCR), em obj['output_collection'] (workflow) ou
    solto. Busca por prioridade: output_text > texto genérico > actual_value."""
    if not isinstance(obj, dict):
        return ""
    texto = _texto_de_data(obj.get("data"))
    if texto and texto.strip():
        return texto
    for chaves in (("output_text", "outputtext", "generated_text"),
                   ("text", "texto", "content", "result", "resultado",
                    "resposta", "answer"),
                   ("actual_value",)):
        alvos = []
        _coletar_por_chaves(obj, chaves, alvos)
        alvos = [t for t in alvos if t.strip() and not _eh_telemetria(t)]
        if alvos:
            return max(alvos, key=len)
    return ""


def _resultado_stream(corpo):
    """Interpreta o protocolo de status do stream (PROCESSING/COMPLETED/...).

    Devolve ("COMPLETED", texto) | ("ERRO", detalhe) | ("NONE", None)."""
    estado, pedacos, erro = "NONE", [], None
    for linha in (corpo or "").splitlines():
        s = linha.strip()
        if s.startswith("data:"):
            s = s[5:].strip()
        if not s or s == "[DONE]":
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        st = str(obj.get("status", "")).strip().lower()
        classe = _classificar_estado(st)
        if classe == "OK":
            estado = "COMPLETED"
            texto = _texto_de_saida(obj)
            if texto and texto.strip() and texto not in pedacos:
                pedacos.append(texto)
        elif classe == "ERRO":
            dados = obj.get("data")
            msg = dados.get("error") if isinstance(dados, dict) else None
            estado, erro = "ERRO", (msg or obj.get("message") or st)
    if estado == "COMPLETED":
        return "COMPLETED", "\n\n".join(pedacos)
    if estado == "ERRO":
        return "ERRO", erro
    return "NONE", None


_CAMPOS_ID = ("workflow_execution_id", "execution_id", "executionid",
              "workflowexecutionid", "id")
_CAMPOS_EXEC_ID = ("workflow_execution_id", "workflowexecutionid",
                   "execution_id", "executionid")
_CAMPOS_STATUS = ("workflow_execution_status", "execution_status", "status",
                  "state", "situacao", "situation")
_ESTADOS_OK = ("completed", "complete", "success", "succeeded", "finished",
               "done", "concluido", "concluida", "finalizado", "finalizada",
               "sucesso", "ok")
_ESTADOS_ERRO = ("failed", "failure", "error", "errored", "erro", "cancelled",
                 "canceled", "cancelado", "cancelada", "falha", "falhou",
                 "rejected", "rejeitado", "timeout", "timed_out", "expired",
                 "abandoned")
_ESTADOS_ANDAMENTO = ("processing", "pending", "running", "invoked", "queued",
                      "started", "in_progress", "em_andamento", "iniciado",
                      "aguardando", "waiting", "wf_initiated", "wf_running",
                      "wf_processing", "wf_pending", "wf_started")


def _classificar_estado(status):
    """Classifica o status de execução do workflow (formatos variam: 'COMPLETED',
    'WF_COMPLETED_ERROR', 'WF_INITIATED', 'RUNNING'...). Erro tem prioridade
    porque 'WF_COMPLETED_ERROR' contém 'completed' mas é falha.
    Devolve 'OK' | 'ERRO' | 'ANDAMENTO' | 'DESCONHECIDO'."""
    e = (status or "").strip().lower()
    if not e:
        return "DESCONHECIDO"
    if e in _ESTADOS_ERRO:
        return "ERRO"
    if any(m in e for m in ("error", "fail", "falha", "cancel", "timeout",
                            "abandon", "reject", "expired")):
        return "ERRO"
    if e in _ESTADOS_OK:
        return "OK"
    if e in _ESTADOS_ANDAMENTO:
        return "ANDAMENTO"
    if any(m in e for m in ("complet", "success", "sucesso", "finished",
                            "conclu", "finaliz")):
        return "OK"
    if any(m in e for m in ("process", "running", "pending", "initiat", "iniciad",
                            "started", "queued", "invoked", "andamento", "waiting",
                            "aguard")):
        return "ANDAMENTO"
    return "DESCONHECIDO"


def _carregar_json(corpo):
    try:
        return json.loads(corpo)
    except Exception:
        return None


def _achar_campo(obj, nomes):
    if isinstance(obj, dict):
        for chave, valor in obj.items():
            if str(chave).lower() in nomes and isinstance(valor, (str, int)) and str(valor).strip():
                return str(valor)
        for valor in obj.values():
            achado = _achar_campo(valor, nomes)
            if achado:
                return achado
    elif isinstance(obj, list):
        for item in obj:
            achado = _achar_campo(item, nomes)
            if achado:
                return achado
    return None


# ---- Uso de tokens (para o log de atividade / painel admin) ------------------
# A resposta da Bridge PODE trazer o bloco "usage" do Azure OpenAI
# (prompt_tokens/completion_tokens). Quando trouxer, usamos os numeros reais
# ("fonte": "api"); quando nao, estimamos por caracteres/4 ("fonte":
# "estimativa") — a estimativa inclui o system prompt (regras + documentacao),
# que e o maior custo de entrada de cada auditoria.

_CHAVES_TOKENS_ENTRADA = ("prompt_tokens", "prompttokens", "input_tokens",
                          "inputtokens", "tokens_entrada")
_CHAVES_TOKENS_SAIDA = ("completion_tokens", "completiontokens", "output_tokens",
                        "outputtokens", "tokens_saida")
_CHAVES_TOKENS_TOTAL = ("total_tokens", "totaltokens", "tokens_total")


def _como_inteiro(valor):
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)) and valor >= 0:
        return int(valor)
    if isinstance(valor, str) and valor.strip().isdigit():
        return int(valor.strip())
    return None


def _achar_uso(obj):
    """Procura contagem de tokens (usage) em qualquer nivel da resposta.
    Devolve {"entrada", "saida", "total", "fonte": "api"} ou None."""
    achado = {}

    def visita(v):
        if isinstance(v, dict):
            for chave, item in v.items():
                nome = str(chave).lower().replace("-", "_")
                numero = _como_inteiro(item)
                if numero is not None:
                    if nome in _CHAVES_TOKENS_ENTRADA:
                        achado.setdefault("entrada", numero)
                    elif nome in _CHAVES_TOKENS_SAIDA:
                        achado.setdefault("saida", numero)
                    elif nome in _CHAVES_TOKENS_TOTAL:
                        achado.setdefault("total", numero)
                elif isinstance(item, (dict, list)):
                    visita(item)
        elif isinstance(v, list):
            for item in v:
                visita(item)

    visita(obj)
    if not achado:
        return None
    achado.setdefault("entrada", 0)
    achado.setdefault("saida", 0)
    achado.setdefault("total", achado["entrada"] + achado["saida"])
    achado["fonte"] = "api"
    return achado


def _uso_estimado(chars_entrada, texto_saida):
    """Estimativa de tokens por caracteres (~4 chars/token em pt-BR)."""
    entrada = max(0, int(chars_entrada)) // 4
    saida = len(texto_saida or "") // 4
    return {"entrada": entrada, "saida": saida, "total": entrada + saida,
            "fonte": "estimativa"}


def _parece_ack_async(obj):
    """Devolve o execution_id SOMENTE se a resposta for um 'recibo' do modo
    assíncrono: tem o id da execução e NENHUM texto de resultado. Uma resposta
    completa que por acaso carregue um id de execução não é desviada."""
    exec_id = _achar_campo(obj, _CAMPOS_EXEC_ID)
    if not exec_id:
        return None
    textos = []
    _coletar_textos(obj, textos)
    if any(t and t.strip() and not _eh_telemetria(t) for t in textos):
        return None
    return exec_id


# ---- System prompt (regras + documentação oficial completa) ------------------
_cache_system_prompt = {"texto": None}


def _obter_system_prompt():
    """Regras do auditor + documentacao oficial COMPLETA, enviadas ao modelo.
    Usa SYSTEM_PROMPT_REGRAS se preenchido; senão monta (1x):
      - as regras de ARQUIVO_SYSTEM_PROMPT (sem doc embutida), e
      - a documentacao INTEIRA de ARQUIVO_DOCUMENTACAO (fonte unica de verdade).
    Assim a documentacao_base_bradesco.md e sempre usada na integra, direto do
    arquivo-fonte, sem depender de copia manual que pode ficar desatualizada."""
    if SYSTEM_PROMPT_REGRAS.strip():
        return SYSTEM_PROMPT_REGRAS
    if _cache_system_prompt["texto"] is None:
        regras = ""
        try:
            with open(os.path.join(BASE_DIR, ARQUIVO_SYSTEM_PROMPT), "r", encoding="utf-8") as f:
                regras = f.read().strip()
            print("[async] Regras do auditor carregadas de {} ({} caracteres).".format(
                ARQUIVO_SYSTEM_PROMPT, len(regras)))
        except Exception as e:
            print("[async] Não consegui ler {} ({}); sigo sem as regras.".format(
                ARQUIVO_SYSTEM_PROMPT, str(e)[:100]))

        doc = ""
        if ARQUIVO_DOCUMENTACAO:
            try:
                with open(os.path.join(BASE_DIR, ARQUIVO_DOCUMENTACAO), "r", encoding="utf-8") as f:
                    doc = f.read().strip()
                print("[async] Documentação completa carregada de {} ({} caracteres).".format(
                    ARQUIVO_DOCUMENTACAO, len(doc)))
            except Exception as e:
                print("[async] Não consegui ler {} ({}); sigo sem a documentação.".format(
                    ARQUIVO_DOCUMENTACAO, str(e)[:100]))

        # Evita duplicar a doc caso o arquivo de regras ja a traga embutida.
        ja_tem_doc = ("DOCUMENTACAO OFICIAL" in regras
                      or "Documentação Base — Padrão de Tagueamento" in regras)
        if doc and not ja_tem_doc:
            texto = (regras + "\n\n---\n\n"
                     "# DOCUMENTACAO OFICIAL (fonte unica de verdade)\n\n" + doc).strip()
        else:
            texto = regras or doc
        _cache_system_prompt["texto"] = texto
    return _cache_system_prompt["texto"]


# ---- Disparo assíncrono (textgenerator / workflow) ---------------------------

def _postar_disparo(url, payload, rotulo):
    """POST que INICIA a execução (textgenerator ou workflow), com retry.

    O gateway corta a conexão em 30s. Mesmo no modo assíncrono, o ACK (a
    resposta com o workflow_execution_id) passa por essa régua: se o serviço
    não responder a tempo vem um "504 Gateway Time-out" em HTML e a mensagem
    do usuário morria de primeira - TENTATIVAS_GATEWAY só valia para o modo
    síncrono legado. Aqui 429/5xx e quedas de conexão são retentados.

    ATENÇÃO: um 504 no disparo NÃO prova que ele foi recusado - o serviço pode
    ter enfileirado a execução e só não ter respondido a tempo. A retentativa
    pode, então, criar uma execução órfã (ninguém consulta o resultado, mas ela
    consome tokens). É o preço de não perder a mensagem: estas rotas não têm
    idempotência (nenhuma chave de requisição para reaproveitar).

    Devolve (status, corpo, tentativas). Quem chama monta a mensagem de erro:
    status fora de 2xx aqui significa "não deu, nem com as retentativas".
    """
    status, corpo = None, ""
    for tentativa in range(1, TENTATIVAS_GATEWAY + 1):
        try:
            token = obter_token()
            status, corpo = post_json(url, payload, {"Authorization": "Bearer " + token})
            if status in (401, 403):
                token = obter_token(forcar=True)
                status, corpo = post_json(url, payload, {"Authorization": "Bearer " + token})
        except RuntimeError as e:
            # Rede/VPN fora, ou o login-servico falhou: não há status HTTP.
            status, corpo = None, str(e)
            print("[async] {}: {} (tentativa {}/{}).".format(
                rotulo, str(e)[:140], tentativa, TENTATIVAS_GATEWAY))

        if status is not None:
            if 200 <= status < 300:
                if tentativa > 1:
                    print("[async] {}: disparo aceito na tentativa {}.".format(rotulo, tentativa))
                return status, corpo, tentativa
            if status not in _ERROS_HTTP_TRANSITORIOS:
                return status, corpo, tentativa   # erro definitivo: retentar não ajuda

        if tentativa < TENTATIVAS_GATEWAY:
            print("[async] {}: {} - tentando de novo em {}s ({}/{}).".format(
                rotulo,
                "HTTP {} (transitório)".format(status) if status is not None else "sem resposta",
                PAUSA_ENTRE_TENTATIVAS, tentativa, TENTATIVAS_GATEWAY))
            time.sleep(PAUSA_ENTRE_TENTATIVAS)

    if status is None:
        raise RuntimeError(
            "Não consegui conectar para {} após {} tentativas. Verifique a "
            "rede/VPN. Detalhe: {}".format(rotulo, TENTATIVAS_GATEWAY, corpo[:200]))
    return status, corpo, TENTATIVAS_GATEWAY


def _montar_props_workflow(mensagem):
    regras = _obter_system_prompt() if ENVIAR_SYSTEM_PROMPT else ""
    if WORKFLOW_FORMATO == "messages":
        msgs = []
        if regras:
            msgs.append({"role": "system", "content": regras})
        msgs.append({"role": "user", "content": mensagem})
        return [{"full_path": "messages", "actual_value": msgs, "is_valid": True}]
    props = [{"full_path": WORKFLOW_INPUT_PATH, "actual_value": mensagem, "is_valid": True}]
    if regras:
        props.append({"full_path": "system_prompt", "actual_value": regras, "is_valid": True})
    return props


def _iniciar_workflow(mensagem):
    props = _montar_props_workflow(mensagem)
    payload = {
        "workflow_configuration_code": WORKFLOW_CONFIG_CODE,
        "input_collection": {"input_properties": [
            {"workflow_step_order": 0, "workflow_step_properties": props}
        ]},
    }
    status, corpo, tentativas = _postar_disparo(URL_WORKFLOW_START, payload, "start-workflow")
    if status < 200 or status >= 300:
        raise RuntimeError("Falha ao iniciar o workflow (HTTP {} em {} tentativa(s)): {}".format(
            status, tentativas, corpo[:400]))
    exec_id = _achar_campo(_carregar_json(corpo), _CAMPOS_ID)
    if not exec_id:
        raise RuntimeError(
            "Workflow iniciado, mas não encontrei o workflow_execution_id na resposta: {}".format(corpo[:300]))
    return exec_id


def _aguardar_workflow(exec_id):
    """Aguarda a execução terminar. Devolve (texto, uso_de_tokens_ou_None)."""
    url = URL_WORKFLOW_STATUS + str(exec_id)
    inicio = time.time()
    ultimo, primeiro = "", True
    falhas_seguidas, ultima_falha = 0, None
    while True:
        try:
            token = obter_token()
            status, corpo = get_json(url, {"Authorization": "Bearer " + token})
            if status in (401, 403):
                token = obter_token(forcar=True)
                status, corpo = get_json(url, {"Authorization": "Bearer " + token})
        except RuntimeError as e:
            status, corpo = None, ""
            falhas_seguidas += 1
            ultima_falha = "conexão ({})".format(str(e)[:80])

        if status is not None and 200 <= status < 300:
            falhas_seguidas, ultima_falha = 0, None
            if primeiro:
                print("[async] 1ª resposta de status (bruta): {}".format(corpo[:300].replace("\n", " ")))
                primeiro = False
            obj = _carregar_json(corpo)
            estado = (_achar_campo(obj, _CAMPOS_STATUS) or "").strip().lower()
            classe = _classificar_estado(estado)
            if estado and estado != ultimo:
                print("[async] status: {}".format(estado))
                ultimo = estado
            if classe in ("ERRO", "OK"):
                print("[async] resposta final (bruta): {}".format(corpo[:600].replace("\n", " ")))
            if classe == "ERRO":
                detalhe = _achar_campo(obj, ("error", "erro", "message", "detail",
                                             "detailed_info")) or corpo[:400]
                raise RuntimeError("O workflow terminou com erro ({}): {}".format(estado, detalhe))
            if classe == "OK":
                texto = _texto_de_saida(obj)
                if not (texto and texto.strip()):
                    texto = extrair_texto_resposta(corpo)
                if texto and texto.strip() and not _eh_telemetria(texto):
                    return texto, _achar_uso(obj)
                raise RuntimeError(
                    "O workflow {} concluiu ({}), mas não encontrei o texto do "
                    "resultado na resposta: {}".format(exec_id, estado, corpo[:800]))
        elif status is not None:
            if status not in _ERROS_HTTP_TRANSITORIOS:
                raise RuntimeError("Erro ao consultar o status do workflow (HTTP {}): {}".format(status, corpo[:400]))
            falhas_seguidas += 1
            ultima_falha = "HTTP {}".format(status)

        if time.time() - inicio > POLL_TIMEOUT_SEG:
            detalhe = ("" if not falhas_seguidas else
                       " As últimas {} consultas falharam ({}).".format(falhas_seguidas, ultima_falha))
            raise RuntimeError(
                "Tempo esgotado ({}s) aguardando o workflow {}. Último status: '{}'.{}".format(
                    POLL_TIMEOUT_SEG, exec_id, ultimo or "desconhecido", detalhe))
        time.sleep(POLL_INTERVALO_SEG)


def _iniciar_textgen(mensagens, system_prompt=None, modelo=None, max_tokens=None,
                     reasoning_effort=None, rotulo="textgenerator"):
    """Dispara a geração ASSÍNCRONA direto na API do textgenerator (Exemplo 4
    da doc): responde na hora com o workflow_execution_id, sem esperar o
    processamento - é a rota oficial do app (ver ASYNC_VIA_TEXTGEN).

    Isso tira a GERAÇÃO (que leva minutos com o raciocínio ligado) de baixo do
    corte de 30s do gateway, mas não o ACK: já aconteceu de o próprio disparo
    levar 504 quando o serviço não devolveu o execution_id em 30s. Por isso o
    POST vai pelo _postar_disparo, que retenta.

    'mensagens' é a lista [{role, content}] com o histórico + a mensagem atual.
    Sem os parâmetros opcionais vale a configuração do auditor (regras +
    documentação, MODELO_LLM...); outras ferramentas passam os seus."""
    if system_prompt is None:
        regras = _obter_system_prompt() if ENVIAR_SYSTEM_PROMPT else ""
    else:
        regras = system_prompt
    msgs = []
    if regras:
        msgs.append({"role": "system", "content": regras})
    msgs.extend(mensagens)
    payload = {
        "async_mode": True,
        "stream": False,
        "model": modelo or MODELO_LLM,
        "openai_api_version": OPENAI_API_VERSION,
        "messages": msgs,
        "max_tokens": max_tokens or MAX_TOKENS_RESPOSTA,
    }
    esforco = REASONING_EFFORT if reasoning_effort is None else reasoning_effort
    if esforco:
        payload["reasoning_effort"] = esforco
    if TEMPERATURA is not None:
        payload["temperature"] = TEMPERATURA
    status, corpo, tentativas = _postar_disparo(URL_TEXTGEN, payload, rotulo)
    if status < 200 or status >= 300:
        raise RuntimeError(
            "Falha ao iniciar a geração assíncrona (HTTP {} em {} tentativa(s)): {}".format(
                status, tentativas, corpo[:400]))
    exec_id = _achar_campo(_carregar_json(corpo), _CAMPOS_EXEC_ID)
    if not exec_id:
        raise RuntimeError(
            "Geração iniciada, mas não encontrei o workflow_execution_id na "
            "resposta: {}".format(corpo[:300]))
    return exec_id


def _auditar_async(mensagem, session_id, historico_cliente=None):
    # Se o cliente enviou o histórico (localStorage), ele é a fonte de verdade e
    # o servidor fica sem estado. Sem histórico do cliente, usa o cache local.
    usar_cache = historico_cliente is None
    historico = historico_sessao(session_id) if usar_cache else historico_cliente
    if ASYNC_VIA_TEXTGEN:
        mensagens = list(historico) + [{"role": "user", "content": mensagem}]
        exec_id = _iniciar_textgen(mensagens)
        print("[async] Geração assíncrona iniciada no textgenerator (execution_id={}, "
              "{} msg de histórico). Aguardando conclusão...".format(exec_id, len(historico)))
    else:
        mensagens = [{"role": "user", "content": mensagem}]
        exec_id = _iniciar_workflow(mensagem)
        print("[async] workflow iniciado (execution_id={}). Aguardando conclusão...".format(exec_id))
    bruto, uso = _aguardar_workflow(exec_id)
    if uso is None:
        regras = _obter_system_prompt() if ENVIAR_SYSTEM_PROMPT else ""
        chars_entrada = len(regras) + sum(
            len(str(m.get("content") or "")) for m in mensagens)
        uso = _uso_estimado(chars_entrada, bruto)
    print("[tokens] entrada={} saida={} total={} ({})".format(
        uso["entrada"], uso["saida"], uso["total"], uso["fonte"]))
    resposta = filtrar_falsos_positivos(bruto)
    if usar_cache:
        # Mantém o cache do servidor só quando o cliente NÃO controla o histórico.
        anexar_historico(session_id, "user", mensagem)
        anexar_historico(session_id, "assistant", resposta)
    return resposta, uso


def _chars_de_texto(conteudo):
    """Caracteres de TEXTO de um content (string ou lista de partes OpenAI).
    Partes de imagem não entram: o custo delas é estimado à parte."""
    if isinstance(conteudo, str):
        return len(conteudo)
    total = 0
    if isinstance(conteudo, list):
        for parte in conteudo:
            if isinstance(parte, dict) and parte.get("type") == "text":
                total += len(str(parte.get("text") or ""))
    return total


def gerar_texto(mensagens, system_prompt, modelo=None, max_tokens=None,
                reasoning_effort=None, rotulo="textgenerator",
                tokens_extra_estimados=0):
    """Geração assíncrona no textgenerator com um system prompt PRÓPRIO.

    Para as ferramentas da plataforma que não são o auditor (ex.: Tag
    Planner): mesmo caminho oficial (disparo assíncrono + polling da Workflow
    Response), mas sem as regras/documentação do auditor e SEM o filtro de
    falsos positivos, que só faz sentido na auditoria.

    'mensagens': [{role, content}] com o histórico + a mensagem atual. O content
    pode ser texto ou lista de partes no formato OpenAI (text / image_url).
    'tokens_extra_estimados': custo estimado do que não é texto (imagens), somado
    à entrada só quando a API não devolver o uso real.
    Devolve (texto, uso_de_tokens)."""
    exec_id = _iniciar_textgen(mensagens, system_prompt=system_prompt or "",
                               modelo=modelo, max_tokens=max_tokens,
                               reasoning_effort=reasoning_effort, rotulo=rotulo)
    print("[async] {}: geração iniciada (execution_id={}, {} mensagem(ns)). "
          "Aguardando conclusão...".format(rotulo, exec_id, len(mensagens)))
    texto, uso = _aguardar_workflow(exec_id)
    if uso is None:
        chars = len(system_prompt or "") + sum(
            _chars_de_texto(m.get("content")) for m in mensagens)
        uso = _uso_estimado(chars, texto)
        extra = max(0, int(tokens_extra_estimados or 0))
        uso["entrada"] += extra
        uso["total"] += extra
    print("[tokens] {}: entrada={} saida={} total={} ({})".format(
        rotulo, uso["entrada"], uso["saida"], uso["total"], uso["fonte"]))
    return texto, uso


# ---- Modo síncrono legado (agente com retries) -------------------------------

def _chamar_agente(payload, com_stream=False):
    token = obter_token()
    headers = {"Authorization": "Bearer " + token}
    if com_stream:
        headers["Accept"] = "text/event-stream, application/json"
    status, corpo = post_json(URL_AGENTE, payload, headers)
    if status in (401, 403):
        print("[agente] {} recebido - renovando token e tentando novamente...".format(status))
        token = obter_token(forcar=True)
        headers["Authorization"] = "Bearer " + token
        status, corpo = post_json(URL_AGENTE, payload, headers)
    return status, corpo


def enviar_para_agente(mensagem, session_id, historico=None):
    """Envia a mensagem para a IA. Devolve (resposta, uso_de_tokens)."""
    if MODO_ASSINCRONO:
        return _auditar_async(mensagem, session_id, historico)

    payload = {
        "messages": [{"type": "text", "content": {"text": mensagem}}],
        "session_id": session_id,
        "metadata": {},
    }
    usar_stream = MODO_STREAM
    if usar_stream and STREAM_NO_PAYLOAD:
        payload["stream"] = True
    if ASYNC_NO_PAYLOAD:
        payload["async"] = True

    status, corpo, erro_interno = None, "", None
    tentativa = 0
    while tentativa < TENTATIVAS_GATEWAY:
        tentativa += 1
        if tentativa > 1:
            try:
                obter_token(forcar=True)
                print("[agente] Token renovado antes da tentativa {}.".format(tentativa))
            except Exception as e:
                print("[agente] Não consegui renovar o token agora ({}); "
                      "sigo com o atual.".format(str(e)[:120]))

        try:
            status, corpo = _chamar_agente(payload, usar_stream)
        except RuntimeError as e:
            status, corpo = None, ""
            print("[agente] Falha de conexão na tentativa {}/{}: {}".format(
                tentativa, TENTATIVAS_GATEWAY, str(e)[:160]))
            if tentativa >= TENTATIVAS_GATEWAY:
                raise
            time.sleep(PAUSA_ENTRE_TENTATIVAS)
            continue

        print("[agente] HTTP {} (tentativa {}/{}{}) | resposta bruta (inicio): {}".format(
            status, tentativa, TENTATIVAS_GATEWAY,
            " stream" if usar_stream else "", corpo[:400].replace("\n", " ")))

        if 200 <= status < 300:
            erro_interno = None
            exec_id = _parece_ack_async(_carregar_json(corpo))
            if exec_id:
                print("[agente] Execução assíncrona detectada (workflow_execution_id={}). "
                      "Aguardando o resultado na Workflow Response...".format(exec_id))
                texto_w, uso_w = _aguardar_workflow(exec_id)
                return texto_w, (uso_w or _uso_estimado(len(mensagem), texto_w))

            estado_s, texto_s = _resultado_stream(corpo)
            if estado_s == "COMPLETED" and texto_s and texto_s.strip():
                return texto_s, (_achar_uso(_carregar_json(corpo))
                                 or _uso_estimado(len(mensagem), texto_s))
            if estado_s == "ERRO":
                erro_interno = texto_s or "erro no stream"
                if tentativa < TENTATIVAS_GATEWAY:
                    print("[agente] Stream retornou erro (\"{}\") - tentando de novo em {}s...".format(
                        (texto_s or "")[:90].replace("\n", " "), PAUSA_ENTRE_TENTATIVAS))
                    time.sleep(PAUSA_ENTRE_TENTATIVAS)
                continue

            texto = extrair_texto_resposta(corpo)
            if not _resposta_eh_erro_interno(texto):
                return texto, (_achar_uso(_carregar_json(corpo))
                               or _uso_estimado(len(mensagem), texto))
            erro_interno = texto
            if tentativa < TENTATIVAS_GATEWAY:
                print("[agente] Serviço interno do agente falhou (\"{}\") - "
                      "tentando de novo em {}s...".format(
                          texto[:90].replace("\n", " "), PAUSA_ENTRE_TENTATIVAS))
                time.sleep(PAUSA_ENTRE_TENTATIVAS)
            continue

        # Falhou com o stream ligado? A causa mais provável é a API não aceitar
        # o stream/Accept nesta rota. Desliga TUDO de stream e refaz a MESMA
        # tentativa em modo clássico (não consome tentativa; acontece 1 vez só).
        if usar_stream or "stream" in payload or "async" in payload:
            usar_stream = False
            payload.pop("stream", None)
            payload.pop("async", None)
            print("[agente] HTTP {} com stream/async ligado - reenviando em "
                  "modo clássico (sem stream/async)...".format(status))
            tentativa -= 1
            continue

        if status in _ERROS_HTTP_TRANSITORIOS and tentativa < TENTATIVAS_GATEWAY:
            print("[agente] HTTP {} (transitório) - tentando de novo em {}s...".format(
                status, PAUSA_ENTRE_TENTATIVAS))
            time.sleep(PAUSA_ENTRE_TENTATIVAS)
            continue
        break

    if status is None:
        raise RuntimeError(
            "Não consegui conectar ao agente após {} tentativas. "
            "Verifique a rede/VPN e tente de novo.".format(tentativa))
    if status < 200 or status >= 300:
        if status in _ERROS_HTTP_TRANSITORIOS:
            raise RuntimeError(
                "O serviço do agente falhou (HTTP {}) mesmo após {} tentativas. "
                "Detalhe: {} - Aguarde alguns segundos e envie de novo - ou "
                "divida o JSON em partes menores.".format(
                    status, tentativa, corpo[:200].replace("\n", " ") or "(sem corpo)"))
        raise RuntimeError("O agente retornou erro HTTP {}: {}".format(status, corpo[:600]))
    if erro_interno is not None:
        raise RuntimeError(
            "O serviço interno do agente falhou mesmo após {} tentativas "
            "(\"{}\"). Costuma ser instabilidade passageira do Bridge: aguarde "
            "1-2 minutos e reenvie o mesmo arquivo.".format(
                tentativa, erro_interno[:160]))
    texto = extrair_texto_resposta(corpo)
    return texto, (_achar_uso(_carregar_json(corpo))
                   or _uso_estimado(len(mensagem), texto))


def aquecer_agente():
    try:
        sessao = "999{:09d}".format(int(time.time()) % 10 ** 9)
        print("[aquecimento] Acordando o agente (pode levar até ~1 min)...")
        enviar_para_agente("oi", sessao)
        print("[aquecimento] Agente acordado - pronto para auditar.")
    except Exception as e:
        print("[aquecimento] Agente ainda lento ({}). "
              "Se a 1ª auditoria falhar, o retry automático tenta de novo.".format(str(e)[:140]))
