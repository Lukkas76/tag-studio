"""Filtro de falsos positivos da auditoria.

A IA as vezes ignora regras do prompt/da documentacao e inventa apontamentos
que nao correspondem a nenhuma regra oficial, ex.:
    "o uso de _ sugere padrao hierarquico desnecessario"
    "name e path nao estao coerentes entre si"
Antes de exibir ao usuario, as linhas de tabela cujo Problema/Correcao caem
nesses padroes conhecidos sao removidas. Se a tabela ficar vazia, ela vira
"Sem ajustes." (mesmo texto que o prompt manda usar).
"""
import re
import unicodedata

from .config import FILTRAR_FALSOS_POSITIVOS

_RE_ESPACO = re.compile(r"\s+")


def normalizar(texto):
    """minusculas, sem acento/emoji, espacos colapsados (p/ casar os padroes)."""
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    t = t.replace("`", " ").replace("*", " ")
    return _RE_ESPACO.sub(" ", t).strip().lower()


# Especulacao/opiniao (avaliada em Problema + Correcao). Apontamento legitimo
# da doc e factual ("Underline em vez de hifen"); quando o modelo usa
# "sugere", "parece", "se for de fato"... ele esta inventando regra.
_FP_ESPECULACAO = tuple(re.compile(p) for p in (
    r"\bsugere[m]?\b", r"\bsugerind", r"\bparece[m]?\b", r"\baparenta[m]?\b",
    r"\btalvez\b", r"\bpossivelmente\b", r"\bprovavelmente\b",
    r"\bpode[m]? (ser|estar|indicar|sugerir|causar|nao)\b",
    r"\bse (for|este for|esta for|isso for)\b", r"\bde fato\b",
    r"\bcaso (seja|esteja|se aplique)\b",
    r"\b(avaliar|verificar|confirmar|validar|conferir) se\b",
    r"\bnao esta claro\b", r"\bdesnecessari",
    # "coerencia"/"consistencia" ENTRE campos (name x path, name x tela...):
    # regra que nao existe na documentacao
    r"coeren", r"\bentre si\b", r"\bnao condiz", r"\bnao reflete[m]?\b",
))

# Padroes proibidos pelo prompt, avaliados SO na coluna Problema
_FP_PROBLEMA_PROIBIDO = tuple(re.compile(p) for p in (
    # ausencia de bloco/campo: so se audita o que esta declarado no plano
    r"nao (foi |esta )?(declarad|informad|preenchid|enviad|definid)",
    r"(bloco|campo|parametro|variavel|chave|evento)[^,;.]{0,30}(ausente|faltante|omitid)",
    r"(ausencia|falta) d[oe] (bloco|campo|parametro|variavel|chave)",
    r"nao consta no plano",
))

# Placeholders {{...}}/${...} sao valores de runtime - nao auditaveis
# (a unica excecao permitida pelo prompt e placeholder no parametro errado)
_RE_FP_PLACEHOLDER = re.compile(r"\{\{[^}]*\}\}|\$\{[^}]*\}")
_RE_FP_PARAM_ERRADO = re.compile(r"(parametro|bloco|campo|lugar) errad")

# `location` aceita `_` como separador de hierarquia (doc, Padroes de
# Nomenclatura) - reclamacao de underline em location e falso positivo
_RE_FP_UNDERLINE = re.compile(r"underline|underscore|sublinhad|\bhierarq|\b_\b")

# A barra `/` e separador VALIDO de diretorios nestes parametros (doc, "Padroes
# de Nomenclatura": `app-pf/cadastro`, `cambio/algo-deu-errado`) - reclamar de
# barra neles e falso positivo.
_FP_ACEITAM_BARRA = ("location", "path", "page_title", "pagetitle",
                     "url_webview", "urlwebview")
# Reconhece tanto a PALAVRA ("barra", "slash", "diretorio") quanto o SIMBOLO
# `/` solto na frase (ex.: "location usa / fora do padrao"). O `/` isolado
# (cercado por espaco/limite) evita casar barra DENTRO de um valor citado
# (ex.: "acento em cambio/algo" nao e reclamacao de barra).
_RE_FP_BARRA = re.compile(r"\bbarra[s]?\b|\bslash\b|\bdiretorio|(?:^|\s)/(?:\s|$)")

# Correcao tipica de "campo ausente" (pede para ADICIONAR algo ao plano)
_RE_FP_CORR_ADICIONAR = re.compile(r"^(adicionar|incluir|acrescentar|declarar|criar)\b")

_FP_VALORES_VAZIOS = ("", "-", "--", "(vazio)", "(ausente)", "n/a", "na",
                      "nao informado", "nao declarado", "nenhum")


def _fp_celulas(linha):
    t = linha.strip()
    if t.startswith("|"):
        t = t[1:]
    if t.endswith("|"):
        t = t[:-1]
    return [c.strip() for c in t.split("|")]


def _fp_eh_separador(linha):
    t = (linha or "").strip()
    return "|" in t and "-" in t and not t.strip("|:-\t ")


def _fp_linha_falsa(celulas, idx):
    """True se a linha da tabela e um falso positivo conhecido."""
    def cel(nome):
        j = idx.get(nome)
        return celulas[j] if j is not None and j < len(celulas) else ""

    problema = normalizar(cel("problema"))
    if not problema or problema in ("ok", "sem ajustes"):
        return False        # linha sem apontamento: o front ja trata
    correcao = normalizar(cel("correcao"))
    combinado = problema + " | " + correcao

    for rx in _FP_ESPECULACAO:
        if rx.search(combinado):
            return True
    for rx in _FP_PROBLEMA_PROIBIDO:
        if rx.search(problema):
            return True

    valor = cel("valor")
    if _RE_FP_PLACEHOLDER.search(valor) and not _RE_FP_PARAM_ERRADO.search(combinado):
        return True

    onde = normalizar(cel("localizacao") + " " + cel("parametro"))
    if "location" in onde and _RE_FP_UNDERLINE.search(problema):
        return True
    if (any(p in onde for p in _FP_ACEITAM_BARRA)
            and _RE_FP_BARRA.search(problema)):
        return True

    if normalizar(valor) in _FP_VALORES_VAZIOS and _RE_FP_CORR_ADICIONAR.match(correcao):
        return True
    return False


def _fp_mapear_colunas(header):
    idx = {}
    for j, c in enumerate(normalizar(cel) for cel in header):
        if "problema" in c or "observa" in c:
            idx.setdefault("problema", j)
        elif "corre" in c or "recomenda" in c or "ajuste" in c:
            idx.setdefault("correcao", j)
        elif "valor" in c:
            idx.setdefault("valor", j)
        elif "localiza" in c:
            idx.setdefault("localizacao", j)
        elif "parametro" in c or "chave" in c:
            idx.setdefault("parametro", j)
    return idx


def percorrer_tabelas(texto, linha_reprovada):
    """Percorre as tabelas markdown do texto removendo as linhas reprovadas.

    `linha_reprovada(celulas, idx)` recebe as celulas da linha do corpo e o mapa
    de colunas ({"problema": 3, "correcao": 4, ...}) e devolve True para remover.
    Tabela que fica sem nenhuma linha vira "Sem ajustes." (mesmo texto que o
    prompt manda usar). Devolve (texto_novo, quantidade_de_linhas_removidas).
    """
    linhas = texto.split("\n")
    saida, removidas, i = [], 0, 0
    while i < len(linhas):
        linha = linhas[i]
        prox = linhas[i + 1] if i + 1 < len(linhas) else ""
        if "|" not in linha or not _fp_eh_separador(prox):
            saida.append(linha)
            i += 1
            continue
        # inicio de tabela markdown: header | separador | corpo...
        fim = i + 2
        while fim < len(linhas) and "|" in linhas[fim] and linhas[fim].strip():
            fim += 1
        idx = _fp_mapear_colunas(_fp_celulas(linha))
        if "problema" not in idx:
            saida.extend(linhas[i:fim])     # tabela de outro formato: nao mexe
        else:
            mantidas = []
            for corpo in linhas[i + 2:fim]:
                if linha_reprovada(_fp_celulas(corpo), idx):
                    removidas += 1
                else:
                    mantidas.append(corpo)
            if mantidas:
                saida.append(linha)
                saida.append(linhas[i + 1])
                saida.extend(mantidas)
            else:
                saida.append("Sem ajustes.")
        i = fim
    return "\n".join(saida), removidas


def filtrar_falsos_positivos(texto):
    """Remove das tabelas markdown da resposta as linhas de falso positivo."""
    if not FILTRAR_FALSOS_POSITIVOS or not isinstance(texto, str) or "|" not in texto:
        return texto
    texto, removidas = percorrer_tabelas(texto, _fp_linha_falsa)
    if removidas:
        print("[filtro] {} apontamento(s) de falso positivo removido(s) "
              "da auditoria.".format(removidas))
    return texto
