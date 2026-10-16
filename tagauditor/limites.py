"""Limites técnicos do GA4 — contagem feita em código, não pela IA.

Esta regra vivia na documentação (seção 7, "Limites técnicos do GA4") e
dependia de o modelo CONTAR caracteres — algo em que LLM erra com frequência
(conta o valor com as aspas, inclui o prefixo do bloco, "estima" o tamanho ou
simplesmente não verifica). Agora a contagem é feita aqui, em Python, direto no
JSON do plano: determinística, sempre igual, e sem gastar contexto do modelo.

O que é verificado (valores em config.py):

    nome do evento               <= 40   erro    evento não é registrado
    nome do parâmetro            <= 40   erro    parâmetro descartado
    valor do parâmetro (string)  <= 100  erro    valor truncado
    parâmetros por evento        <= 25   erro    excedentes ignorados
    nome de user property        <= 24   alerta
    valor de user property       <= 36   alerta
    total de user properties     <= 25   alerta  (nomes distintos no plano todo)

Blocos `user` e `up` são escopo USER (limites de user property). `user_id` fica
de fora deles: é o identificador reservado do GA4, não uma user property — vale
o limite comum de valor.

NÃO é auditado aqui:
    - valores dinâmicos (`{{...}}` / `${...}`): o tamanho real só existe em
      runtime, então contar o placeholder daria falso positivo;
    - "eventos por sessão" (500): depende da execução, não do plano.

Fluxo (servidor.py): a resposta da IA passa por `filtrar_falsos_positivos` e
depois por `aplicar_limites_ga4`, que remove qualquer apontamento de tamanho
que o modelo tenha inventado e anexa a tabela contada aqui.
"""
import json
import re
import unicodedata

from .config import (
    VALIDAR_LIMITES_GA4, LIMITE_NOME_EVENTO, LIMITE_NOME_PARAMETRO,
    LIMITE_VALOR_PARAMETRO, LIMITE_PARAMS_POR_EVENTO,
    LIMITE_NOME_USER_PROPERTY, LIMITE_VALOR_USER_PROPERTY,
    LIMITE_USER_PROPERTIES,
)
from .filtro import normalizar, percorrer_tabelas

ERRO = "❌"      # viola limite crítico do GA4 (dado perdido)
ALERTA = "⚠️"  # fora do limite recomendado (funciona, mas não envia tudo)

# Profundidade máxima de recursão no JSON (plano real tem 3-4 níveis; o teto só
# protege de JSON malformado vindo do Confluence).
_MAX_PROFUNDIDADE = 15

# Chaves que são "embalagem" do plano exportado, não parte do payload: não
# entram no rótulo do evento exibido na tabela.
_CHAVES_EMBALAGEM = frozenset({
    "sections", "secoes", "section", "secao", "code_json", "codejson",
    "items", "itens", "blocos", "eventos", "events", "data", "conteudo",
})

# Blocos de escopo USER (documentação, seção 4.2): valem os limites de user
# property, mais apertados que os de parâmetro de evento.
_BLOCOS_USER = frozenset({"user", "up", "user_properties", "userproperties"})

# Identificador reservado do GA4 — não conta como user property.
_NAO_E_USER_PROPERTY = frozenset({"user_id", "userid"})

# Parâmetros de URL/página: estourar 100 caracteres neles derruba a atribuição
# de campanha (utm_*), conforme a nota de WebView da documentação.
_PARAMS_URL = frozenset({"page_location", "location", "url_webview", "path"})

_RE_DINAMICO = re.compile(r"\{\{[^}]*\}\}|\$\{[^}]*\}")
_RE_ESPACO = re.compile(r"\s+")

# Apontamento de tamanho feito pela IA: agora é ruído (a contagem oficial é a
# desta tabela). Casa "excede 100 caracteres", "limite de 40 caracteres",
# "valor truncado por exceder o limite", "acima do limite técnico"... e NÃO
# casa "sem caracteres especiais" (regra de separador, que continua valendo).
_RE_APONTAMENTO_LIMITE = re.compile(
    r"\b\d{1,4}\s*caracter"
    r"|caracter\w*[^|]{0,40}\b(limite|excede|ultrapass|estoura|truncad|maximo)"
    r"|\b(limite|excede|ultrapass|estoura|truncad)\w*[^|]{0,40}caracter"
    r"|limite (tecnico|do ga4|de tamanho|de caracter)"
    r"|(excede|ultrapass|estoura)\w*[^|]{0,30}(limite|tamanho)"
)


def _norm_chave(chave):
    texto = unicodedata.normalize("NFKD", str(chave)).encode("ascii", "ignore").decode("ascii")
    return texto.strip().lower()


def _dinamico(valor):
    """True se o valor tem placeholder de runtime ({{x}} / ${x})."""
    return bool(_RE_DINAMICO.search(valor))


def _juntar(rotulo, parte):
    parte = _RE_ESPACO.sub(" ", str(parte)).strip()
    if not parte:
        return rotulo
    return "{} > {}".format(rotulo, parte) if rotulo else parte


# ---- 1. Achar os eventos dentro do JSON do plano -----------------------------

def _coletar_eventos(objeto, rotulo, saida, prof=0):
    """Junta (rótulo, payload) de cada push do dataLayer encontrado no plano.

    Payload = dict com `event` string (o push do dataLayer) ou o conteúdo de uma
    chave `code_json` (formato exportado do Confluence).
    """
    if prof > _MAX_PROFUNDIDADE:
        return
    if isinstance(objeto, dict):
        if isinstance(objeto.get("event"), str):
            saida.append((rotulo, objeto))
            return
        for chave, item in objeto.items():
            nome = _norm_chave(chave)
            if nome in _CHAVES_EMBALAGEM:
                if nome in ("code_json", "codejson") and isinstance(item, dict):
                    saida.append((rotulo, item))
                else:
                    _coletar_eventos(item, rotulo, saida, prof + 1)
                continue
            _coletar_eventos(item, _juntar(rotulo, chave), saida, prof + 1)
    elif isinstance(objeto, list):
        for i, item in enumerate(objeto, 1):
            marca = ""
            if isinstance(item, dict):
                for chave in ("id", "index", "ordem", "numero"):
                    if isinstance(item.get(chave), (int, str)) and str(item[chave]).strip():
                        marca = str(item[chave]).strip()
                        break
            _coletar_eventos(item, _juntar(rotulo, "#{}".format(marca or i)),
                             saida, prof + 1)


# ---- 2. Achatar o payload em (caminho, nome, valor, escopo) ------------------

def _achatar(valor, caminho, nome, escopo, saida, prof=0):
    if prof > _MAX_PROFUNDIDADE:
        return
    if isinstance(valor, dict):
        for chave, item in valor.items():
            texto = str(chave)
            if texto.startswith("_"):
                continue        # metadado da ferramenta (ex.: _is_native)
            filho = escopo or ("user" if _norm_chave(texto) in _BLOCOS_USER else "")
            _achatar(item, "{}.{}".format(caminho, texto) if caminho else texto,
                     texto, filho, saida, prof + 1)
    elif isinstance(valor, list):
        for i, item in enumerate(valor):
            _achatar(item, "{}[{}]".format(caminho, i), nome, escopo, saida, prof + 1)
    else:
        saida.append((caminho, nome, valor, escopo))


# ---- 3. Conferir os limites --------------------------------------------------

def _achado(evento, caminho, parametro, valor, problema, correcao):
    return {
        "evento": evento,
        "localizacao": caminho if "." in caminho else "raiz",
        "parametro": parametro,
        "valor": valor,
        "problema": problema,
        "correcao": correcao,
    }


def _excesso(tamanho, limite):
    return "excesso: {} caractere(s)".format(tamanho - limite)


def _conferir_evento(rotulo, payload, achados, nomes_up):
    nome_evento = payload.get("event")
    literal = isinstance(nome_evento, str) and not _dinamico(nome_evento)
    evento = _juntar(rotulo, nome_evento if literal else "evento")

    if literal and len(nome_evento) > LIMITE_NOME_EVENTO:
        achados.append(_achado(
            evento, "event", "event", nome_evento,
            "{} Nome do evento com {} caracteres (limite: {})".format(
                ERRO, len(nome_evento), LIMITE_NOME_EVENTO),
            "Encurtar o nome do evento ({}) — acima do limite o evento não é "
            "registrado no GA4".format(_excesso(len(nome_evento), LIMITE_NOME_EVENTO))))

    parametros = []
    _achatar(payload, "", "", "", parametros)
    do_evento = set()

    for caminho, nome, valor, escopo in parametros:
        if caminho == "event":
            continue                    # nome do evento: já conferido acima
        eh_up = escopo == "user" and _norm_chave(nome) not in _NAO_E_USER_PROPERTY
        if eh_up:
            nomes_up.add(_norm_chave(nome))
        else:
            do_evento.add(_norm_chave(nome))

        limite_nome = LIMITE_NOME_USER_PROPERTY if eh_up else LIMITE_NOME_PARAMETRO
        if len(nome) > limite_nome:
            achados.append(_achado(
                evento, caminho, nome, valor,
                "{} Nome {} com {} caracteres (limite: {})".format(
                    ALERTA if eh_up else ERRO,
                    "de user property" if eh_up else "do parâmetro",
                    len(nome), limite_nome),
                "Renomear o parâmetro ({}) — acima do limite o GA4 {}".format(
                    _excesso(len(nome), limite_nome),
                    "não envia a user property" if eh_up else "descarta o parâmetro")))

        if not isinstance(valor, str) or _dinamico(valor):
            continue                    # número/booleano ou valor de runtime
        limite_valor = LIMITE_VALOR_USER_PROPERTY if eh_up else LIMITE_VALOR_PARAMETRO
        if len(valor) > limite_valor:
            correcao = "Reduzir para no máximo {} caracteres ({}) — o GA4 {}".format(
                limite_valor, _excesso(len(valor), limite_valor),
                "não envia a user property" if eh_up else "trunca o restante do valor")
            if not eh_up and _norm_chave(nome) in _PARAMS_URL:
                correcao += "; com o corte a atribuição de campanha (utm_*) se perde"
            achados.append(_achado(
                evento, caminho, nome, valor,
                "{} Valor {} com {} caracteres (limite: {})".format(
                    ALERTA if eh_up else ERRO,
                    "de user property" if eh_up else "do parâmetro",
                    len(valor), limite_valor),
                correcao))

    if len(do_evento) > LIMITE_PARAMS_POR_EVENTO:
        achados.append(_achado(
            evento, "raiz", "(evento inteiro)", "{} parâmetros".format(len(do_evento)),
            "{} {} parâmetros no evento (limite: {})".format(
                ERRO, len(do_evento), LIMITE_PARAMS_POR_EVENTO),
            "Remover ou agrupar {} parâmetro(s) — os excedentes são ignorados "
            "pelo GA4".format(len(do_evento) - LIMITE_PARAMS_POR_EVENTO)))


def validar_limites(objeto):
    """Confere os limites do GA4 no plano já convertido em objeto Python.

    Devolve (achados, total_de_eventos_encontrados).
    """
    eventos = []
    _coletar_eventos(objeto, "", eventos)
    achados, nomes_up = [], set()
    for rotulo, payload in eventos:
        _conferir_evento(rotulo, payload, achados, nomes_up)
    if len(nomes_up) > LIMITE_USER_PROPERTIES:
        achados.append(_achado(
            "plano inteiro", "raiz", "(user properties)",
            "{} nomes distintos".format(len(nomes_up)),
            "{} {} user properties distintas no plano (limite: {} por propriedade "
            "GA4)".format(ALERTA, len(nomes_up), LIMITE_USER_PROPERTIES),
            "Reaproveitar user properties existentes ou mover parte para escopo "
            "de evento — acima do limite a propriedade não envia as excedentes"))
    return achados, len(eventos)


# ---- 4. Montar a tabela e costurar na resposta -------------------------------

def _celula(texto, corte=0):
    texto = _RE_ESPACO.sub(" ", str(texto)).strip()
    if corte and len(texto) > corte:
        texto = texto[:corte] + "…"
    return texto.replace("|", "\\|")


def _agrupar(achados):
    """Junta o mesmo apontamento repetido em vários eventos numa linha só."""
    ordem, grupos = [], {}
    for achado in achados:
        chave = (achado["localizacao"], achado["parametro"],
                 str(achado["valor"]), achado["problema"])
        if chave not in grupos:
            grupos[chave] = dict(achado, eventos=[])
            ordem.append(chave)
        grupos[chave]["eventos"].append(achado["evento"])
    juntos = [grupos[chave] for chave in ordem]
    # erros críticos primeiro; dentro de cada grupo, a ordem do plano
    return sorted(juntos, key=lambda g: 0 if g["problema"].startswith(ERRO) else 1)


def _tabela(achados):
    linhas = [
        "### Limites técnicos do GA4 — validação automática",
        "",
        "| Evento | Localização | Parâmetro | Valor atual | Problema | Correção |",
        "|---|---|---|---|---|---|",
    ]
    for grupo in _agrupar(achados):
        eventos = grupo["eventos"]
        onde = _celula(eventos[0], 60)
        if len(eventos) > 1:
            onde += " (+{} evento(s))".format(len(eventos) - 1)
        linhas.append("| {} | {} | {} | `{}` | {} | {} |".format(
            onde, _celula(grupo["localizacao"]), _celula(grupo["parametro"]),
            _celula(grupo["valor"], 60), _celula(grupo["problema"]),
            _celula(grupo["correcao"])))
    return "\n".join(linhas)


def _linha_de_limite(celulas, idx):
    """True se a linha da IA é um apontamento de tamanho (agora contado aqui)."""
    def cel(nome):
        j = idx.get(nome)
        return celulas[j] if j is not None and j < len(celulas) else ""

    problema = normalizar(cel("problema"))
    if not problema or problema in ("ok", "sem ajustes"):
        return False
    return bool(_RE_APONTAMENTO_LIMITE.search(
        problema + " | " + normalizar(cel("correcao"))))


def aplicar_limites_ga4(texto, json_anexo):
    """Troca os apontamentos de tamanho da IA pela contagem feita em Python.

    Sem JSON anexado (plano colado como texto) nada é feito: sem o plano bruto
    não dá para contar, e aí o apontamento da IA é melhor que nada.
    """
    if not VALIDAR_LIMITES_GA4 or not isinstance(texto, str) or not json_anexo:
        return texto
    try:
        objeto = json.loads(json_anexo)
    except (ValueError, TypeError):
        return texto
    achados, eventos = validar_limites(objeto)
    if not eventos:
        print("[limites] nenhum evento reconhecido no plano — validação de "
              "limites do GA4 não aplicada.")
        return texto

    if "|" in texto:
        texto, removidas = percorrer_tabelas(texto, _linha_de_limite)
        if removidas:
            print("[limites] {} apontamento(s) de tamanho da IA substituído(s) "
                  "pela contagem em código.".format(removidas))
    texto = texto.rstrip()
    if achados:
        print("[limites] {} violação(ões) de limite do GA4 em {} evento(s).".format(
            len(achados), eventos))
        return texto + "\n\n" + _tabela(achados)
    print("[limites] {} evento(s) conferido(s): nenhum limite do GA4 "
          "estourado.".format(eventos))
    return texto + ("\n\n> ✅ **Limites técnicos do GA4:** {} evento(s) "
                    "conferido(s) automaticamente — nenhum limite de tamanho ou "
                    "de quantidade estourado.".format(eventos))
