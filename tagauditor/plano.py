"""Limpeza do plano de tagueamento (JSON) e montagem da mensagem para a IA.

Antes do envio o plano passa por:
  1. Remoção de imagens/base64 (não são auditáveis e estouram o contexto);
  2. "Enxugo": ficam só eventos/parâmetros/canal — DOM, capturas e metadados
     de ferramenta são descartados;
  3. Minificação do JSON.
"""
import json
import re
import unicodedata

from .config import (
    SANITIZAR_CHAVES_DUPLAS, REMOVER_CAMPOS_BASE64, TAMANHO_MIN_BASE64,
    ENXUGAR_PLANO, LIMITE_VALOR_STR, LIMITE_OBS_STR, MINIFICAR_JSON,
    INSTRUCAO_PADRAO, SOMENTE_TABELA,
)


def sanitizar(texto):
    if SANITIZAR_CHAVES_DUPLAS:
        texto = texto.replace("{{", "{ {").replace("}}", "} }")
    return texto


_ALFABETO_B64 = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=-_\r\n"
)


def _parece_base64(texto):
    if not isinstance(texto, str) or len(texto) < TAMANHO_MIN_BASE64:
        return False
    inicio = texto[:100]
    if inicio.startswith("data:") and ";base64," in inicio:
        return True
    return all(c in _ALFABETO_B64 for c in texto[:2000])


def limpar_base64(valor, stats):
    if isinstance(valor, dict):
        novo = {}
        for chave, item in valor.items():
            nome = str(chave).lower()
            if "b64" in nome or "base64" in nome:
                stats["campos"] += 1
                stats["chars"] += (len(item) if isinstance(item, str)
                                   else len(json.dumps(item, ensure_ascii=False)))
                continue
            novo[chave] = limpar_base64(item, stats)
        return novo
    if isinstance(valor, list):
        return [limpar_base64(item, stats) for item in valor]
    if isinstance(valor, str) and _parece_base64(valor):
        stats["campos"] += 1
        stats["chars"] += len(valor)
        return "[imagem/base64 removido: {} caracteres]".format(len(valor))
    return valor


_TOKENS_LIXO = (
    "screenshot", "printscreen", "screencapture", "thumbnail", "evidenc",
    "innerhtml", "outerhtml", "innertext", "outertext",
    "cssselector", "queryselector", "uniqueselector", "xpath", "csspath",
    "boundingrect", "boundingbox", "boundingclientrect", "clientrect",
    "computedstyle", "classname", "classlist",
    "useragent", "sessionrecording", "base64", "dataurl", "data_url",
    "timestamp", "epoch", "createdat", "updatedat", "capturedat",
)

_CHAVES_LIXO = frozenset({
    "dom", "domtree", "dom_tree", "domsnapshot", "dom_snapshot", "domnode",
    "html", "rawhtml", "raw_html", "markup", "node", "nodes",
    "selector", "selectors", "css", "css_path", "xpath", "jspath", "dompath",
    "styles", "style", "inlinestyle", "classe", "classes",
    "rect", "rects", "geometry", "coordinates", "coordenadas", "coords", "coord", "bbox",
    "scroll", "scrollx", "scrolly", "viewport", "position", "posicao", "offset",
    "b64", "timestamp", "timestamps", "createdat", "created_at", "updatedat",
    "updated_at", "capturedat", "captured_at", "epoch", "datetime",
    "status_macro", "statusmacro", "conf_macro",
})


_RE_TAG = re.compile(r"<[^>]+>")
_RE_ESPACO = re.compile(r"\s+")
_RE_OBS = re.compile(r"observa[cç][õo]es\s*:?\s*", re.IGNORECASE)


def _html_para_texto(texto):
    limpo = _RE_TAG.sub(" ", texto)
    for de, para in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                     ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'")):
        limpo = limpo.replace(de, para)
    return _RE_ESPACO.sub(" ", limpo).strip()


def _coleta_strings(valor, saida):
    if isinstance(valor, dict):
        for item in valor.values():
            _coleta_strings(item, saida)
    elif isinstance(valor, list):
        for item in valor:
            _coleta_strings(item, saida)
    elif isinstance(valor, str):
        saida.append(valor)


def _colapsa_dup(texto):
    palavras = texto.split(" ")
    n = len(palavras)
    if n >= 2 and n % 2 == 0 and palavras[:n // 2] == palavras[n // 2:]:
        return " ".join(palavras[:n // 2])
    return texto


def _observacoes(item):
    brutos = []
    _coleta_strings(item, brutos)
    texto = _html_para_texto(" ".join(brutos))
    segmentos = []
    for parte in _RE_OBS.split(texto):
        parte = _colapsa_dup(parte.strip(" |"))
        if parte and parte not in segmentos:
            segmentos.append(parte)
    if not segmentos:
        return ""
    junto = " | ".join(segmentos)
    if len(junto) > LIMITE_OBS_STR:
        junto = junto[:LIMITE_OBS_STR] + "...[+{} caracteres]".format(len(junto) - LIMITE_OBS_STR)
    return junto


def _norm_chave(chave):
    texto = unicodedata.normalize("NFKD", str(chave)).encode("ascii", "ignore").decode("ascii")
    return texto.strip().lower()


def _chave_lixo(chave):
    nome = _norm_chave(chave)
    if nome in _CHAVES_LIXO:
        return True
    for token in _TOKENS_LIXO:
        if token in nome:
            return True
    return False


def _cortar_valor(valor):
    if (isinstance(valor, str) and len(valor) > LIMITE_VALOR_STR
            and not valor.endswith(" caracteres]")):
        return valor[:LIMITE_VALOR_STR] + "...[+{} caracteres]".format(len(valor) - LIMITE_VALOR_STR)
    return valor


def _enx_vazio(valor):
    return (isinstance(valor, dict) and not valor) or (isinstance(valor, list) and not valor)


def _enxugar(valor):
    if isinstance(valor, dict):
        novo = {}
        for chave, item in valor.items():
            nchave = _norm_chave(chave)
            if nchave in ("rich_texts", "richtexts"):
                obs = _observacoes(item)
                if obs:
                    novo["observacoes"] = obs
                continue
            if _chave_lixo(chave):
                continue
            filtrado = _enxugar(item)
            if _enx_vazio(filtrado):
                continue
            novo[chave] = filtrado
        return novo
    if isinstance(valor, list):
        saida = []
        for item in valor:
            filtrado = _enxugar(item)
            if _enx_vazio(filtrado):
                continue
            saida.append(filtrado)
        return saida
    return _cortar_valor(valor)


def _cortar_recursivo(valor):
    if isinstance(valor, dict):
        return {chave: _cortar_recursivo(item) for chave, item in valor.items()}
    if isinstance(valor, list):
        return [_cortar_recursivo(item) for item in valor]
    return _cortar_valor(valor)


def enxugar_plano(objeto):
    reduzido = _enxugar(objeto)
    if _enx_vazio(reduzido) and not _enx_vazio(objeto):
        return _cortar_recursivo(objeto), False
    return reduzido, True


def montar_mensagem(texto_usuario, json_anexo):
    partes = []
    if texto_usuario and texto_usuario.strip():
        partes.append(texto_usuario.strip())

    if json_anexo:
        try:
            objeto = json.loads(json_anexo)
        except json.JSONDecodeError as e:
            raise ValueError("O arquivo anexado não é um JSON válido: {}".format(e))

        if REMOVER_CAMPOS_BASE64:
            stats = {"campos": 0, "chars": 0}
            objeto = limpar_base64(objeto, stats)
            if stats["campos"]:
                print("[limpeza] {} campo(s) de imagem/base64 removido(s) "
                      "(-{} caracteres) antes do envio.".format(
                          stats["campos"], stats["chars"]))

        if ENXUGAR_PLANO:
            antes = len(json.dumps(objeto, ensure_ascii=False))
            objeto, aplicado = enxugar_plano(objeto)
            depois = len(json.dumps(objeto, ensure_ascii=False))
            if aplicado and antes - depois > 0:
                print("[enxugar] plano reduzido de {} para {} caracteres "
                      "(mantidos apenas eventos/parametros/canal).".format(antes, depois))

        if MINIFICAR_JSON:
            json_texto = json.dumps(objeto, ensure_ascii=False, separators=(",", ":"))
        else:
            json_texto = json.dumps(objeto, ensure_ascii=False, indent=2)
        if not partes:
            partes.append(INSTRUCAO_PADRAO)
        partes.append(json_texto)
        if SOMENTE_TABELA:
            partes.append(
                "Responda somente com a auditoria em tabela(s) markdown dos "
                "eventos deste plano, sem texto introdutório e sem pedir o "
                "plano novamente. Toda tabela de eventos deve ter uma coluna "
                "'Status' preenchida com exatamente 'OK' (evento conforme) ou "
                "'AJUSTAR' (evento com problema) - não use outras palavras, "
                "símbolos nem '-' nessa coluna - e uma coluna 'Observação' "
                "com a descrição do problema quando Status=AJUSTAR (quando "
                "Status=OK, escreva 'OK')."
            )

    if not partes:
        raise ValueError("Digite uma mensagem ou anexe um arquivo JSON.")

    return sanitizar("\n\n".join(partes))
