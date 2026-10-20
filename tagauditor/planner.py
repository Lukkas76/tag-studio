"""Tag Planner — do KPI ao plano mínimo de tagueamento.

A pessoa envia as telas da jornada (imagem do computador, print colado ou
frame do Figma) e a pergunta de negócio; o modelo devolve o que DEVE e o que
NÃO deve ser tagueado (KPI, plano mínimo, taxonomia) e, num bloco ```marcacao
no fim da resposta, as coordenadas de cada elemento. Quem desenha a marcação
é o navegador (static/js/planner-marcacao.js), não o modelo: modelo de texto
não edita imagem, mas devolve coordenadas muito bem.

O que este módulo faz:
  - monta o system prompt = regras do time (PLANNER_ARQUIVO_PROMPT) +
    documentação oficial (PLANNER_ARQUIVO_DOCUMENTACAO) + instruções técnicas
    da ferramenta (INSTRUCOES_FERRAMENTA), e recarrega sozinho quando um dos
    arquivos muda (o time ajusta o prompt sem reiniciar o servidor);
  - confere o pedido (limites de telas/partes/MB, imagem de verdade...);
  - monta a mensagem multimodal (contexto em texto + imagens rotuladas);
  - chama a IA (bridge.gerar_texto) e, se a rota do modelo recusar imagem,
    refaz a análise só com as camadas do Figma (quando houver);
  - extrai e normaliza o bloco de coordenadas (extrair_marcacao);
  - busca telas no Figma para a rota /api/planner/figma (figma.buscar);
  - registra tudo na trilha de auditoria — nunca imagem, base64 ou token.

O servidor (servidor.py) só confere a sessão e chama rota_analisar() /
rota_figma(): as duas devolvem (status_http, corpo_dict) e não levantam.
A IA e o Figma são chamados SEMPRE pelo módulo (bridge.gerar_texto,
figma.buscar), nunca importando a função solta: assim o servidor de teste
consegue trocá-los por simulações.
"""
import base64
import binascii
import json
import math
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import atividade
from . import bridge
from .config import (
    BASE_DIR, PLANNER_ARQUIVO_PROMPT, PLANNER_ARQUIVO_DOCUMENTACAO,
    PLANNER_MODELO_LLM, PLANNER_REASONING_EFFORT, PLANNER_MAX_TOKENS_RESPOSTA,
    PLANNER_DETALHE_IMAGEM, PLANNER_MAX_TELAS, PLANNER_MAX_PARTES,
    PLANNER_MAX_MB_IMAGENS, PLANNER_LADO_MAX_ENVIO,
    PLANNER_SEM_IMAGEM_SE_RECUSAR, FIGMA_MAX_FRAMES,
    PLANNER_IDENTIFICAR_IMAGENS, PLANNER_MARCACAO_POR_IMAGEM, PLANNER_MARCACAO_REFAZER,
    PLANNER_MARCACAO_PARALELAS, PLANNER_MARCACAO_REASONING_EFFORT, PLANNER_MARCACAO_MAX_TOKENS,
    PLANNER_ESCLARECER, PLANNER_ESCLARECER_MAX_PERGUNTAS, PLANNER_ESCLARECER_REASONING_EFFORT,
    PLANNER_ESCLARECER_MAX_TOKENS,
)

# O módulo do Figma fala com uma API externa (rede, proxy, cotas). Se ele não
# carregar, o Tag Planner continua de pé com o upload de imagem — e o resto da
# plataforma (Tag Auditor) nem fica sabendo.
try:
    from . import figma
except Exception as _erro_import_figma:
    figma = None
    print("[planner] Busca no Figma indisponível: o módulo figma.py não carregou "
          "({}).".format(str(_erro_import_figma)[:150]))


# ---- Parâmetros de imagem usados pelo navegador (TAG_CONFIG.planner) ---------
# Ficam aqui, e não no config.py, porque o servidor confere parte deles (ex.:
# partes por tela): navegador e servidor precisam enxergar o MESMO número.
QUALIDADE_JPEG_ENVIO = 0.85   # JPEG de cada parte enviada à IA
LIMIAR_DIVISAO = 2.4          # altura/largura acima disso -> tela dividida em partes
PROPORCAO_PARTE = 1.8         # altura de cada parte = largura * 1.8
MAX_PARTES_POR_TELA = 6

# ---- Limites do pedido (o navegador já respeita; aqui é a última barreira) ---
MAX_PERGUNTA = 4000           # pergunta de negócio / pergunta de acompanhamento
MAX_CAMPO_CURTO = 300         # produto, jornada, nome da tela, telas vizinhas
MAX_NOME_TELA = 120           # nome de cada imagem (vai em todos os rótulos)
MAX_MAPA_METRICAS = 20000
MAX_OBSERVACOES = 2000
MAX_ESCLARECIMENTOS = 5       # perguntas da IA respondidas antes da análise
MAX_ITENS_INVENTARIO = 200    # camadas do Figma por tela
MAX_MSGS_HISTORICO_PLANNER = 30
MAX_CHARS_HISTORICO = 400000  # ~100 mil tokens de histórico, no máximo
MAX_CHARS_MSG_HISTORICO = 200000
MAX_ELEMENTOS_MARCACAO = 200
MAX_CHARS_ANALISE_MARCACAO = 30000   # texto da análise mandado à marcação de cada tela

MIMES_ACEITOS = ("image/jpeg", "image/png", "image/webp", "image/gif")
_ALIAS_MIME = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg",
               "image/x-png": "image/png"}
ORIGENS = ("upload", "figma", "colada")

NAO_INFORMADO = "não informado"
# Canal vazio ganha o "não deduza": as regras do time proíbem deduzir o canal
# (uma tela "Android" pode ser WebView) e o modelo tende a chutar.
CANAL_NAO_INFORMADO = "não informado (não deduza)"

NOTA_IMAGENS_HISTORICO = ("(As imagens acima foram enviadas na primeira mensagem e "
                          "não são reenviadas nas perguntas seguintes.)")
AVISO_SEM_IMAGEM_MODELO = ("ATENÇÃO: as imagens não puderam ser enviadas ao modelo; "
                           "baseie a análise no inventário de camadas do Figma e deixe "
                           "claro o que não pôde ser confirmado visualmente.")
AVISO_SEM_IMAGEM_USUARIO = (
    "O modelo de IA não aceitou as imagens nesta análise: ela foi feita só com as "
    "camadas do Figma (textos e posições exatas do arquivo), sem ver as telas. "
    "Confira a marcação e os pontos marcados como \"a validar\".")
MSG_SEM_BLOCO_MARCACAO = ("A resposta não trouxe o bloco de coordenadas (marcacao); "
                          "a marcação visual não pôde ser gerada.")


# ---- Instruções técnicas anexadas ao system prompt ---------------------------
# Texto do SPEC §10, palavra por palavra. Complementa as regras do time
# (prompt_tag_planner.txt, que é verbatim e não deve ser editado para isso):
# diz ao modelo que a ferramenta desenha a marcação e em que formato devolver
# as coordenadas.
INSTRUCOES_FERRAMENTA = """# INSTRUÇÕES DA FERRAMENTA (Tag Planner) — integração técnica

Estas instruções complementam as regras acima; não as substituem.

1. Marcação visual: quem desenha a marcação é a FERRAMENTA (retângulo vermelho nos OBRIGATÓRIOS, amarelo nos OPCIONAIS/DIAGNÓSTICOS, numeração T1, T2..., legenda curta e PNG anotado para download), a partir das coordenadas que você devolver no bloco `marcacao` descrito no item 5. Na seção "Marcação visual" da resposta, escreva apenas que a imagem marcada é gerada pela ferramenta a partir dessas coordenadas; não diga que não é possível editar a imagem e não repita o JSON nessa seção.

2. Imagens: chegam numeradas ("Imagem 1", "Imagem 2"...) na ordem da jornada informada pelo usuário. Na coluna "Tela" das tabelas, use o número e o nome da imagem. Telas muito altas chegam divididas em partes ("Imagem 2 — parte 1 de 3"): são recortes verticais consecutivos, com pequena sobreposição, da MESMA tela; um elemento que aparece na sobreposição é um elemento só. Quando a imagem trouxer no topo uma faixa escura de identificação ("IMAGEM 2 · PARTE 1 DE 3"), use exatamente esses números nos campos "imagem" e "parte" do bloco. A faixa não faz parte da tela: não marque nada nela (as coordenadas continuam sendo da imagem inteira como recebida, com a faixa).

3. Camadas do Figma: quando houver "Inventário de camadas do Figma", os textos e as posições ali são exatos (vêm do arquivo de design). Use-os para confirmar textos em vez de depender de OCR. Ao marcar um elemento que existe no inventário, informe o código dele em "figma_ref" (ex.: "F12"); não invente códigos.

4. Privacidade nas imagens: se aparecerem dados pessoais (CPF, agência/conta, cartão, telefone, e-mail, nome de pessoa), não os transcreva; refira-se ao campo de forma genérica (ex.: "campo de CPF").

5. Bloco de coordenadas — obrigatório, no FINAL da resposta, exatamente um bloco cercado por ```marcacao e ```:

```marcacao
{"elementos": [
  {"id": "T1", "imagem": 1, "parte": 1, "texto": "Solicitar", "classificacao": "OBRIGATÓRIO", "decisao": "TAGUEAR", "legenda": "T1 — TAGUEAR: início da jornada", "figma_ref": null, "bbox_px": {"x": 0, "y": 0, "largura": 0, "altura": 0}, "bbox_normalizada": {"x": 0.0, "y": 0.0, "largura": 0.0, "altura": 0.0}}
]}
```

Regras do bloco:
- Inclua todos os elementos OBRIGATÓRIOS e OPCIONAIS/DIAGNÓSTICOS que aparecem nas imagens (são os desenhados). Os NÃO TAGUEAR e NECESSITA CONTEXTO visíveis também podem entrar (a ferramenta só os mostra sob demanda).
- "classificacao": exatamente "OBRIGATÓRIO", "OPCIONAL/DIAGNÓSTICO", "NÃO TAGUEAR" ou "NECESSITA CONTEXTO". "decisao": "TAGUEAR", "OPCIONAL", "NÃO TAGUEAR" ou "A VALIDAR".
- "id": o mesmo T1, T2... usado nas tabelas da resposta. "legenda": curta, no formato "T1 — TAGUEAR: início da jornada".
- "imagem" e "parte": onde o elemento aparece (parte 1 quando a imagem não foi dividida). "imagem" é o número da TELA (Imagem 1, Imagem 2...), não a posição do recorte na mensagem; "parte" é o número da parte dentro daquela tela. Coordenadas relativas a essa imagem/parte, com origem no canto superior esquerdo: "bbox_px" em pixels da imagem como recebida (as dimensões vêm na mensagem) e "bbox_normalizada" = bbox_px dividido pela largura/altura (valores de 0 a 1).
- O retângulo envolve o elemento clicável inteiro (botão, card, link, campo), não só o texto.
- Eventos sem elemento visível nas imagens (ex.: sucesso numa tela que não foi enviada) ficam fora do bloco.
- Cobertura: TODA imagem enviada precisa aparecer no bloco. Se uma imagem não tiver nenhum elemento a marcar, inclua para ela um item sem id e sem coordenadas: {"imagem": 3, "sem_elementos": true, "motivo": "tela de carregamento, sem interação"}. Nunca deixe uma imagem de fora em silêncio.
- JSON válido: aspas duplas, sem comentários, sem vírgula sobrando.

6. Perguntas de acompanhamento: responda direto ao que foi perguntado, mantendo os mesmos IDs (T1, T2...). Só inclua um novo bloco `marcacao` se a marcação mudar; nesse caso devolva o bloco COMPLETO (ele substitui o anterior)."""


# Instruções da 2ª etapa (uma chamada por tela): só localizar o que a análise
# já decidiu. System prompt curto de propósito — as regras do time e a
# documentação já foram aplicadas na análise.
INSTRUCOES_MARCACAO = """# MARCAÇÃO VISUAL DE UMA TELA — tarefa técnica

Você recebe UMA tela (às vezes dividida em partes) de uma análise de mensuração que já foi feita, a lista de elementos que a análise decidiu e o texto dessa análise. A sua única tarefa é LOCALIZAR nesta tela os elementos da análise e devolver as coordenadas. Não refaça a análise, não mude classificações nem IDs e não escreva nada fora do bloco.

1. Use os mesmos "id" (T1, T2...), "classificacao", "decisao" e "legenda" da análise. Se um elemento citado nas tabelas da análise aparece nesta tela mas não está na lista, inclua-o com o id que a análise deu a ele.
2. Inclua só elementos visíveis NESTA tela; elemento da lista que não aparece aqui fica fora. Inclua os OBRIGATÓRIOS e OPCIONAIS/DIAGNÓSTICOS visíveis; os NÃO TAGUEAR e NECESSITA CONTEXTO visíveis também podem entrar.
3. "imagem" = o número desta tela (informado na mensagem). "parte" = a parte onde o elemento aparece (1 quando a tela não foi dividida). Um elemento na sobreposição entre partes entra uma vez só.
4. Coordenadas relativas à imagem/parte como recebida, origem no canto superior esquerdo: "bbox_px" em pixels e "bbox_normalizada" = bbox_px dividido pela largura/altura (0 a 1). Se houver uma faixa escura de identificação no topo, ela conta nas coordenadas, mas não é parte da tela: nada fica dentro dela.
5. O retângulo envolve o elemento clicável inteiro (botão, card, link, campo), não só o texto.
6. "classificacao": exatamente "OBRIGATÓRIO", "OPCIONAL/DIAGNÓSTICO", "NÃO TAGUEAR" ou "NECESSITA CONTEXTO". "decisao": "TAGUEAR", "OPCIONAL", "NÃO TAGUEAR" ou "A VALIDAR".
7. Camadas do Figma, quando houver: informe o código da camada em "figma_ref" (ex.: "F12"); não invente códigos.
8. Não transcreva dados pessoais que aparecerem (CPF, conta, cartão, telefone, e-mail, nome): refira-se ao campo de forma genérica.
9. Se a tela não tiver nenhum elemento a marcar, devolva {"elementos": [], "sem_elementos": true, "motivo": "..."}.
10. Responda SOMENTE com um bloco cercado por ```marcacao e ```, JSON válido (aspas duplas, sem comentários):

```marcacao
{"elementos": [
  {"id": "T1", "imagem": 1, "parte": 1, "texto": "Solicitar", "classificacao": "OBRIGATÓRIO", "decisao": "TAGUEAR", "legenda": "T1 — TAGUEAR: início da jornada", "figma_ref": null, "bbox_px": {"x": 0, "y": 0, "largura": 0, "altura": 0}, "bbox_normalizada": {"x": 0.0, "y": 0.0, "largura": 0.0, "altura": 0.0}}
]}
```"""


# Etapa de dúvidas (antes da análise): mesmas regras do time e documentação,
# mas no lugar das instruções da análise vai este bloco ({max} é trocado por
# PLANNER_ESCLARECER_MAX_PERGUNTAS).
INSTRUCOES_ESCLARECIMENTO = """# ETAPA DE ESCLARECIMENTO (antes da análise) — instruções da ferramenta

Esta chamada NÃO é a análise: ainda não monte KPI, tabelas, plano de eventos nem coordenadas. Olhe as imagens e o pedido e identifique o que você precisa confirmar com o usuário para definir com segurança o KPI (numerador e denominador), o evento de conversão e o escopo da jornada. Esta etapa substitui a pergunta de esclarecimento do fim do relatório: as respostas chegam junto com o pedido de análise.

1. Faça no máximo {max} perguntas, e só as que mudariam materialmente a recomendação (KPI, conversão, denominador, escopo, produto, regra de negócio). Não pergunte o que dá para ver nas imagens nem o que já foi informado no pedido. Se o canal não foi informado e a taxonomia depender dele, você pode perguntar.
2. Cada pergunta: uma frase curta e direta; "opcoes" com 2 a 4 respostas prováveis, curtas e mutuamente exclusivas; "por_que" em uma frase (o que muda na recomendação); "premissa" = o que você vai assumir se o usuário não responder.
3. "entendimento": uma ou duas frases com o que você entendeu das telas e do objetivo (produto, jornada e o que parece ser a conversão).
4. Sem dúvida que mude a recomendação, devolva "perguntas": [].
5. Não transcreva dados pessoais que aparecerem nas imagens.
6. Responda SOMENTE com um bloco cercado por ```esclarecimento e ```, com JSON válido (aspas duplas, sem comentários):

```esclarecimento
{"entendimento": "...", "perguntas": [{"pergunta": "...", "por_que": "...", "opcoes": ["...", "..."], "premissa": "..."}]}
```"""


class ErroPlanner(Exception):
    """Erro já com a mensagem amigável (pt-BR) para a pessoa e o status HTTP
    da resposta (400 = pedido inválido, 502 = falha da IA, 500 = servidor)."""

    def __init__(self, mensagem, status=400):
        super().__init__(mensagem)
        self.status = status


# ---- Utilitários -------------------------------------------------------------
_RE_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RE_ESPACOS = re.compile(r"\s+")
# Metade de um emoji (surrogate UTF-16 solto): aparece quando o navegador
# corta um nome no meio do emoji. Não vira UTF-8 - derrubaria o envio à IA.
_RE_SURROGATE = re.compile("[\ud800-\udfff]")


def sem_surrogates(texto):
    """Troca surrogates soltos por "�" (texto que vem do navegador)."""
    return _RE_SURROGATE.sub("�", texto) if isinstance(texto, str) else texto


def _console(mensagem):
    """print() que nunca derruba a requisição: a janela do servidor pode estar
    num console/arquivo em cp1252 e a mensagem pode trazer texto do usuário
    (nome de tela com emoji, por exemplo). Só o caractere que o console não
    sabe mostrar vira "?"; os acentos continuam."""
    try:
        print(mensagem)
    except UnicodeEncodeError:
        codificacao = getattr(sys.stdout, "encoding", None) or "ascii"
        try:
            print(mensagem.encode(codificacao, "replace").decode(codificacao))
        except Exception:
            pass
    except Exception:
        pass


def _texto(valor, limite=None, linha_unica=False):
    """Texto limpo: sem caracteres de controle, sem espaços nas pontas e
    cortado em `limite`. linha_unica junta tudo numa linha (campos que vão
    dentro de uma linha da mensagem). Número vira texto; o resto vira ""."""
    if valor is None or isinstance(valor, bool):
        return ""
    if isinstance(valor, (int, float)):
        valor = str(valor)
    elif not isinstance(valor, str):
        return ""
    texto = _RE_CONTROLE.sub("", valor.replace("\r\n", "\n").replace("\r", "\n"))
    texto = sem_surrogates(texto)
    texto = _RE_ESPACOS.sub(" ", texto).strip() if linha_unica else texto.strip()
    if limite is not None and len(texto) > limite:
        texto = texto[:limite].rstrip()
    return texto


def _sem_acento(texto):
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")


def _inteiro(valor, minimo=1, maximo=100000):
    """Inteiro dentro de [minimo, maximo] a partir de número JSON (ou texto
    numérico); float é arredondado. None se faltar ou for inválido."""
    if valor is None or isinstance(valor, bool):
        return None
    try:
        if isinstance(valor, int):
            numero = valor
        elif isinstance(valor, float):
            numero = int(round(valor))
        elif isinstance(valor, str) and valor.strip():
            numero = int(round(float(valor.strip().replace(",", "."))))
        else:
            return None
    except (ValueError, OverflowError):
        return None
    return numero if minimo <= numero <= maximo else None


def _numero(valor):
    """float finito a partir de número JSON ou de texto ("12.5", "12,5",
    "45%", "51px"). None se faltar ou for inválido."""
    if valor is None or isinstance(valor, bool):
        return None
    try:
        if isinstance(valor, (int, float)):
            numero = float(valor)
        elif isinstance(valor, str):
            texto = valor.strip().lower().replace(",", ".")
            for sufixo in ("px", "%"):
                if texto.endswith(sufixo):
                    texto = texto[:-len(sufixo)].strip()
            numero = float(texto)
        else:
            return None
    except (ValueError, OverflowError):
        return None
    return numero if math.isfinite(numero) else None


def _fmt_int(numero):
    """1234567 -> "1.234.567" (separador de milhar brasileiro)."""
    return "{:,}".format(int(numero)).replace(",", ".")


def _fmt_num(numero):
    """20 -> "20"; 20.5 -> "20,5"."""
    numero = float(numero)
    if numero.is_integer():
        return _fmt_int(numero)
    return "{:.1f}".format(numero).replace(".", ",")


def _fmt_mb(n_bytes):
    return "{:.1f}".format(n_bytes / 1048576.0).replace(".", ",")


def _detalhe_erro(erro):
    return (str(erro) or erro.__class__.__name__).strip()[:400]


# ---- Config do navegador -----------------------------------------------------

def figma_configurado():
    """True se o servidor tem um token do Figma (FIGMA_TOKEN). Nunca levanta:
    é chamada ao montar todas as páginas."""
    if figma is None:
        return False
    try:
        return bool(figma.configurado())
    except Exception:
        return False


def config_frontend():
    """Bloco "planner" do window.TAG_CONFIG (lido por static/js/planner*.js)."""
    return {
        "figmaConfigurado": figma_configurado(),
        "maxTelas": PLANNER_MAX_TELAS,
        "maxPartes": PLANNER_MAX_PARTES,
        "maxMbImagens": PLANNER_MAX_MB_IMAGENS,
        "ladoMaxEnvio": PLANNER_LADO_MAX_ENVIO,
        "qualidadeJpeg": QUALIDADE_JPEG_ENVIO,
        "limiarDivisao": LIMIAR_DIVISAO,
        "proporcaoParte": PROPORCAO_PARTE,
        "maxPartesPorTela": MAX_PARTES_POR_TELA,
        "identificarImagens": bool(PLANNER_IDENTIFICAR_IMAGENS),
        "esclarecer": bool(PLANNER_ESCLARECER),
        "maxPerguntas": int(PLANNER_ESCLARECER_MAX_PERGUNTAS or 0),
    }


# ---- System prompt (regras + documentação + instruções da ferramenta) --------
SEPARADOR_PROMPT = "\n\n---\n\n"
TITULO_DOCUMENTACAO = "# DOCUMENTACAO OFICIAL (base de conhecimento)"

_trava_prompt = threading.Lock()
_cache_prompt = {"chave": None, "texto": None}


def _caminho_projeto(nome):
    return nome if os.path.isabs(nome) else os.path.join(BASE_DIR, nome)


def _assinatura(caminho):
    """(data de modificação em ns, tamanho) do arquivo; None se não existir.
    O tamanho entra junto porque duas gravações no mesmo instante podem ter a
    mesma data em alguns sistemas de arquivos (OneDrive/rede)."""
    try:
        info = os.stat(caminho)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def _ler_texto(caminho):
    """Conteúdo do arquivo (UTF-8, com ou sem BOM). Se alguém salvou como ANSI
    no Bloco de Notas, lê em cp1252 em vez de derrubar a análise."""
    with open(caminho, "rb") as f:
        bruto = f.read()
    try:
        return bruto.decode("utf-8-sig").strip()
    except UnicodeDecodeError:
        _console("[planner] {} não está em UTF-8; lido como ANSI (cp1252). Salve o "
                 "arquivo em UTF-8.".format(os.path.basename(caminho)))
        return bruto.decode("cp1252", errors="replace").strip()


def obter_system_prompt():
    """System prompt do Tag Planner, montado assim:

        regras do time (PLANNER_ARQUIVO_PROMPT)
        ---
        # DOCUMENTACAO OFICIAL (base de conhecimento)    <- se configurada
        documentação (PLANNER_ARQUIVO_DOCUMENTACAO)
        ---
        INSTRUCOES_FERRAMENTA

    Fica em cache, mas é remontado sozinho quando um dos dois arquivos muda
    (data/tamanho): o time ajusta o prompt sem reiniciar o servidor. Sem o
    arquivo de regras levanta ErroPlanner (500) com a explicação; sem a
    documentação, segue sem ela e avisa no console."""
    if not PLANNER_ARQUIVO_PROMPT:
        raise ErroPlanner("O arquivo de regras do Planner não está configurado "
                          "(PLANNER_ARQUIVO_PROMPT no config.py).", 500)
    caminho_regras = _caminho_projeto(PLANNER_ARQUIVO_PROMPT)
    caminho_doc = (_caminho_projeto(PLANNER_ARQUIVO_DOCUMENTACAO)
                   if PLANNER_ARQUIVO_DOCUMENTACAO else "")
    chave = (caminho_regras, _assinatura(caminho_regras),
             caminho_doc, _assinatura(caminho_doc) if caminho_doc else None)
    with _trava_prompt:
        if _cache_prompt["texto"] is not None and _cache_prompt["chave"] == chave:
            return _cache_prompt["texto"]
        sem_regras = ErroPlanner(
            "O arquivo de regras do Planner ({}) não foi encontrado na pasta do "
            "projeto. Restaure o arquivo ou ajuste PLANNER_ARQUIVO_PROMPT no "
            "config.py.".format(PLANNER_ARQUIVO_PROMPT), 500)
        if chave[1] is None:
            raise sem_regras
        try:
            regras = _ler_texto(caminho_regras)
        except OSError:
            raise sem_regras
        if not regras:
            raise ErroPlanner("O arquivo de regras do Planner ({}) está vazio.".format(
                PLANNER_ARQUIVO_PROMPT), 500)
        doc = ""
        if caminho_doc:
            try:
                doc = _ler_texto(caminho_doc)
            except OSError as e:
                _console("[planner] Não consegui ler a documentação {} ({}); sigo sem a "
                         "base de conhecimento.".format(PLANNER_ARQUIVO_DOCUMENTACAO,
                                                        str(e)[:100]))
        blocos = [regras]
        if doc:
            blocos.append(TITULO_DOCUMENTACAO + "\n\n" + doc)
        blocos.append(INSTRUCOES_FERRAMENTA)
        texto = SEPARADOR_PROMPT.join(blocos)
        recarga = _cache_prompt["texto"] is not None
        _cache_prompt["chave"], _cache_prompt["texto"] = chave, texto
        _console("[planner] System prompt {}: regras {} + documentação {} + instruções da "
                 "ferramenta = {} caracteres.".format(
                     "recarregado (arquivo alterado)" if recarga else "carregado",
                     len(regras), len(doc), len(texto)))
        return texto


# ---- Validação do pedido -----------------------------------------------------
_CANAIS = {"web": "Web", "webview": "WebView", "appnativo": "App nativo",
           "app": "App nativo", "nativo": "App nativo",
           "aplicativonativo": "App nativo", "aplicativo": "App nativo"}
_PLATAFORMAS = {"android": "Android", "ios": "iOS", "navegador": "Navegador",
                "browser": "Navegador", "web": "Navegador"}
_VALORES_VAZIOS = {"", "naoinformado", "nenhum", "nenhuma", "na", "nd"}

_RE_CHAVE_FIGMA = re.compile(r"^[0-9A-Za-z]{1,128}$")
# Ids de nó do Figma: "12:34" ou, dentro de instâncias, "I12:34;56:78".
_RE_NODE_ID = re.compile(r"^I?\d+:\d+(?:;I?\d+:\d+)*$")
_RE_REF_INVENTARIO = re.compile(r"^[A-Za-z]{0,3}\d{1,6}$")


def _normalizar_opcao(valor, opcoes):
    """Canal/plataforma: aceita variações ("webview", "Web View", "app") e
    devolve o nome oficial; vazio/"Não informado" vira ""; valor desconhecido
    segue como foi digitado (o modelo lê texto livre)."""
    texto = _texto(valor, 60, linha_unica=True)
    chave = re.sub(r"[^a-z0-9]", "", _sem_acento(texto).lower())
    if chave in _VALORES_VAZIOS:
        return ""
    return opcoes.get(chave, texto)


def _normalizar_node_id(valor):
    """Id de nó do Figma com ':' ("12-34" e "12%3A34" da URL viram "12:34";
    às vezes o link vem codificado duas vezes)."""
    if isinstance(valor, bool) or not isinstance(valor, (str, int)):
        return None
    texto = str(valor).strip()
    for _ in range(2):
        if "%" not in texto:
            break
        texto = urllib.parse.unquote(texto)
    texto = texto.strip().replace("-", ":")
    return texto if _RE_NODE_ID.match(texto) else None


def validar_contexto(contexto):
    """Campos do formulário (SPEC §5.4) limpos e cortados. Só a pergunta de
    negócio é obrigatória."""
    ctx = contexto if isinstance(contexto, dict) else {}
    pergunta = _texto(ctx.get("pergunta"))
    if not pergunta:
        raise ErroPlanner("Informe a pergunta de negócio: é ela que define o que "
                          "precisa ser tagueado.")
    if len(pergunta) > MAX_PERGUNTA:
        raise ErroPlanner("A pergunta de negócio tem {} caracteres; o máximo é {}. Resuma a "
                          "pergunta e deixe os detalhes em Observações.".format(
                              _fmt_int(len(pergunta)), _fmt_int(MAX_PERGUNTA)))
    return {
        "pergunta": pergunta,
        "produto": _texto(ctx.get("produto"), MAX_CAMPO_CURTO, linha_unica=True),
        "jornada": _texto(ctx.get("jornada"), MAX_CAMPO_CURTO, linha_unica=True),
        "tela": _texto(ctx.get("tela"), MAX_CAMPO_CURTO, linha_unica=True),
        "canal": _normalizar_opcao(ctx.get("canal"), _CANAIS),
        "plataforma": _normalizar_opcao(ctx.get("plataforma"), _PLATAFORMAS),
        "tela_anterior": _texto(ctx.get("tela_anterior"), MAX_CAMPO_CURTO, linha_unica=True),
        "tela_seguinte": _texto(ctx.get("tela_seguinte"), MAX_CAMPO_CURTO, linha_unica=True),
        "mapa_metricas": _texto(ctx.get("mapa_metricas"), MAX_MAPA_METRICAS),
        "observacoes": _texto(ctx.get("observacoes"), MAX_OBSERVACOES),
        "esclarecimentos": _validar_esclarecimentos(ctx.get("esclarecimentos")),
    }


def _validar_esclarecimentos(lista):
    """Dúvidas da IA com a resposta da pessoa (ou só a premissa, se pulou):
    [{"pergunta", "resposta", "premissa"}]. Item sem pergunta é ignorado."""
    if not isinstance(lista, list):
        return []
    saida = []
    for item in lista[:MAX_ESCLARECIMENTOS]:
        if not isinstance(item, dict):
            continue
        pergunta = _texto(item.get("pergunta"), 300, linha_unica=True)
        if not pergunta:
            continue
        saida.append({
            "pergunta": pergunta,
            "resposta": _texto(item.get("resposta"), 1000),
            "premissa": _texto(item.get("premissa"), 300, linha_unica=True),
        })
    return saida


def _bbox_inventario(valor):
    """bbox de uma camada do Figma (normalizada à tela inteira) ou None."""
    if not isinstance(valor, dict):
        return None
    numeros = [_numero(valor.get(k)) for k in ("x", "y", "largura", "altura")]
    if any(n is None for n in numeros):
        return None
    x, y, largura, altura = numeros
    return {"x": round(x, 4), "y": round(y, 4),
            "largura": round(largura, 4), "altura": round(altura, 4)}


def _sanitizar_inventario(lista):
    """Inventário de camadas do Figma: só os campos do SPEC (§5.3), textos
    cortados e no máximo MAX_ITENS_INVENTARIO itens. Item sem código ("F12")
    é descartado — sem ele o modelo não tem como referenciar a camada."""
    if not isinstance(lista, list):
        return []
    itens = []
    for item in lista:
        if len(itens) >= MAX_ITENS_INVENTARIO:
            break
        if not isinstance(item, dict):
            continue
        ref = _texto(item.get("ref"), 12, linha_unica=True)
        if not _RE_REF_INVENTARIO.match(ref):
            continue
        itens.append({
            "ref": ref,
            "id": _normalizar_node_id(item.get("id")) or "",
            "tipo": _texto(item.get("tipo"), 40, linha_unica=True).upper(),
            "nome": _texto(item.get("nome"), 120, linha_unica=True),
            "texto": _texto(item.get("texto"), 300, linha_unica=True),
            "interativo": item.get("interativo") is True,
            "bbox": _bbox_inventario(item.get("bbox")),
        })
    return itens


def _validar_figma_tela(valor):
    """Bloco "figma" de uma tela ({file_key, node_id, inventario}) ou None."""
    if not isinstance(valor, dict):
        return None
    file_key = _texto(valor.get("file_key"), 128, linha_unica=True)
    return {
        "file_key": file_key if _RE_CHAVE_FIGMA.match(file_key) else "",
        "node_id": _normalizar_node_id(valor.get("node_id")) or "",
        "inventario": _sanitizar_inventario(valor.get("inventario")),
    }


def _validar_recorte(valor, numero, total, onde):
    """Faixa vertical da tela coberta pela parte (frações da altura da tela)."""
    if valor is None:
        # Parte única cobre a tela toda; tela dividida sem recorte assume
        # faixas iguais, sem sobreposição (melhor que recusar a análise).
        return {"y": round((numero - 1) / float(total), 6), "altura": round(1.0 / total, 6)}
    invalido = ErroPlanner("{}: recorte inválido (y e altura são frações da altura da "
                           "tela, entre 0 e 1).".format(onde))
    if not isinstance(valor, dict):
        raise invalido
    y, altura = _numero(valor.get("y")), _numero(valor.get("altura"))
    if (y is None or altura is None or not 0 <= y <= 1 or not 0 < altura <= 1
            or y + altura > 1.001):
        raise invalido
    altura = min(altura, 1.0 - y)    # absorve o arredondamento do navegador
    if altura <= 0:
        raise invalido
    return {"y": y, "altura": altura}


def _validar_parte(bruta, numero, total, rotulo_tela):
    onde = "{}, parte {}".format(rotulo_tela, numero)
    if not isinstance(bruta, dict):
        raise ErroPlanner("{}: formato inválido.".format(onde))
    b64 = bruta.get("base64")
    if not isinstance(b64, str) or not b64.strip():
        raise ErroPlanner("{}: a imagem veio vazia.".format(onde))
    b64 = b64.strip()
    mime = _texto(bruta.get("mime"), 40, linha_unica=True).lower()
    if b64[:5].lower() == "data:":
        # Veio a data URI inteira (data:image/png;base64,...): aproveita o tipo
        # e tira o prefixo — o contrato é base64 puro, mas não custa aceitar.
        cabecalho, _, b64 = b64.partition(",")
        mime = mime or cabecalho[5:].split(";")[0].strip().lower()
        b64 = b64.strip()
    mime = _ALIAS_MIME.get(mime, mime)
    # Sem tipo informado, quem decide são os bytes (_conferir_imagem); tipo
    # informado e fora da lista é recusado já aqui, com a mensagem certa.
    if mime and mime not in MIMES_ACEITOS:
        raise ErroPlanner("{}: formato de imagem não suportado ({}). Use PNG, JPEG, WebP "
                          "ou GIF.".format(onde, mime))
    largura, altura = _inteiro(bruta.get("largura")), _inteiro(bruta.get("altura"))
    if ((bruta.get("largura") is not None and largura is None)
            or (bruta.get("altura") is not None and altura is None)):
        raise ErroPlanner("{}: largura/altura inválidas.".format(onde))
    return {"parte": numero, "mime": mime, "base64": b64,
            "largura": largura, "altura": altura,
            # altura (px) da faixa "IMAGEM n · PARTE p" desenhada no topo pelo navegador
            "faixa": _inteiro(bruta.get("faixa"), 0, 4000) or 0,
            "recorte": _validar_recorte(bruta.get("recorte"), numero, total, onde)}


def _validar_tela(bruta, indice):
    if not isinstance(bruta, dict):
        raise ErroPlanner("Tela {}: formato inválido.".format(indice))
    # A posição na lista é a ordem da jornada: o "indice" do cliente é só
    # informativo (a marcação devolvida usa imagem = posição 1..N).
    nome = _texto(bruta.get("nome"), MAX_NOME_TELA, linha_unica=True) or "Tela {}".format(indice)
    rotulo = 'Tela {} ("{}")'.format(indice, nome)
    origem = _texto(bruta.get("origem"), 20, linha_unica=True).lower()
    if origem not in ORIGENS:
        origem = "upload"
    largura, altura = _inteiro(bruta.get("largura")), _inteiro(bruta.get("altura"))
    if ((bruta.get("largura") is not None and largura is None)
            or (bruta.get("altura") is not None and altura is None)):
        raise ErroPlanner("{}: largura/altura inválidas.".format(rotulo))
    figma_tela = _validar_figma_tela(bruta.get("figma"))
    partes = bruta.get("partes")
    if partes is None:
        partes = []
    if not isinstance(partes, list):
        raise ErroPlanner("{}: formato das partes inválido.".format(rotulo))
    if not partes:
        # Frame do Figma cujo PNG a rede/proxy barrou: vale analisar só pelas
        # camadas (textos e posições exatos). Sem camadas, não há o que ler.
        if not (figma_tela and figma_tela["inventario"]):
            raise ErroPlanner("{}: nenhuma imagem recebida.".format(rotulo))
    if len(partes) > MAX_PARTES_POR_TELA:
        raise ErroPlanner("{}: no máximo {} partes por tela (foram {}).".format(
            rotulo, MAX_PARTES_POR_TELA, len(partes)))
    return {
        "indice": indice, "nome": nome, "origem": origem,
        "largura": largura, "altura": altura,
        "figma": figma_tela,
        "sem_imagem": not partes,
        "partes": [_validar_parte(p, n, len(partes), rotulo)
                   for n, p in enumerate(partes, 1)],
    }


def _tipo_por_assinatura(dados):
    """Tipo real da imagem pelos primeiros bytes (o "mime" do cliente pode
    mentir; o que a IA recebe tem de ser imagem de verdade)."""
    if dados.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if dados.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if dados[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(dados) >= 12 and dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":
        return "image/webp"
    return None


_MARCADORES_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                   0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _dimensoes_jpeg(dados):
    i, n = 2, len(dados)
    while i + 4 <= n:
        if dados[i] != 0xFF:
            i += 1
            continue
        marcador = dados[i + 1]
        if marcador == 0xFF:              # bytes de preenchimento
            i += 1
            continue
        if marcador in (0x01, 0xD8) or 0xD0 <= marcador <= 0xD7:
            i += 2                        # marcadores sem tamanho
            continue
        if marcador in (0xD9, 0xDA):      # fim / dados de imagem sem SOF antes
            return None
        tamanho = int.from_bytes(dados[i + 2:i + 4], "big")
        if marcador in _MARCADORES_SOF:
            if i + 9 > n:
                return None
            altura = int.from_bytes(dados[i + 5:i + 7], "big")
            largura = int.from_bytes(dados[i + 7:i + 9], "big")
            return largura, altura
        i += 2 + tamanho
    return None


def _dimensoes_webp(dados):
    if len(dados) < 30:
        return None
    bloco = dados[12:16]
    if bloco == b"VP8X":
        return (1 + int.from_bytes(dados[24:27], "little"),
                1 + int.from_bytes(dados[27:30], "little"))
    if bloco == b"VP8 " and dados[23:26] == b"\x9d\x01\x2a":
        return (int.from_bytes(dados[26:28], "little") & 0x3FFF,
                int.from_bytes(dados[28:30], "little") & 0x3FFF)
    if bloco == b"VP8L" and dados[20] == 0x2F:
        bits = int.from_bytes(dados[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def _dimensoes_imagem(dados, tipo):
    """(largura, altura) lidas do cabeçalho da imagem; None se não der."""
    try:
        if tipo == "image/png" and len(dados) >= 24 and dados[12:16] == b"IHDR":
            dims = (int.from_bytes(dados[16:20], "big"), int.from_bytes(dados[20:24], "big"))
        elif tipo == "image/gif" and len(dados) >= 10:
            dims = (int.from_bytes(dados[6:8], "little"), int.from_bytes(dados[8:10], "little"))
        elif tipo == "image/webp":
            dims = _dimensoes_webp(dados)
        elif tipo == "image/jpeg":
            dims = _dimensoes_jpeg(dados)
        else:
            dims = None
    except Exception:
        return None
    if not dims or min(dims) < 1 or max(dims) > 100000:
        return None
    return dims


def _conferir_imagem(tela, parte):
    """Decodifica a parte, confere que é imagem de verdade e acerta tipo e
    dimensões pelo cabeçalho. Devolve o tamanho em bytes."""
    onde = 'Tela {} ("{}"), parte {}'.format(tela["indice"], tela["nome"], parte["parte"])
    try:
        dados = base64.b64decode(parte["base64"], validate=True)
    except (binascii.Error, ValueError):
        raise ErroPlanner("{}: a imagem não veio em base64 válido.".format(onde))
    tipo = _tipo_por_assinatura(dados)
    if tipo is None:
        raise ErroPlanner("{}: o conteúdo não é uma imagem PNG, JPEG, WebP ou GIF "
                          "válida.".format(onde))
    # Vale o tipo real dos bytes: o rótulo da data URI precisa bater com eles.
    parte["mime"] = tipo
    dims = _dimensoes_imagem(dados, tipo)
    if dims:
        # Vale o tamanho real: as coordenadas em px que o modelo devolve são da
        # imagem que ele VÊ, então o rótulo tem de trazer esse tamanho.
        if parte["largura"] is not None and (parte["largura"], parte["altura"]) != dims:
            _console("[planner] {}: o navegador informou {}x{} px, mas a imagem tem {}x{} px; "
                     "vale a imagem.".format(onde, parte["largura"], parte["altura"],
                                             dims[0], dims[1]))
        parte["largura"], parte["altura"] = dims
    elif parte["largura"] is None or parte["altura"] is None:
        raise ErroPlanner("{}: largura/altura da imagem não informadas.".format(onde))
    return len(dados)


def validar_telas(lista):
    """Confere as telas (SPEC §5.4) e devolve (telas_limpas, bytes_imagens).
    Ordem das conferências: estrutura e limites baratos primeiro; só depois
    decodifica o base64 (até 20 MB) para conferir as imagens."""
    if not isinstance(lista, list) or not lista:
        raise ErroPlanner("Envie ao menos uma tela (imagem ou frame do Figma) para analisar.")
    if len(lista) > PLANNER_MAX_TELAS:
        raise ErroPlanner("Envie no máximo {} telas por análise (foram {}).".format(
            PLANNER_MAX_TELAS, len(lista)))
    telas = [_validar_tela(bruta, indice) for indice, bruta in enumerate(lista, 1)]

    total_partes = sum(len(t["partes"]) for t in telas)
    if total_partes > PLANNER_MAX_PARTES:
        raise ErroPlanner("As telas somam {} partes de imagem; o máximo por análise é {} "
                          "(limite do Azure OpenAI de imagens por requisição). Remova telas "
                          "ou envie telas menos altas.".format(total_partes, PLANNER_MAX_PARTES))
    total_b64 = sum(len(p["base64"]) for t in telas for p in t["partes"])
    if total_b64 > float(PLANNER_MAX_MB_IMAGENS) * 1024 * 1024:
        raise ErroPlanner("As imagens somam {} MB; o limite por análise é {} MB. Envie menos "
                          "telas ou imagens menores.".format(
                              _fmt_mb(total_b64), _fmt_num(PLANNER_MAX_MB_IMAGENS)))

    bytes_imagens = 0
    for tela in telas:
        for parte in tela["partes"]:
            bytes_imagens += _conferir_imagem(tela, parte)
            # "faixa" que ocupa metade da imagem não é faixa de identificação
            if parte["faixa"] * 2 >= (parte["altura"] or 0):
                parte["faixa"] = 0
        if (tela["largura"] is None or tela["altura"] is None) and tela["partes"]:
            tela["largura"] = tela["partes"][0]["largura"]
            tela["altura"] = tela["partes"][0]["altura"]
    return telas, bytes_imagens


def validar_analise(dados):
    """Pedido de análise nova (modo "analise") limpo:
    {"contexto": {...}, "telas": [...], "bytes_imagens": n}."""
    if not isinstance(dados, dict):
        raise ErroPlanner("Requisição inválida: envie o pedido de análise em JSON.")
    contexto = validar_contexto(dados.get("contexto"))
    telas, bytes_imagens = validar_telas(dados.get("telas"))
    return {"contexto": contexto, "telas": telas, "bytes_imagens": bytes_imagens}


def _sanitizar_historico(lista):
    """Histórico da conversa vindo do navegador: só {role: user|assistant,
    content: texto}. Passou de MAX_MSGS_HISTORICO_PLANNER mensagens ou de
    MAX_CHARS_HISTORICO caracteres, saem as mais antigas — menos as 2
    primeiras (o pedido original e a análise), que dão o contexto de tudo."""
    if not isinstance(lista, list):
        return []
    mensagens = []
    for item in lista:
        if not isinstance(item, dict):
            continue
        papel = _texto(item.get("role"), 20, linha_unica=True).lower()
        if papel not in ("user", "assistant"):
            continue
        conteudo = item.get("content")
        if isinstance(conteudo, list):
            # partes no formato OpenAI: fica só o texto (imagem não é reenviada)
            conteudo = "\n\n".join(str(p.get("text") or "") for p in conteudo
                                   if isinstance(p, dict) and p.get("type") == "text")
        if not isinstance(conteudo, str) or not conteudo.strip():
            continue
        mensagens.append({"role": papel,
                          "content": sem_surrogates(conteudo[:MAX_CHARS_MSG_HISTORICO])})
    if len(mensagens) > MAX_MSGS_HISTORICO_PLANNER:
        mensagens = mensagens[:2] + mensagens[-(MAX_MSGS_HISTORICO_PLANNER - 2):]
    total = sum(len(m["content"]) for m in mensagens)
    while total > MAX_CHARS_HISTORICO and len(mensagens) > 2:
        total -= len(mensagens.pop(2)["content"])
    return mensagens


def validar_pergunta(dados):
    """Pergunta de acompanhamento (modo "pergunta"): {"texto", "historico"}."""
    if not isinstance(dados, dict):
        raise ErroPlanner("Requisição inválida: envie a pergunta em JSON.")
    texto = _texto(dados.get("texto"))
    if not texto:
        raise ErroPlanner("Digite a pergunta de acompanhamento.")
    if len(texto) > MAX_PERGUNTA:
        raise ErroPlanner("A pergunta tem {} caracteres; o máximo é {}.".format(
            _fmt_int(len(texto)), _fmt_int(MAX_PERGUNTA)))
    return {"texto": texto, "historico": _sanitizar_historico(dados.get("historico"))}


# ---- Mensagem para a IA (SPEC §10) -------------------------------------------

def _bloco_codigo(texto):
    """Texto dentro de um bloco ``` — com cerca maior se o texto já tiver
    crases (um mapa de métricas colado do Confluence, por exemplo)."""
    maior = max([len(m) for m in re.findall(r"`+", texto)] or [0])
    cerca = "`" * max(3, maior + 1)
    return "{}\n{}\n{}".format(cerca, texto, cerca)


def _descrever_origem(tela):
    if tela["origem"] == "figma":
        node_id = (tela.get("figma") or {}).get("node_id")
        return "Figma (frame {})".format(node_id) if node_id else "Figma"
    return tela["origem"]


def _percentual(fracao):
    return int(math.floor(fracao * 100 + 0.5))


def _linhas_tela(tela):
    """Linha(s) da tela na lista "Imagens enviadas"."""
    partes = tela["partes"]
    if not partes:
        dims = (" — {}×{} px".format(tela["largura"], tela["altura"])
                if tela["largura"] and tela["altura"] else "")
        return ['- Imagem {} — "{}" — origem: {}{} — SEM IMAGEM: só o inventário de camadas '
                'do Figma (a imagem não pôde ser baixada)'.format(
                    tela["indice"], tela["nome"], _descrever_origem(tela), dims)]
    if len(partes) == 1:
        parte = partes[0]
        return ['- Imagem {} — "{}" — origem: {} — {}×{} px (1 parte: {}×{} px)'.format(
            tela["indice"], tela["nome"], _descrever_origem(tela),
            tela["largura"], tela["altura"], parte["largura"], parte["altura"])]
    linhas = ['- Imagem {} — "{}" — origem: {} — dividida em {} partes:'.format(
        tela["indice"], tela["nome"], _descrever_origem(tela), len(partes))]
    for parte in partes:
        recorte = parte["recorte"]
        linhas.append("  - parte {} de {}: {}×{} px — faixa de {}% a {}% da altura da "
                      "tela".format(parte["parte"], len(partes), parte["largura"],
                                    parte["altura"], _percentual(recorte["y"]),
                                    _percentual(recorte["y"] + recorte["altura"])))
    return linhas


def _linha_inventario(item):
    """F1 | INSTANCE "Button/Primary" | texto: "Solicitar" | interativo | x=0.053 ..."""
    pedacos = [item["ref"]]
    descricao = []
    if item["tipo"]:
        descricao.append(item["tipo"])
    if item["nome"]:
        descricao.append('"{}"'.format(item["nome"]))
    if descricao:
        pedacos.append(" ".join(descricao))
    if item["texto"]:
        pedacos.append('texto: "{}"'.format(item["texto"]))
    if item["interativo"]:
        pedacos.append("interativo")
    caixa = item["bbox"]
    if caixa:
        pedacos.append("x={:.3f} y={:.3f} l={:.3f} a={:.3f}".format(
            caixa["x"], caixa["y"], caixa["largura"], caixa["altura"]))
    return " | ".join(pedacos)


def montar_texto_contexto(contexto, telas):
    """Texto do pedido (1ª parte da mensagem; também é o que fica no
    histórico): contexto do formulário, lista das imagens e inventário de
    camadas do Figma de cada tela que tiver."""
    def valor(campo):
        return contexto.get(campo) or NAO_INFORMADO

    mapa = contexto.get("mapa_metricas") or ""
    linhas = [
        "## Pedido de análise de mensuração",
        "**Pergunta de negócio:** " + contexto["pergunta"],
    ]
    # Produto, jornada, telas vizinhas e observações não estão mais no
    # formulário (a IA confirma nas dúvidas): só entram quando vierem
    # preenchidos (análises antigas reabertas com "Duplicar e editar").
    for campo, rotulo in (("produto", "Produto"), ("jornada", "Jornada"),
                          ("tela", "Nome da tela")):
        if contexto.get(campo):
            linhas.append("**{}:** {}".format(rotulo, contexto[campo]))
    linhas.extend([
        "**Canal:** " + (contexto.get("canal") or CANAL_NAO_INFORMADO),
        "**Plataforma:** " + valor("plataforma"),
    ])
    for campo, rotulo in (("tela_anterior", "Tela anterior"), ("tela_seguinte", "Próxima tela"),
                          ("observacoes", "Observações")):
        if contexto.get(campo):
            linhas.append("**{}:** {}".format(rotulo, contexto[campo]))
    linhas.extend([
        "**Mapa de métricas existente:**",
        _bloco_codigo(mapa) if mapa else NAO_INFORMADO,
    ])
    esclarecimentos = contexto.get("esclarecimentos") or []
    if esclarecimentos:
        linhas.extend(["", "## Esclarecimentos antes da análise",
                       "Dúvidas que você levantou ao ver as telas e o que o usuário respondeu. "
                       "Trate as respostas como fatos confirmados. Na seção \"Premissas e "
                       "dúvidas\", separe o que o usuário confirmou do que foi suposto, e não "
                       "repita perguntas já respondidas."])
        for n, item in enumerate(esclarecimentos, 1):
            linhas.append("{}. **{}**".format(n, item["pergunta"]))
            if item["resposta"]:
                linhas.append("   - Resposta do usuário: " + item["resposta"].replace("\n", " "))
            else:
                linhas.append("   - Sem resposta. Premissa assumida: " +
                              (item["premissa"] or "use o seu melhor julgamento e registre-o"))
    linhas.extend(["", "## Imagens enviadas (ordem da jornada)"])
    for tela in telas:
        linhas.extend(_linhas_tela(tela))
    sem_imagem = [str(t["indice"]) for t in telas if not t["partes"]]
    if sem_imagem:
        linhas.extend([
            "",
            "Imagens sem arquivo de imagem ({}): analise pelo inventário de camadas abaixo e, "
            "na marcação, informe o figma_ref do elemento (a ferramenta usa as coordenadas do "
            "inventário). Deixe claro o que não pôde ser confirmado visualmente.".format(
                ", ".join(sem_imagem)),
        ])
    for tela in telas:
        inventario = (tela.get("figma") or {}).get("inventario") or []
        if not inventario:
            continue
        linhas.extend([
            "",
            '## Inventário de camadas do Figma — Imagem {} "{}"'.format(tela["indice"], tela["nome"]),
            "Coordenadas normalizadas (0 a 1) em relação à TELA INTEIRA (não à parte).",
        ])
        linhas.extend(_linha_inventario(item) for item in inventario)
    return "\n".join(linhas)


def _rotulo_parte(tela, parte, total):
    """Texto que vai logo antes de cada imagem."""
    dims = "{}×{} px".format(parte["largura"], parte["altura"])
    if parte.get("faixa"):
        dims += ", com faixa de identificação de {} px no topo".format(parte["faixa"])
    if total == 1:
        return 'Imagem {} ("{}") — {}'.format(tela["indice"], tela["nome"], dims)
    return 'Imagem {} — parte {} de {} ("{}") — {}'.format(
        tela["indice"], parte["parte"], total, tela["nome"], dims)


def montar_mensagem_analise(contexto, telas, detalhe):
    """Conteúdo da mensagem do usuário no modo análise (lista de partes no
    formato OpenAI): 1º o texto do contexto; depois, para cada tela/parte, um
    rótulo curto seguido da imagem. `detalhe` None omite o campo "detail".
    Devolve (conteudo, texto_contexto, user_content) — user_content é a
    versão só-texto que o navegador guarda no histórico."""
    texto = montar_texto_contexto(contexto, telas)
    if not any(tela["partes"] for tela in telas):
        # Só telas "só camadas": vai como texto puro (nada multimodal a enviar).
        return texto, texto, texto
    conteudo = [{"type": "text", "text": texto}]
    for tela in telas:
        total = len(tela["partes"])
        for parte in tela["partes"]:
            conteudo.append({"type": "text", "text": _rotulo_parte(tela, parte, total)})
            imagem = {"url": "data:{};base64,{}".format(parte["mime"], parte["base64"])}
            if detalhe:
                imagem["detail"] = detalhe
            conteudo.append({"type": "image_url", "image_url": imagem})
    return conteudo, texto, texto + "\n\n" + NOTA_IMAGENS_HISTORICO


def caracteres_de_texto(conteudo):
    """Caracteres de TEXTO de um content (string ou lista de partes OpenAI)."""
    if isinstance(conteudo, str):
        return len(conteudo)
    return sum(len(p.get("text") or "") for p in conteudo or []
               if isinstance(p, dict) and p.get("type") == "text")


def estimar_tokens_imagem(largura, altura, detalhe):
    """Tokens de UMA imagem para o gpt-5.4 (doc OpenAI, out/2026): a imagem é
    reduzida para caber no lado máximo do nível de detalhe e coberta por
    "patches" de 32x32 px, com teto de patches por nível; tokens = patches x
    1,2 (arredondado para cima). Só estimativa: vale quando a API não devolve
    o uso real."""
    nivel = (detalhe or "auto").strip().lower()
    if nivel == "low":
        lado_max, teto = 2048, 6144
    elif nivel == "original":
        lado_max, teto = 6000, 10000
    else:                                   # "high" e "auto" (padrão)
        lado_max, teto = 2048, 2500
    largura, altura = max(1, int(largura)), max(1, int(altura))
    maior = max(largura, altura)
    if maior > lado_max:
        fator = float(lado_max) / maior
        largura, altura = max(1, int(largura * fator)), max(1, int(altura * fator))
    patches = int(math.ceil(largura / 32.0)) * int(math.ceil(altura / 32.0))
    # x1,2 em inteiros (12/10) para não sofrer com arredondamento de float
    return (min(patches, teto) * 12 + 9) // 10


def estimar_tokens_telas(telas, detalhe):
    return sum(estimar_tokens_imagem(p["largura"], p["altura"], detalhe)
               for t in telas for p in t["partes"])


# ---- Chamada à IA ------------------------------------------------------------
# Recusa de imagem pela rota do modelo: erro 4xx/validação do conteúdo. Erros
# 5xx, tempo esgotado e falha de conexão NÃO são recusa (refazer sem imagem
# não resolveria e ainda gastaria outra chamada).
_RE_RECUSA_IMAGEM = re.compile(
    r"http 4\d\d|image|imagem|multimodal|vision|image_url|content|unsupported|invalid")
_RE_ERRO_NAO_RECUSA = re.compile(
    r"http 5\d\d|tempo esgotado|timeout|timed out|time-out|n[aã]o consegui conectar|"
    r"consultar o status|falha no login|"
    # 4xx que não falam do conteúdo: limite (429), acesso (401/403), rota (404)...
    r"http (?:401|403|404|408|409|429)\b")


def eh_recusa_de_imagem(erro):
    """True se o erro da Bridge indica que o CONTEÚDO (imagem) foi recusado."""
    if not isinstance(erro, RuntimeError):
        return False
    texto = str(erro).lower()
    if _RE_ERRO_NAO_RECUSA.search(texto):
        return False
    return bool(_RE_RECUSA_IMAGEM.search(texto))


def _gerar(mensagens, system_prompt, tokens_extra):
    # PLANNER_REASONING_EFFORT = None no config quer dizer "desligado"; para a
    # bridge, None é "use o do auditor" — "" é o que desliga de fato.
    esforco = PLANNER_REASONING_EFFORT or ""
    texto, uso = bridge.gerar_texto(
        mensagens, system_prompt, modelo=PLANNER_MODELO_LLM,
        max_tokens=PLANNER_MAX_TOKENS_RESPOSTA, reasoning_effort=esforco,
        rotulo="planner", tokens_extra_estimados=tokens_extra)
    texto = texto if isinstance(texto, str) else str(texto or "")
    if not texto.strip():
        raise ErroPlanner("A IA respondeu vazio. Tente de novo em instantes.", 502)
    return texto, (uso if isinstance(uso, dict) else {})


def _falha_ia(erro):
    return ErroPlanner("Não consegui concluir a análise com a IA: {}".format(
        _detalhe_erro(erro)), 502)


# ---- Bloco de coordenadas (```marcacao) ----------------------------------------
_RE_ABRE_MARCACAO_LINHA = re.compile(r"(?m)^[ \t]*(`{3,}|~{3,})[ \t]*marca[cç][aã]o\b",
                                     re.IGNORECASE)
_RE_ABRE_MARCACAO = re.compile(r"(`{3,}|~{3,})[ \t]*marca[cç][aã]o\b", re.IGNORECASE)
_RE_ABRE_JSON = re.compile(r"(?m)^[ \t]*(`{3,}|~{3,})[ \t]*json[c5]?\b", re.IGNORECASE)
_RE_TEM_COORDENADAS = re.compile(r"elementos|elements|bbox_normalizada|bbox_px", re.IGNORECASE)
_RE_LINHA_CERCA = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
_ASPAS_TIPOGRAFICAS = {"“": '"', "”": '"', "„": '"', "‟": '"', "″": '"'}
_LITERAIS_PYTHON = {"True": "true", "False": "false", "None": "null"}


def _blocos_cercados(texto, abertura):
    """Blocos de código cuja abertura casa com a regex: [(inicio, fim, corpo)].
    Bloco sem fechamento vai até o fim do texto (resposta cortada pelo limite
    de tokens): o JSON pode estar incompleto, mas ainda sai do texto exibido."""
    blocos, pos = [], 0
    while True:
        achado = abertura.search(texto, pos)
        if not achado:
            return blocos
        cerca = achado.group(1)
        fecha = texto.find(cerca, achado.end())
        if fecha < 0:
            blocos.append((achado.start(), len(texto), texto[achado.end():]))
            return blocos
        fim = fecha + len(cerca)
        while fim < len(texto) and texto[fim] == cerca[0]:
            fim += 1            # cerca de fechamento maior que a de abertura
        blocos.append((achado.start(), fim, texto[achado.end():fecha]))
        pos = fim


def _remover_trechos(texto, trechos):
    pedacos, pos = [], 0
    for inicio, fim, _corpo in sorted(trechos):
        pedacos.append(texto[pos:inicio])
        pos = max(pos, fim)
    pedacos.append(texto[pos:])
    return "".join(pedacos)


def _limpar_cercas(texto):
    """Tira blocos de código vazios (``` sozinhos que sobram ao remover a
    marcação) e linhas em branco repetidas fora dos blocos."""
    saida = []
    abertura = None           # (posição em saida, caractere da cerca, tamanho)
    for linha in texto.split("\n"):
        cerca = _RE_LINHA_CERCA.match(linha)
        if abertura is None:
            if cerca and not (cerca.group(1)[0] == "`" and "`" in cerca.group(2)):
                abertura = (len(saida), cerca.group(1)[0], len(cerca.group(1)))
            elif not linha.strip() and saida and not saida[-1].strip():
                continue      # linha em branco repetida
            saida.append(linha)
            continue
        if (cerca and cerca.group(1)[0] == abertura[1]
                and len(cerca.group(1)) >= abertura[2] and not cerca.group(2).strip()):
            if all(not l.strip() for l in saida[abertura[0] + 1:]):
                del saida[abertura[0]:]           # bloco vazio: some inteiro
                while len(saida) >= 2 and not saida[-1].strip() and not saida[-2].strip():
                    saida.pop()
            else:
                saida.append(linha)
            abertura = None
            continue
        saida.append(linha)
    if abertura is not None and all(not l.strip() for l in saida[abertura[0] + 1:]):
        del saida[abertura[0]:]                   # cerca aberta no fim, sem conteúdo
    return "\n".join(saida).strip()


def _varrer_fora_de_strings(texto, tratar):
    """Percorre o JSON e chama tratar(texto, i) em cada posição FORA de
    strings; tratar devolve (trecho_para_saida, proximo_i) ou None (copia o
    caractere). Mexer em comentário/vírgula/literal sem olhar as strings
    estragaria textos como "https://..." ou "a, b]"."""
    saida, i, n, em_string = [], 0, len(texto), False
    while i < n:
        c = texto[i]
        if em_string:
            saida.append(c)
            if c == "\\" and i + 1 < n:
                saida.append(texto[i + 1])
                i += 2
                continue
            if c == '"':
                em_string = False
            i += 1
            continue
        if c == '"':
            em_string = True
            saida.append(c)
            i += 1
            continue
        resultado = tratar(texto, i)
        if resultado is None:
            saida.append(c)
            i += 1
        else:
            trecho, i = resultado
            saida.append(trecho)
    return "".join(saida)


def _tratar_comentario(texto, i):
    if texto.startswith("//", i):
        fim = texto.find("\n", i)
        return "", (len(texto) if fim < 0 else fim)
    if texto.startswith("/*", i):
        fim = texto.find("*/", i + 2)
        return "", (len(texto) if fim < 0 else fim + 2)
    return None


def _tratar_virgula_e_literal(texto, i):
    c = texto[i]
    if c == ",":
        j = i + 1
        while j < len(texto) and texto[j] in " \t\r\n":
            j += 1
        if j >= len(texto) or texto[j] in "}]":
            return "", i + 1          # vírgula sobrando: descarta
        return None
    if c.isalpha():
        j = i
        while j < len(texto) and (texto[j].isalnum() or texto[j] == "_"):
            j += 1
        palavra = texto[i:j]
        return _LITERAIS_PYTHON.get(palavra, palavra), j
    return None


def _recortar_json(texto):
    """Do 1º '{' ou '[' até o último '}' ou ']' (tira texto solto em volta)."""
    inicios = [p for p in (texto.find("{"), texto.find("[")) if p >= 0]
    fim = max(texto.rfind("}"), texto.rfind("]"))
    if not inicios or fim <= min(inicios):
        return texto
    return texto[min(inicios):fim + 1]


def _carregar_json_tolerante(corpo):
    """json.loads com tolerância aos deslizes comuns de modelo: texto em volta
    do JSON, comentários //, vírgula sobrando, True/False/None e, por último,
    aspas tipográficas (“ ”) como delimitador. Devolve (objeto, None) ou
    (None, motivo)."""
    texto = (corpo or "").strip()
    if not texto:
        return None, "O bloco de coordenadas (marcacao) veio vazio."
    recortado = _recortar_json(texto)
    consertado = _varrer_fora_de_strings(
        _varrer_fora_de_strings(recortado, _tratar_comentario), _tratar_virgula_e_literal)
    aspas = recortado
    for de, para in _ASPAS_TIPOGRAFICAS.items():
        aspas = aspas.replace(de, para)
    aspas = _varrer_fora_de_strings(
        _varrer_fora_de_strings(aspas, _tratar_comentario), _tratar_virgula_e_literal)
    primeiro_erro, vistos = None, set()
    for candidato in (texto, recortado, consertado, aspas):
        if candidato in vistos:
            continue
        vistos.add(candidato)
        try:
            return json.loads(candidato), None
        except (ValueError, RecursionError) as e:
            primeiro_erro = primeiro_erro or e
    return None, "O bloco de coordenadas (marcacao) não é um JSON válido: {}.".format(
        str(primeiro_erro)[:160])


def _lista_de_elementos(objeto, profundidade=0):
    if isinstance(objeto, list):
        return objeto
    if not isinstance(objeto, dict) or profundidade > 2:
        return None
    for chave in ("elementos", "elements", "itens", "items"):
        if isinstance(objeto.get(chave), list):
            return objeto[chave]
    for chave in ("marcacao", "marcação", "marking"):
        if isinstance(objeto.get(chave), (dict, list)):
            return _lista_de_elementos(objeto[chave], profundidade + 1)
    if any(k in objeto for k in ("bbox_px", "bbox_normalizada", "figma_ref")):
        return [objeto]             # um elemento só, sem a lista em volta
    return None


def _primeiro_numero(dicionario, chaves):
    for chave in chaves:
        if chave in dicionario:
            numero = _numero(dicionario[chave])
            if numero is not None:
                return numero
    return None


def _bbox(valor):
    """Caixa {x, y, largura, altura} (floats) a partir das variações que os
    modelos devolvem: width/height/w/h, cantos x2/y2, lista [x, y, l, a].
    Porcentagem (ex.: 45.5) fica como veio — o navegador trata. None se não
    for uma caixa utilizável (falta número ou tamanho zero)."""
    if isinstance(valor, (list, tuple)):
        if len(valor) != 4:
            return None
        x, y, largura, altura = (_numero(v) for v in valor)
    elif isinstance(valor, dict):
        d = {str(k).strip().lower(): v for k, v in valor.items()}
        x = _primeiro_numero(d, ("x", "left", "esquerda", "x0", "x1"))
        y = _primeiro_numero(d, ("y", "top", "topo", "y0", "y1"))
        largura = _primeiro_numero(d, ("largura", "width", "w", "l"))
        altura = _primeiro_numero(d, ("altura", "height", "h", "a"))
        if largura is None and x is not None:
            x2 = _primeiro_numero(d, ("x2", "right", "direita"))
            largura = None if x2 is None else x2 - x
        if altura is None and y is not None:
            y2 = _primeiro_numero(d, ("y2", "bottom", "base"))
            altura = None if y2 is None else y2 - y
    else:
        return None
    if None in (x, y, largura, altura):
        return None
    if x < 0:                       # caixa vazando à esquerda/acima: recorta
        largura, x = largura + x, 0.0
    if y < 0:
        altura, y = altura + y, 0.0
    if largura <= 0 or altura <= 0:
        return None                 # ex.: o modelo copiou o exemplo com zeros
    return {"x": round(x, 4), "y": round(y, 4),
            "largura": round(largura, 4), "altura": round(altura, 4)}


def _palavras(valor):
    """"OPCIONAL/DIAGNÓSTICO" -> "OPCIONAL DIAGNOSTICO" (para comparar)."""
    texto = _sem_acento(_texto(valor, 80, linha_unica=True)).upper()
    return re.sub(r"[^A-Z]+", " ", texto).strip()


def _classe_por_palavras(valor):
    t = _palavras(valor)
    if not t:
        return None
    if "NAO TAGUE" in t or "DESCART" in t or "IGNORAR" in t:
        return "NAO_TAGUEAR"
    if "CONTEXTO" in t or "VALIDAR" in t:
        return "NECESSITA_CONTEXTO"
    if "OPCION" in t or "DIAGN" in t:
        return "OPCIONAL"
    if "OBRIGAT" in t:
        return "OBRIGATORIO"
    return None


def _classificar(classificacao, decisao):
    """OBRIGATORIO | OPCIONAL | NAO_TAGUEAR | NECESSITA_CONTEXTO. Sem
    classificação reconhecível, decide pela decisão; sem nenhuma das duas,
    fica "a validar" (só aparece sob demanda — melhor que pintar de vermelho
    um elemento que o modelo não classificou)."""
    classe = _classe_por_palavras(classificacao) or _classe_por_palavras(decisao)
    if classe:
        return classe
    if "TAGUEAR" in _palavras(decisao):
        return "OBRIGATORIO"
    return "NECESSITA_CONTEXTO"


_DECISAO_POR_CLASSE = {"OBRIGATORIO": "TAGUEAR", "OPCIONAL": "OPCIONAL",
                       "NAO_TAGUEAR": "NÃO TAGUEAR", "NECESSITA_CONTEXTO": "A VALIDAR"}


def _decisao(valor, classe):
    t = _palavras(valor)
    if "NAO TAGUE" in t or "DESCART" in t:
        return "NÃO TAGUEAR"
    if "VALIDAR" in t or "CONTEXTO" in t:
        return "A VALIDAR"
    if "OPCION" in t or "DIAGN" in t:
        return "OPCIONAL"
    if "TAGUEAR" in t or "OBRIGAT" in t:
        return "TAGUEAR"
    return _DECISAO_POR_CLASSE[classe]


def _ref_figma(valor):
    """Código da camada no inventário ("F12") ou None."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return "F{}".format(valor) if valor > 0 else None
    achado = re.match(r"^F?(\d{1,6})$", _texto(valor, 20, linha_unica=True), re.IGNORECASE)
    return "F{}".format(int(achado.group(1))) if achado else None


def _indice(valor):
    """imagem/parte: inteiro >= 1. Aceita 2, 2.0, "2" e até "Imagem 2"."""
    if isinstance(valor, str):
        achado = re.search(r"\d+", valor)
        valor = achado.group(0) if achado else None
    numero = _inteiro(valor, 1, 999)
    return numero if numero is not None else 1


def _primeiro(dicionario, chaves):
    for chave in chaves:
        valor = dicionario.get(chave)
        if valor not in (None, ""):
            return valor
    return None


def _normalizar_elemento(bruto, posicao):
    """Elemento no formato do SPEC §5.4, ou None se não tiver como desenhar
    nem referenciar (sem caixa válida e sem figma_ref)."""
    if not isinstance(bruto, dict):
        return None
    d = {str(k).strip().lower(): v for k, v in bruto.items()}
    ident = _texto(d.get("id"), 20, linha_unica=True) or "T{}".format(posicao)
    texto = _texto(_primeiro(d, ("texto", "text", "elemento", "label", "nome")), 200,
                   linha_unica=True)
    bruto_classe = _primeiro(d, ("classificacao", "classificação", "classification"))
    bruto_decisao = _primeiro(d, ("decisao", "decisão", "decision"))
    classe = _classificar(bruto_classe, bruto_decisao)
    decisao = _decisao(bruto_decisao, classe)
    figma_ref = _ref_figma(_primeiro(d, ("figma_ref", "ref_figma", "ref")))
    bbox_px = _bbox(_primeiro(d, ("bbox_px", "bbox_pixels", "bboxpx")))
    bbox_norm = _bbox(_primeiro(d, ("bbox_normalizada", "bbox_normalized", "bbox_norm",
                                    "bboxnormalizada")))
    if bbox_px is None and bbox_norm is None:
        caixa = _bbox(d.get("bbox"))
        if caixa and max(caixa.values()) <= 1.5:
            bbox_norm = caixa
        elif caixa:
            bbox_px = caixa
    if bbox_px is None and bbox_norm is None and not figma_ref:
        return None
    legenda = (_texto(_primeiro(d, ("legenda", "legend", "caption")), 200, linha_unica=True)
               or "{} — {}: {}".format(ident, decisao, texto or "elemento"))
    return {
        "id": ident,
        "imagem": _indice(_primeiro(d, ("imagem", "image"))),
        "parte": _indice(_primeiro(d, ("parte", "part"))),
        "texto": texto,
        "classificacao": classe,
        "decisao": decisao,
        "legenda": legenda,
        "figma_ref": figma_ref,
        "bbox_px": bbox_px,
        "bbox_normalizada": bbox_norm,
    }


def _eh_sem_elementos(bruto):
    """Item {"imagem": 3, "sem_elementos": true, "motivo": "..."}: a imagem foi
    vista e não tem o que marcar (não é esquecimento)."""
    if not isinstance(bruto, dict):
        return False
    d = {str(k).strip().lower().replace(" ", "_"): v for k, v in bruto.items()}
    valor = d.get("sem_elementos", d.get("no_elements"))
    return valor is True or (isinstance(valor, str) and valor.strip().lower() in ("true", "sim"))


def _item_sem_elementos(bruto, imagem=None):
    d = {str(k).strip().lower(): v for k, v in bruto.items()} if isinstance(bruto, dict) else {}
    motivo = _texto(_primeiro(d, ("motivo", "reason", "justificativa")), 300, linha_unica=True)
    return {"imagem": imagem or _indice(_primeiro(d, ("imagem", "image"))),
            "motivo": motivo or "o modelo indicou que não há elementos a marcar nesta tela"}


def _normalizar_marcacao(objeto):
    """{"elementos": [...], "sem_elementos": [{"imagem", "motivo"}]} ou
    (None, motivo do erro). Um bloco só com "sem_elementos" é válido: a IA
    olhou as telas e disse por que não há o que marcar."""
    lista = _lista_de_elementos(objeto)
    sem = []
    if isinstance(objeto, dict):
        topo = objeto.get("sem_elementos")
        if isinstance(topo, list):
            sem.extend(_item_sem_elementos(b) for b in topo if isinstance(b, dict))
        elif _eh_sem_elementos(objeto) and not lista:
            sem.append(_item_sem_elementos(objeto))
    if lista is None and not sem:
        return None, "O bloco de coordenadas (marcacao) não traz a lista \"elementos\"."
    elementos = []
    for posicao, bruto in enumerate(lista or [], 1):
        if _eh_sem_elementos(bruto):
            sem.append(_item_sem_elementos(bruto))
            continue
        elemento = _normalizar_elemento(bruto, posicao)
        if elemento:
            elementos.append(elemento)
            if len(elementos) >= MAX_ELEMENTOS_MARCACAO:
                break
    if not elementos and not sem:
        return None, ("Nenhum elemento do bloco de coordenadas (marcacao) tem posição "
                      "válida nem código do Figma.")
    marcacao = {"elementos": elementos}
    if sem:
        marcacao["sem_elementos"] = sem
    return marcacao, None


def extrair_marcacao(texto):
    """Separa o bloco de coordenadas do texto da resposta.

    Procura blocos ```marcacao (sem diferenciar maiúsculas; "marcação" também
    vale); se não houver, usa o ÚLTIMO ```json que fale de "elementos" ou
    "bbox_normalizada". O(s) bloco(s) usado(s) saem do texto exibido. Com
    vários blocos ```marcacao vale o último que der certo (o modelo às vezes
    se corrige no fim).

    Devolve (texto_limpo, marcacao | None, erro | None):
      - marcacao = {"elementos": [...]} já normalizada (SPEC §5.4);
      - erro = motivo quando havia bloco mas ele não serviu. Sem bloco nenhum
        o erro é None (numa pergunta de acompanhamento isso é o normal)."""
    if not isinstance(texto, str):
        return "", None, None
    normalizado = texto.replace("\r\n", "\n").replace("\r", "\n")
    blocos = (_blocos_cercados(normalizado, _RE_ABRE_MARCACAO_LINHA)
              or _blocos_cercados(normalizado, _RE_ABRE_MARCACAO))
    if blocos:
        remover, candidatos = blocos, blocos[::-1]
    else:
        jsons = [b for b in _blocos_cercados(normalizado, _RE_ABRE_JSON)
                 if _RE_TEM_COORDENADAS.search(b[2])]
        remover = candidatos = jsons[-1:]
    limpo = _limpar_cercas(_remover_trechos(normalizado, remover))
    erro = None
    for _inicio, _fim, corpo in candidatos:
        objeto, motivo = _carregar_json_tolerante(corpo)
        if objeto is not None:
            marcacao, motivo = _normalizar_marcacao(objeto)
            if marcacao:
                return limpo, marcacao, None
        erro = erro or motivo
    return limpo, None, erro


def _ajustar_marcacao_as_telas(marcacao, telas):
    """Na análise o servidor sabe quantas imagens/partes foram enviadas:
    elemento numa imagem que não existe sai (não há onde desenhar); parte
    além da última vira a última (erro de contagem do modelo). Devolve
    (marcacao | None, quantos elementos saíram)."""
    if not marcacao:
        return marcacao, 0
    elementos, descartados = [], 0
    for elemento in marcacao["elementos"]:
        if elemento["imagem"] > len(telas):
            descartados += 1
            continue
        # Tela "só camadas" não tem partes: vale a parte 1 (a tela inteira).
        total = max(1, len(telas[elemento["imagem"] - 1]["partes"]))
        elemento["parte"] = min(elemento["parte"], total)
        elementos.append(elemento)
    com_elemento = {e["imagem"] for e in elementos}
    sem, vistas = [], set()
    for item in marcacao.get("sem_elementos") or []:
        n = item["imagem"]
        if n <= len(telas) and n not in com_elemento and n not in vistas:
            vistas.add(n)
            sem.append(item)
    if not elementos and not sem:
        return None, descartados
    ajustada = {"elementos": elementos}
    if sem:
        ajustada["sem_elementos"] = sem
    return ajustada, descartados


# ---- 2ª etapa: marcação tela a tela (config PLANNER_MARCACAO_*) ---------------

def _somar_usos(usos):
    """Soma o uso de tokens de várias chamadas. "estimativa" contamina o total
    (o painel mostra "~" quando parte do número é estimada)."""
    total = {"entrada": 0, "saida": 0, "total": 0, "fonte": "api"}
    fontes = set()
    for uso in usos:
        if not isinstance(uso, dict):
            continue
        for chave in ("entrada", "saida", "total"):
            total[chave] += int(uso.get(chave) or 0)
        fontes.add(uso.get("fonte") or "")
    fontes.discard("")
    if "estimativa" in fontes:
        total["fonte"] = "estimativa"
    elif fontes:
        total["fonte"] = sorted(fontes)[0]
    return total


def _texto_marcacao_tela(contexto, telas, indice, texto_analise, rascunho, nova_tentativa):
    """Texto da chamada de marcação da tela `indice` (1..N)."""
    tela = telas[indice - 1]
    linhas = [
        '## Marcação da Imagem {} de {}: "{}"'.format(indice, len(telas), tela["nome"]),
        "Esta mensagem traz só a Imagem {} ({}). Use \"imagem\": {} em todos os "
        "elementos.".format(indice, _descrever_partes(len(tela["partes"])), indice),
        "**Pergunta de negócio:** " + contexto["pergunta"],
        "**Canal:** " + (contexto.get("canal") or CANAL_NAO_INFORMADO),
    ]
    if nova_tentativa:
        linhas.append("Uma tentativa anterior não devolveu nenhum elemento para esta tela. "
                      "Confira de novo; se de fato não houver o que marcar, diga o motivo.")
    elementos = (rascunho or {}).get("elementos") or []
    linhas.extend(["", "## Elementos decididos pela análise (todas as telas)"])
    if elementos:
        for el in elementos:
            onde = "nesta tela" if el["imagem"] == indice else "a análise situou na Imagem {}".format(
                el["imagem"])
            linhas.append('- {} · {} · {} · "{}" · {} ({})'.format(
                el["id"], el["classificacao"], el["decisao"], el["texto"] or "-",
                el["legenda"], onde))
    else:
        linhas.append("(a análise não trouxe a lista de coordenadas; use os IDs das tabelas "
                      "do texto abaixo)")
    analise = texto_analise or ""
    if len(analise) > MAX_CHARS_ANALISE_MARCACAO:
        analise = analise[:MAX_CHARS_ANALISE_MARCACAO] + "\n[... texto cortado ...]"
    linhas.extend(["", "## Texto da análise (referência)", _bloco_codigo(analise)])
    inventario = (tela.get("figma") or {}).get("inventario") or []
    if inventario:
        linhas.extend([
            "", "## Inventário de camadas do Figma desta tela",
            "Coordenadas normalizadas (0 a 1) em relação à TELA INTEIRA (não à parte).",
        ])
        linhas.extend(_linha_inventario(item) for item in inventario)
    linhas.extend(["", "As imagens desta tela vêm a seguir."])
    return "\n".join(linhas)


def _descrever_partes(n):
    return "tela inteira" if n <= 1 else "{} partes".format(n)


def _marcar_tela(contexto, telas, indice, texto_analise, rascunho, nova_tentativa=False):
    """Uma chamada de marcação só da tela `indice`. Nunca levanta: devolve
    {"ok", "elementos", "motivo", "uso", "erro"}."""
    tela = telas[indice - 1]
    rotulo = "planner-marcacao-{}".format(indice)
    conteudo = [{"type": "text", "text": _texto_marcacao_tela(
        contexto, telas, indice, texto_analise, rascunho, nova_tentativa)}]
    total = len(tela["partes"])
    for parte in tela["partes"]:
        conteudo.append({"type": "text", "text": _rotulo_parte(tela, parte, total)})
        imagem = {"url": "data:{};base64,{}".format(parte["mime"], parte["base64"])}
        if PLANNER_DETALHE_IMAGEM:
            imagem["detail"] = PLANNER_DETALHE_IMAGEM
        conteudo.append({"type": "image_url", "image_url": imagem})
    extra = sum(estimar_tokens_imagem(p["largura"], p["altura"], PLANNER_DETALHE_IMAGEM)
                for p in tela["partes"])
    try:
        texto, uso = bridge.gerar_texto(
            [{"role": "user", "content": conteudo}], INSTRUCOES_MARCACAO,
            modelo=PLANNER_MODELO_LLM, max_tokens=PLANNER_MARCACAO_MAX_TOKENS,
            reasoning_effort=PLANNER_MARCACAO_REASONING_EFFORT or "", rotulo=rotulo,
            tokens_extra_estimados=extra)
    except Exception as e:
        _console("[planner] Marcação da Imagem {} falhou: {}".format(indice, _detalhe_erro(e)[:160]))
        return {"ok": False, "elementos": [], "motivo": None, "uso": None,
                "erro": _detalhe_erro(e)}
    _limpo, marcacao, erro = extrair_marcacao(texto if isinstance(texto, str) else str(texto or ""))
    if not marcacao:
        return {"ok": False, "elementos": [], "motivo": None, "uso": uso,
                "erro": erro or "sem bloco de coordenadas"}
    elementos = []
    for el in marcacao["elementos"]:
        el["imagem"] = indice                      # a chamada só viu esta tela
        el["parte"] = min(max(1, el["parte"]), max(1, total))
        elementos.append(el)
    motivo = None
    if not elementos:
        sem = marcacao.get("sem_elementos") or []
        motivo = sem[0]["motivo"] if sem else None
    return {"ok": bool(elementos or motivo), "elementos": elementos, "motivo": motivo,
            "uso": uso, "erro": None if (elementos or motivo) else "nenhum elemento devolvido"}


def _rodar_marcacoes(indices, *args, **kwargs):
    """Marca as telas `indices` em paralelo (até PLANNER_MARCACAO_PARALELAS)."""
    if not indices:
        return {}
    paralelas = max(1, min(int(PLANNER_MARCACAO_PARALELAS or 1), len(indices)))
    with ThreadPoolExecutor(max_workers=paralelas) as executor:
        futuros = {i: executor.submit(_marcar_tela, args[0], args[1], i, *args[2:], **kwargs)
                   for i in indices}
        return {i: f.result() for i, f in futuros.items()}


def marcar_tela_a_tela(contexto, telas, texto_analise, rascunho, info):
    """2ª etapa da análise. Parte da marcação da própria análise (`rascunho`,
    pode ser None) e, para cada tela com imagem:
      - PLANNER_MARCACAO_POR_IMAGEM: uma chamada só daquela tela, que substitui
        a marcação da análise para ela (se a chamada falhar, fica a da análise);
      - PLANNER_MARCACAO_REFAZER: tela que terminou sem elemento e sem motivo
        ganha mais uma tentativa.
    Devolve (marcacao | None, [usos]). Falhas aqui nunca derrubam a análise."""
    n = len(telas)
    por_tela = {i: [] for i in range(1, n + 1)}
    motivos = {}
    for el in (rascunho or {}).get("elementos") or []:
        por_tela[el["imagem"]].append(el)
    for item in (rascunho or {}).get("sem_elementos") or []:
        motivos[item["imagem"]] = item["motivo"]
    com_imagem = [i for i in range(1, n + 1) if telas[i - 1]["partes"]]
    usos, falhas = [], 0

    def aplicar(resultados):
        nonlocal falhas
        for i, r in resultados.items():
            if r["uso"]:
                usos.append(r["uso"])
            if not r["ok"]:
                falhas += 1
                continue
            por_tela[i] = r["elementos"]
            if r["elementos"]:
                motivos.pop(i, None)
            else:
                motivos[i] = r["motivo"]

    etapa2 = com_imagem if PLANNER_MARCACAO_POR_IMAGEM else []
    if etapa2:
        _console("[planner] Marcação tela a tela: {} chamada(s), até {} ao mesmo tempo.".format(
            len(etapa2), max(1, int(PLANNER_MARCACAO_PARALELAS or 1))))
        aplicar(_rodar_marcacoes(etapa2, contexto, telas, texto_analise, rascunho))
    refazer = ([i for i in com_imagem if not por_tela[i] and i not in motivos]
               if PLANNER_MARCACAO_REFAZER else [])
    if refazer:
        _console("[planner] Imagem(ns) sem marcação: {}. Nova tentativa só delas.".format(
            ", ".join(str(i) for i in refazer)))
        aplicar(_rodar_marcacoes(refazer, contexto, telas, texto_analise, rascunho,
                                 nova_tentativa=True))
    info["marcacao_chamadas"] = len(etapa2) + len(refazer)
    info["marcacao_refeitas"] = len(refazer)
    info["marcacao_falhas"] = falhas

    elementos = [el for i in range(1, n + 1) for el in por_tela[i]][:MAX_ELEMENTOS_MARCACAO]
    sem = [{"imagem": i, "motivo": motivos[i]} for i in range(1, n + 1)
           if i in motivos and not por_tela[i]]
    if not elementos and not sem:
        return None, usos
    marcacao = {"elementos": elementos}
    if sem:
        marcacao["sem_elementos"] = sem
    return marcacao, usos


# ---- Trilha de auditoria (SPEC §6) -------------------------------------------

def _info_analise():
    """Campos do log de planner_analise que vêm do pedido (preenchidos aos
    poucos: um erro de validação no meio ainda registra o que já se sabe)."""
    return {"sessao": "", "modo": "", "telas": 0, "partes": 0, "origem": "",
            "canal": "", "plataforma": "", "produto_analisado": "", "jornada": "",
            "pergunta": "", "bytes_imagens": 0, "caracteres": 0, "sem_imagem": False,
            "telas_sem_imagem": 0,
            # marcação (só na análise): ver _registrar_analise
            "elementos_analise": "", "marcacao_descartados": 0, "marcacao_chamadas": 0,
            "marcacao_refeitas": 0, "marcacao_falhas": 0,
            # dúvidas da IA antes da análise (etapa "esclarecer")
            "duvidas": 0, "duvidas_respondidas": 0}


def _contagem_por_tela(marcacao, n_telas):
    """"3,0,5": elementos marcados em cada tela, na ordem (vazio sem telas)."""
    if not n_telas:
        return ""
    contagem = [0] * n_telas
    for el in (marcacao or {}).get("elementos") or []:
        if 1 <= el["imagem"] <= n_telas:
            contagem[el["imagem"] - 1] += 1
    return ",".join(str(c) for c in contagem)


def _origem_resumo(telas):
    origens = {t["origem"] for t in telas}
    if len(origens) == 1:
        return origens.pop()
    return "misto" if origens else ""


def _registrar_analise(usuario, ip, info, status, inicio, uso=None, marcacao=None,
                       estado=None, erro=None):
    campos = {"sessao": info["sessao"], "modo": info["modo"], "status": status}
    for chave in ("telas", "partes", "origem", "canal", "plataforma", "produto_analisado",
                  "jornada", "pergunta", "bytes_imagens", "caracteres"):
        campos[chave] = info[chave]
    if status == "ok":
        elementos = (marcacao or {}).get("elementos") or []
        campos["obrigatorios"] = sum(1 for e in elementos if e["classificacao"] == "OBRIGATORIO")
        campos["opcionais"] = sum(1 for e in elementos if e["classificacao"] == "OPCIONAL")
        campos["elementos"] = len(elementos)
        campos["marcacao"] = estado or "ausente"
        if info["modo"] == "analise" and info["telas"]:
            # Diagnóstico da marcação (por que uma tela ficou sem caixas):
            #   elementos_por_imagem  resultado final, tela a tela ("3,0,5")
            #   elementos_analise     o que a análise marcou antes da 2ª etapa
            #   imagens_sem_elementos telas que a IA disse não ter o que marcar
            #   imagens_vazias        telas sem caixas e sem motivo (falha)
            por_tela = _contagem_por_tela(marcacao, info["telas"])
            com_motivo = {s["imagem"] for s in (marcacao or {}).get("sem_elementos") or []}
            vazias = [i for i, c in enumerate(por_tela.split(","), 1)
                      if c == "0" and i not in com_motivo]
            campos["elementos_por_imagem"] = por_tela
            campos["elementos_analise"] = info["elementos_analise"]
            campos["imagens_sem_elementos"] = len(com_motivo)
            campos["imagens_vazias"] = len(vazias)
            for chave in ("marcacao_descartados", "marcacao_chamadas", "marcacao_refeitas",
                          "marcacao_falhas"):
                if info[chave]:
                    campos[chave] = info[chave]
    campos["sem_imagem"] = bool(info["sem_imagem"])
    if info["duvidas"]:
        campos["duvidas"] = info["duvidas"]
        campos["duvidas_respondidas"] = info["duvidas_respondidas"]
    if info["telas_sem_imagem"]:
        # Telas do Figma que vieram só com camadas (proxy barrou o PNG)
        campos["telas_sem_imagem"] = info["telas_sem_imagem"]
    campos["duracao_s"] = round(time.time() - inicio, 1)
    if uso:
        campos["tokens_entrada"] = uso.get("entrada", 0)
        campos["tokens_saida"] = uso.get("saida", 0)
        campos["tokens_total"] = uso.get("total", 0)
        campos["tokens_fonte"] = uso.get("fonte", "")
    if erro:
        campos["erro"] = str(erro)[:200]
    atividade.registrar("planner_analise", usuario, ip, **campos)


# ---- Rota: POST /api/planner/analisar ----------------------------------------

def _aviso_so_camadas(telas):
    """Aviso ao usuário: telas do Figma que chegaram sem imagem (o proxy barrou
    o PNG) e foram analisadas só pelas camadas."""
    nomes = ['"{}"'.format(t["nome"]) for t in telas]
    if len(nomes) == 1:
        return ("A tela {} veio do Figma sem imagem: foi analisada só pelas camadas do "
                "arquivo (textos e posições exatos), e a marcação aparece sobre uma prévia "
                "desenhada a partir delas.".format(nomes[0]))
    return ("As telas {} e {} vieram do Figma sem imagem: foram analisadas só pelas camadas "
            "do arquivo (textos e posições exatos), e a marcação aparece sobre prévias "
            "desenhadas a partir delas.".format(", ".join(nomes[:-1]), nomes[-1]))


def _fazer_analise(dados, info):
    """Análise nova. Devolve (corpo, uso, estado_marcacao)."""
    contexto = validar_contexto(dados.get("contexto"))
    info.update(canal=contexto["canal"], plataforma=contexto["plataforma"],
                produto_analisado=contexto["produto"], jornada=contexto["jornada"],
                pergunta=contexto["pergunta"][:300],
                duvidas=len(contexto["esclarecimentos"]),
                duvidas_respondidas=sum(1 for e in contexto["esclarecimentos"] if e["resposta"]))
    telas, bytes_imagens = validar_telas(dados.get("telas"))
    so_camadas = [t for t in telas if not t["partes"]]
    info.update(telas=len(telas), partes=sum(len(t["partes"]) for t in telas),
                origem=_origem_resumo(telas), bytes_imagens=bytes_imagens,
                telas_sem_imagem=len(so_camadas))

    system_prompt = obter_system_prompt()
    conteudo, texto_contexto, user_content = montar_mensagem_analise(
        contexto, telas, PLANNER_DETALHE_IMAGEM)
    info["caracteres"] = caracteres_de_texto(conteudo)
    _console("[planner] Analisando {} tela(s) / {} parte(s) de imagem ({} MB) - sessão {}".format(
        info["telas"], info["partes"], _fmt_mb(bytes_imagens), info["sessao"] or "-"))

    aviso = _aviso_so_camadas(so_camadas) if so_camadas else None
    try:
        texto, uso = _gerar([{"role": "user", "content": conteudo}], system_prompt,
                            estimar_tokens_telas(telas, PLANNER_DETALHE_IMAGEM))
    except ErroPlanner:
        raise
    except Exception as e:
        # Sem nenhuma imagem na mensagem não há "recusa de imagem" a contornar.
        if not info["partes"] or not eh_recusa_de_imagem(e):
            raise _falha_ia(e)
        tem_inventario = any((t.get("figma") or {}).get("inventario") for t in telas)
        if not tem_inventario:
            raise ErroPlanner(
                "A rota do modelo de IA não aceitou as imagens. Traga as telas pela URL do "
                "Figma: com as camadas do arquivo dá para analisar pelos textos e posições, "
                "mesmo sem a imagem. Se continuar, avise o time responsável (o modelo do Tag "
                "Planner precisa aceitar imagem). Detalhe: {}".format(_detalhe_erro(e)), 502)
        if not PLANNER_SEM_IMAGEM_SE_RECUSAR:
            raise ErroPlanner(
                "A rota do modelo de IA não aceitou as imagens e a análise só com as camadas "
                "do Figma está desligada (PLANNER_SEM_IMAGEM_SE_RECUSAR). Avise o time "
                "responsável. Detalhe: {}".format(_detalhe_erro(e)), 502)
        _console("[planner] A rota do modelo recusou as imagens; refazendo só com as camadas "
                 "do Figma ({})".format(_detalhe_erro(e)[:160]))
        info["sem_imagem"] = True
        texto_sem_imagem = texto_contexto + "\n\n" + AVISO_SEM_IMAGEM_MODELO
        info["caracteres"] = len(texto_sem_imagem)
        try:
            texto, uso = _gerar([{"role": "user", "content": texto_sem_imagem}],
                                system_prompt, 0)
        except ErroPlanner:
            raise
        except Exception as e2:
            raise ErroPlanner("A rota do modelo não aceitou as imagens e a nova tentativa só "
                              "com as camadas do Figma também falhou: {}".format(
                                  _detalhe_erro(e2)), 502)
        user_content = texto_sem_imagem
        aviso = AVISO_SEM_IMAGEM_USUARIO

    texto_limpo, marcacao, erro_marcacao = extrair_marcacao(texto)
    if marcacao:
        marcacao, info["marcacao_descartados"] = _ajustar_marcacao_as_telas(marcacao, telas)
        if marcacao is None:
            erro_marcacao = ("As coordenadas devolvidas apontam para imagens que não foram "
                             "enviadas; a marcação visual não pôde ser gerada.")
    # Contagem por tela da marcação da análise (antes da 2ª etapa), para o log
    info["elementos_analise"] = _contagem_por_tela(marcacao, len(telas))
    if (PLANNER_MARCACAO_POR_IMAGEM or PLANNER_MARCACAO_REFAZER) and not info["sem_imagem"]:
        final, usos = marcar_tela_a_tela(contexto, telas, texto_limpo, marcacao, info)
        if usos:
            uso = _somar_usos([uso] + usos)
        if final:
            marcacao, erro_marcacao = final, None
    estado = "ok" if marcacao else ("invalida" if erro_marcacao else "ausente")
    if estado == "ausente":
        erro_marcacao = MSG_SEM_BLOCO_MARCACAO
    corpo = {"resposta": texto_limpo, "resposta_modelo": texto, "user_content": user_content,
             "marcacao": marcacao, "marcacao_erro": erro_marcacao, "aviso": aviso}
    return corpo, uso, estado


def _responder_pergunta(dados, info):
    """Pergunta de acompanhamento (só texto + histórico, sem imagens).
    Devolve (corpo, uso, estado_marcacao)."""
    pedido = validar_pergunta(dados)
    info["pergunta"] = pedido["texto"][:300]
    info["caracteres"] = len(pedido["texto"])
    system_prompt = obter_system_prompt()
    mensagens = pedido["historico"] + [{"role": "user", "content": pedido["texto"]}]
    _console("[planner] Pergunta de acompanhamento ({} caracteres, {} de histórico) - "
             "sessão {}".format(len(pedido["texto"]), len(pedido["historico"]),
                                info["sessao"] or "-"))
    try:
        texto, uso = _gerar(mensagens, system_prompt, 0)
    except ErroPlanner:
        raise
    except Exception as e:
        raise _falha_ia(e)
    # Sem bloco novo a marcação anterior continua valendo: não é erro.
    texto_limpo, marcacao, erro_marcacao = extrair_marcacao(texto)
    estado = "ok" if marcacao else ("invalida" if erro_marcacao else "ausente")
    corpo = {"resposta": texto_limpo, "resposta_modelo": texto,
             "user_content": pedido["texto"], "marcacao": marcacao,
             "marcacao_erro": erro_marcacao, "aviso": None}
    return corpo, uso, estado


def rota_analisar(dados, usuario, ip):
    """POST /api/planner/analisar (SPEC §5.4): modo "analise" (telas +
    contexto) ou "pergunta" (acompanhamento). Devolve (status_http, corpo);
    nunca levanta. Toda chamada vira uma linha planner_analise no log."""
    inicio = time.time()
    info = _info_analise()
    try:
        if not isinstance(dados, dict):
            raise ErroPlanner("Requisição inválida: envie o pedido de análise em JSON.")
        info["sessao"] = _texto(dados.get("session_id"), 80, linha_unica=True)
        modo = _texto(dados.get("modo"), 20, linha_unica=True).lower() or "analise"
        info["modo"] = modo
        if modo == "esclarecer":
            # Etapa própria, com linha própria no log (não conta como análise)
            return rota_esclarecer(dados, usuario, ip)
        if modo == "analise":
            corpo, uso, estado = _fazer_analise(dados, info)
        elif modo == "pergunta":
            corpo, uso, estado = _responder_pergunta(dados, info)
        else:
            raise ErroPlanner('Modo inválido: use "esclarecer" (dúvidas antes da análise), '
                              '"analise" (análise nova) ou "pergunta" (pergunta de '
                              'acompanhamento).')
        _registrar_analise(usuario, ip, info, "ok", inicio, uso=uso,
                           marcacao=corpo["marcacao"], estado=estado)
        elementos = len((corpo["marcacao"] or {}).get("elementos") or [])
        _console("[planner] {} concluída em {:.1f}s - marcação {}{}.".format(
            "Análise" if modo == "analise" else "Resposta", time.time() - inicio,
            {"invalida": "inválida"}.get(estado, estado),
            " ({} elemento{})".format(elementos, "" if elementos == 1 else "s")
            if elementos else ""))
        return 200, corpo
    except ErroPlanner as e:
        _registrar_analise(usuario, ip, info, "erro", inicio, erro=str(e))
        if e.status >= 500:
            _console("[planner][erro] {}".format(str(e)[:300]))
        return e.status, {"erro": str(e)}
    except Exception as e:
        _registrar_analise(usuario, ip, info, "erro", inicio, erro=_detalhe_erro(e))
        _console("[planner][erro] Falha inesperada: {!r}".format(e))
        return 500, {"erro": "Erro interno ao processar a análise: {}".format(
            _detalhe_erro(e)[:200])}


# ---- Etapa "esclarecer": dúvidas da IA antes da análise ---------------------------
_RE_ABRE_ESCLARECIMENTO = re.compile(r"(`{3,}|~{3,})[ \t]*esclarecimento\b", re.IGNORECASE)
_RE_TEM_PERGUNTAS = re.compile(r"perguntas|questions", re.IGNORECASE)


def _system_prompt_esclarecimento():
    """Regras do time + documentação, com as instruções da etapa de dúvidas no
    lugar das instruções da análise (marcação, formato do bloco)."""
    base = obter_system_prompt()
    if base.endswith(INSTRUCOES_FERRAMENTA):
        base = base[:-len(INSTRUCOES_FERRAMENTA)]
    else:
        base += SEPARADOR_PROMPT
    return base + INSTRUCOES_ESCLARECIMENTO.replace(
        "{max}", str(max(1, int(PLANNER_ESCLARECER_MAX_PERGUNTAS or 1))))


def _normalizar_esclarecimento(objeto):
    """{"entendimento", "perguntas": [{"id", "pergunta", "por_que", "opcoes",
    "premissa"}]} a partir do que o modelo devolveu, ou None."""
    if isinstance(objeto, list):
        objeto = {"perguntas": objeto}
    if not isinstance(objeto, dict):
        return None
    d = {str(k).strip().lower(): v for k, v in objeto.items()}
    brutas = _primeiro(d, ("perguntas", "questions", "duvidas", "dúvidas"))
    if not isinstance(brutas, list):
        brutas = []
    limite = max(1, int(PLANNER_ESCLARECER_MAX_PERGUNTAS or 1))
    perguntas = []
    for bruta in brutas:
        if isinstance(bruta, str):
            bruta = {"pergunta": bruta}
        if not isinstance(bruta, dict):
            continue
        b = {str(k).strip().lower(): v for k, v in bruta.items()}
        texto = _texto(_primeiro(b, ("pergunta", "question", "texto")), 300, linha_unica=True)
        if not texto:
            continue
        opcoes_brutas = _primeiro(b, ("opcoes", "opções", "options", "respostas"))
        opcoes = []
        for o in opcoes_brutas if isinstance(opcoes_brutas, list) else []:
            o = _texto(o, 120, linha_unica=True)
            if o and o not in opcoes:
                opcoes.append(o)
        perguntas.append({
            "id": "P{}".format(len(perguntas) + 1),
            "pergunta": texto,
            "por_que": _texto(_primeiro(b, ("por_que", "porque", "motivo", "why")), 300,
                              linha_unica=True),
            "opcoes": opcoes[:4],
            "premissa": _texto(_primeiro(b, ("premissa", "assumption", "suposicao")), 300,
                               linha_unica=True),
        })
        if len(perguntas) >= limite:
            break
    entendimento = _texto(_primeiro(d, ("entendimento", "resumo", "understanding")), 600)
    return {"entendimento": entendimento, "perguntas": perguntas}


def extrair_esclarecimento(texto):
    """Lê o bloco ```esclarecimento (ou o último ```json com "perguntas", ou o
    texto inteiro como JSON). None se nada servir."""
    if not isinstance(texto, str) or not texto.strip():
        return None
    normalizado = texto.replace("\r\n", "\n").replace("\r", "\n")
    blocos = _blocos_cercados(normalizado, _RE_ABRE_ESCLARECIMENTO)
    if not blocos:
        blocos = [b for b in _blocos_cercados(normalizado, _RE_ABRE_JSON)
                  if _RE_TEM_PERGUNTAS.search(b[2])]
    candidatos = [b[2] for b in blocos[::-1]] or [normalizado]
    for corpo in candidatos:
        objeto, _motivo = _carregar_json_tolerante(corpo)
        resultado = _normalizar_esclarecimento(objeto)
        if resultado is not None:
            return resultado
    return None


def rota_esclarecer(dados, usuario, ip):
    """Modo "esclarecer" do POST /api/planner/analisar: a IA vê as telas e o
    pedido e devolve até PLANNER_ESCLARECER_MAX_PERGUNTAS dúvidas. Nunca
    levanta; grava uma linha planner_esclarecimento no log."""
    inicio = time.time()
    campos = {"sessao": _texto(dados.get("session_id"), 80, linha_unica=True),
              "status": "erro", "telas": 0, "partes": 0, "perguntas": 0}
    uso = None
    try:
        if not PLANNER_ESCLARECER:
            raise ErroPlanner("A etapa de dúvidas está desligada neste servidor "
                              "(PLANNER_ESCLARECER no config.py).")
        contexto = validar_contexto(dados.get("contexto"))
        campos.update(canal=contexto["canal"], pergunta=contexto["pergunta"][:300])
        telas, bytes_imagens = validar_telas(dados.get("telas"))
        campos.update(telas=len(telas), partes=sum(len(t["partes"]) for t in telas),
                      bytes_imagens=bytes_imagens)
        conteudo, _texto_ctx, _uc = montar_mensagem_analise(contexto, telas, PLANNER_DETALHE_IMAGEM)
        campos["caracteres"] = caracteres_de_texto(conteudo)
        _console("[planner] Preparando as dúvidas: {} tela(s) / {} parte(s) - sessão {}".format(
            campos["telas"], campos["partes"], campos["sessao"] or "-"))
        try:
            texto, uso = bridge.gerar_texto(
                [{"role": "user", "content": conteudo}], _system_prompt_esclarecimento(),
                modelo=PLANNER_MODELO_LLM, max_tokens=PLANNER_ESCLARECER_MAX_TOKENS,
                reasoning_effort=PLANNER_ESCLARECER_REASONING_EFFORT or "",
                rotulo="planner-esclarecimento",
                tokens_extra_estimados=estimar_tokens_telas(telas, PLANNER_DETALHE_IMAGEM))
        except ErroPlanner:
            raise
        except Exception as e:
            raise ErroPlanner("Não consegui preparar as dúvidas com a IA: {}".format(
                _detalhe_erro(e)), 502)
        resultado = extrair_esclarecimento(texto)
        aviso = None
        if resultado is None:
            resultado = {"entendimento": "", "perguntas": []}
            aviso = ("A IA não devolveu as dúvidas no formato esperado. Dá para seguir direto "
                     "para a análise.")
        campos.update(status="ok", perguntas=len(resultado["perguntas"]),
                      formato="ok" if aviso is None else "invalido")
        corpo = {"entendimento": resultado["entendimento"], "perguntas": resultado["perguntas"],
                 "aviso": aviso}
        status = 200
    except ErroPlanner as e:
        campos["erro"] = str(e)[:200]
        corpo, status = {"erro": str(e)}, e.status
    except Exception as e:
        campos["erro"] = _detalhe_erro(e)[:200]
        _console("[planner][erro] Falha inesperada nas dúvidas: {!r}".format(e))
        corpo, status = {"erro": "Erro interno ao preparar as dúvidas: {}".format(
            _detalhe_erro(e)[:200])}, 500
    campos["duracao_s"] = round(time.time() - inicio, 1)
    if uso:
        campos["tokens_entrada"] = uso.get("entrada", 0)
        campos["tokens_saida"] = uso.get("saida", 0)
        campos["tokens_total"] = uso.get("total", 0)
        campos["tokens_fonte"] = uso.get("fonte", "")
    atividade.registrar("planner_esclarecimento", usuario, ip, **campos)
    if status == 200:
        _console("[planner] Dúvidas prontas em {:.1f}s: {} pergunta(s).".format(
            campos["duracao_s"], campos["perguntas"]))
    return status, corpo


# ---- Rota: POST /api/planner/figma -------------------------------------------
# Mesma regra do figma.py (8 a 512 caracteres visíveis): token curto (tecla
# acidental no campo) recebe a mensagem clara daqui.
_RE_TOKEN_FIGMA = re.compile(r"^[\x21-\x7e]{8,512}$")


def validar_url_figma(valor):
    """URL https de um host figma.com (www, embed...). Sem esquema ganha
    https://; http:// e outros hosts são recusados. As regras batem com as do
    figma.py (que ainda extrai chave e node-id): as duas camadas não podem
    discordar sobre o mesmo link."""
    url = _texto(valor).strip("'\"<>").strip()
    if not url:
        raise ErroPlanner("Cole a URL do arquivo ou do frame do Figma.")
    if len(url) > 4096:
        raise ErroPlanner("A URL do Figma é longa demais.")
    if "\n" in url or "\t" in url:
        raise ErroPlanner("URL do Figma inválida: cole só o link, numa linha.")
    if "://" not in url and re.match(r"^(?:[a-z0-9-]+\.)*figma\.com(?:[/?#]|$)", url,
                                     re.IGNORECASE):
        url = "https://" + url
    invalida = ErroPlanner("URL do Figma inválida: use o link https://www.figma.com/... "
                           "copiado do Figma (Compartilhar > Copiar link).")
    try:
        partes = urllib.parse.urlsplit(url)
        host = (partes.hostname or "").lower()
    except ValueError:
        raise invalida
    if partes.scheme.lower() != "https" or not (host == "figma.com" or host.endswith(".figma.com")):
        raise invalida
    return url


def validar_ids_figma(valor):
    """Ids dos frames escolhidos (lista; "12-34" vira "12:34"). [] se vazio."""
    if valor is None or valor == "":
        return []
    if isinstance(valor, str):
        valor = [v for v in re.split(r"[\s,]+", valor) if v]
    if not isinstance(valor, list):
        raise ErroPlanner("Lista de frames inválida.")
    ids = []
    for bruto in valor:
        if isinstance(bruto, str) and not bruto.strip():
            continue
        node_id = _normalizar_node_id(bruto)
        if node_id is None:
            raise ErroPlanner("Identificador de frame inválido: {}.".format(
                _texto(bruto, 40, linha_unica=True) or "(vazio)"))
        if node_id not in ids:
            ids.append(node_id)
    if len(ids) > FIGMA_MAX_FRAMES:
        raise ErroPlanner("Selecione no máximo {} frames por busca (foram {}).".format(
            FIGMA_MAX_FRAMES, len(ids)))
    return ids


def validar_token_figma(valor):
    """Token pessoal do Figma da própria pessoa (opcional). NUNCA vai para o log."""
    if valor is None:
        return ""
    if not isinstance(valor, str):
        raise ErroPlanner("Token do Figma inválido.")
    token = valor.strip()
    if token and not _RE_TOKEN_FIGMA.match(token):
        raise ErroPlanner("Token do Figma inválido: confira se copiou o token inteiro "
                          "(ele começa com figd_).")
    return token


def _sem_token(texto, token):
    """Garante que o token do usuário não vaze em mensagem de erro/log. Só
    tokens com tamanho de token (>= 8): mascarar "e" picaria a frase toda."""
    texto = str(texto or "")
    return texto.replace(token, "***") if token and len(token) >= 8 else texto


def _status_http(valor, padrao=502):
    status = _inteiro(valor, 400, 599)
    return status if status is not None else padrao


def rota_figma(dados, usuario, ip):
    """POST /api/planner/figma (SPEC §5.3): lista os frames do arquivo ou
    exporta os frames escolhidos (imagem + inventário de camadas). Devolve
    (status_http, corpo); nunca levanta. Toda chamada vira uma linha
    planner_figma no log (sem o token)."""
    inicio = time.time()
    url, ids, token = "", [], ""
    campos = {"status": "erro", "url": "", "arquivo": "", "file_key": "", "frames": 0,
              "ids": "", "elementos": 0, "token_proprio": False}
    try:
        if not isinstance(dados, dict):
            raise ErroPlanner("Requisição inválida: envie a URL do Figma em JSON.")
        token = validar_token_figma(dados.get("token_figma"))
        campos["token_proprio"] = bool(token)
        url = validar_url_figma(dados.get("url"))
        campos["url"] = url[:300]
        ids = validar_ids_figma(dados.get("ids"))
        campos["ids"] = ",".join(ids)[:200]
        if figma is None:
            raise ErroPlanner("A busca no Figma está indisponível neste servidor. Envie as "
                              "telas como imagem.", 503)
        resultado = figma.buscar(url, ids or None, token or None)
        frames = resultado.get("frames") if isinstance(resultado, dict) else None
        opcoes = resultado.get("opcoes") if isinstance(resultado, dict) else None
        if not isinstance(frames, list) and not isinstance(opcoes, list):
            raise RuntimeError("resposta inesperada do módulo do Figma")
        arquivo = resultado.get("arquivo") if isinstance(resultado.get("arquivo"), dict) else {}
        campos.update(
            status="ok" if isinstance(frames, list) else "opcoes",
            arquivo=_texto(arquivo.get("nome"), 200, linha_unica=True),
            file_key=_texto(arquivo.get("key"), 128, linha_unica=True),
            frames=len(frames) if isinstance(frames, list) else 0,
            elementos=sum(len(f.get("inventario") or []) for f in frames or []
                          if isinstance(f, dict)))
        campos["duracao_s"] = round(time.time() - inicio, 1)
        atividade.registrar("planner_figma", usuario, ip, **campos)
        if isinstance(frames, list):
            _console("[planner][figma] {} frame(s) exportado(s) de \"{}\" ({} camadas).".format(
                campos["frames"], campos["arquivo"], campos["elementos"]))
        else:
            _console("[planner][figma] {} frame(s) listado(s) em \"{}\" para escolher.".format(
                len(opcoes), campos["arquivo"]))
        return 200, resultado
    except Exception as e:
        if isinstance(e, ErroPlanner):
            status, mensagem = e.status, str(e)
        elif figma is not None and isinstance(e, figma.ErroFigma):
            status = _status_http(getattr(e, "status", 502))
            mensagem = str(e) or "Falha ao buscar no Figma."
            _console("[planner][figma] Busca recusada (HTTP {}): {}".format(
                status, _sem_token(mensagem, token)[:300]))
        else:
            status = 502
            mensagem = ("Não consegui buscar no Figma agora (falha de rede ou do proxy). "
                        "Tente de novo em instantes ou envie as telas como imagem.")
            _console("[planner][figma][erro] {}".format(_sem_token(_detalhe_erro(e), token)[:300]))
        mensagem = _sem_token(mensagem, token)
        campos["duracao_s"] = round(time.time() - inicio, 1)
        campos["erro"] = mensagem[:200]
        atividade.registrar("planner_figma", usuario, ip, **campos)
        return status, {"erro": mensagem}
