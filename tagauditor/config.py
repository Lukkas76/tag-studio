"""Configurações do Tag Auditor — TUDO que é editável fica neste arquivo.

Depois de alterar qualquer valor, reinicie o servidor (feche a janela e rode
de novo: python tag_auditor_chat.py).
"""
import os

# ---- Credenciais -------------------------------------------------------------
# Os VALORES ficam em tagauditor/segredos.py, que nao vai para o Git (modelo
# em branco: segredos_exemplo.py). Sem o arquivo, cada credencial vale "".
try:
    from . import segredos as _segredos
except ImportError:
    _segredos = None
    print("[config] tagauditor/segredos.py nao encontrado: copie o "
          "segredos_exemplo.py e preencha as credenciais.")


def _segredo(nome):
    return getattr(_segredos, nome, "") if _segredos else ""


# Raiz do projeto (pasta onde ficam tag_auditor_chat.py, static/, prompts...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- Plataforma ----------------------------------------------------------------
# Nome da plataforma que reune as ferramentas (Tag Auditor + Tag Planner).
# Aparece no login, na Home, no painel admin e no titulo das abas. Para trocar
# o nome, altere SO aqui.
NOME_PLATAFORMA = "Tag Studio"

# ---- APIs internas (Bridge / Bradesco IA Gen) --------------------------------
BASE_API = "https://api-leap-platse.apps.arodvplatsepi11.arocorpp.bradesco.com.br"

URL_LOGIN = BASE_API + "/iagen-identity/v1/usuarios/login-servico"
URL_AGENTE = (
    BASE_API
    + "/iagen-agents/v1/agents/TAG_AUDITOR_AGENT/tasks/send?version=draft"
)

URL_WORKFLOW_START = BASE_API + "/iagen-workflow-request/v1/request/start-workflow"
URL_WORKFLOW_STATUS = BASE_API + "/iagen-workflow-response/v1/response/workflow-execution-status/"

# ROTA OFICIAL DO APP, NAO MUDE: todo envio vai ASSINCRONO no TEXTGENERATOR.
# MODO_ASSINCRONO=False volta para a rota do agente (URL_AGENTE, sincrona) e
# ASYNC_VIA_TEXTGEN=False passa a disparar via start-workflow
# (WORKFLOW_CONFIG_CODE). As duas existem so como plano B de diagnostico -
# a geracao leva minutos e o gateway corta em 30s, entao qualquer variante
# sincrona estoura. Manter as duas em True.
MODO_ASSINCRONO = True
ASYNC_VIA_TEXTGEN = True
URL_TEXTGEN = BASE_API + "/iagen-textgenerator/v1/text/generate"
# Modelo LLM da plataforma iagen. Formato: "<conexao>;<modelo>".
# A conexao "az001" (ambiente DEV) aponta para o Azure OpenAI
# opaiazudvbraiagen001 (East US 2) -> SO aceita modelos OpenAI (gpt-*), e o
# <modelo> tem de ser um deployment publicado nesse recurso.
# Em uso: gpt-5.4 (flagship GPT-5 mais avancado publicado em az001/DEV).
# Alternativas ja publicadas em az001: gpt-5.1, gpt-5, gpt-4.1, gpt-4o-2024-11-20.
# Para trocar, altere APENAS o trecho depois do ";".
MODELO_LLM = "az001;gpt-5.4"
# Versao da API OpenAI exigida pelo Azure OpenAI (campo obrigatorio na Bridge).
# O modo de raciocinio (REASONING_EFFORT) so existe nas versoes recentes da
# API; se a Bridge recusar esta versao, volte para "2024-02-01" e deixe
# REASONING_EFFORT = None.
OPENAI_API_VERSION = "2025-01-01-preview"
# "Deep think" / esforco de raciocinio dos modelos GPT-5+:
#   None = desligado | "low" | "medium" | "high" (maximo)
# Em "high" o modelo pensa por mais tempo antes de responder: auditorias mais
# profundas e criteriosas, porem mais lentas (segundos -> minutos). Se o
# Bridge/Azure devolver erro citando "reasoning_effort", deixe None.
REASONING_EFFORT = "high"
# Teto de tokens da RESPOSTA. Com o raciocinio ligado, os "pensamentos" do
# modelo tambem consomem este teto - por isso o valor maior.
MAX_TOKENS_RESPOSTA = 16000
# Aleatoriedade do modelo (0 a 2). Quanto MENOR, mais deterministico/factual.
# ATENCAO: modelos em modo raciocinio NAO aceitam temperature - com
# REASONING_EFFORT ligado deixe None (o campo e omitido do payload).
TEMPERATURA = None
WORKFLOW_CONFIG_CODE = "CD_WRFL_TAG_AUDITOr"
WORKFLOW_FORMATO = "messages"
WORKFLOW_INPUT_PATH = "input_text"
ENVIAR_SYSTEM_PROMPT = True
SYSTEM_PROMPT_REGRAS = ""
# Regras do auditor (SEM a documentacao embutida). A doc completa e carregada
# a parte de ARQUIVO_DOCUMENTACAO e anexada em tempo de execucao.
ARQUIVO_SYSTEM_PROMPT = "prompt_final.txt"
# Documentacao oficial COMPLETA (fonte unica de verdade). Carregada na integra e
# anexada ao system prompt a cada execucao. Deixe "" para nao anexar.
ARQUIVO_DOCUMENTACAO = "documentacao_base_bradesco.md"
POLL_INTERVALO_SEG = 3
POLL_TIMEOUT_SEG = 600

MODO_STREAM = False
STREAM_NO_PAYLOAD = True
ASYNC_NO_PAYLOAD = False

# Credenciais de servico da Bridge (login-servico)
IDENTIFICADOR = _segredo("IDENTIFICADOR")
SENHA = _segredo("SENHA")

SANITIZAR_CHAVES_DUPLAS = False

MINIFICAR_JSON = True

INSTRUCAO_PADRAO = "Audite o seguinte plano de tagueamento:"

# ---- Limpeza do plano antes do envio ----------------------------------------
REMOVER_CAMPOS_BASE64 = True
TAMANHO_MIN_BASE64 = 500

ENXUGAR_PLANO = True
LIMITE_VALOR_STR = 200
LIMITE_OBS_STR = 500

SOMENTE_TABELA = False

FILTRAR_SEM_AJUSTES = True

# Remove da resposta da IA linhas de auditoria que violam regras do prompt/da
# documentacao (falsos positivos conhecidos): apontamentos especulativos
# ("sugere...", "se for de fato..."), "coerencia" entre name/path (regra que
# nao existe), placeholders {{...}}, campos ausentes etc. Ver filtro.py.
FILTRAR_FALSOS_POSITIVOS = True

# ---- Limites tecnicos do GA4 (contagem em codigo, nao no prompt) -------------
# Antes esta regra estava na documentacao (secao 7) e dependia de a IA CONTAR
# caracteres - o modelo errava direto. Agora quem conta e o limites.py, direto
# no JSON do plano; a IA nem precisa saber os numeros. Com True, o servidor
# remove da resposta os apontamentos de tamanho inventados pela IA e anexa a
# tabela "Limites tecnicos do GA4 - validacao automatica".
# So funciona com o plano ANEXADO em JSON (plano colado como texto nao da para
# contar). Ver limites.py.
VALIDAR_LIMITES_GA4 = True
# Limites oficiais do GA4 (em caracteres, exceto os de quantidade). Se o Google
# mudar algum numero, altere AQUI - e o unico lugar onde eles existem.
LIMITE_NOME_EVENTO = 40          # nome do evento          - acima disso o evento nao e registrado
LIMITE_NOME_PARAMETRO = 40       # nome do parametro       - acima disso o parametro e descartado
LIMITE_VALOR_PARAMETRO = 100     # valor (string)          - acima disso o valor e truncado
LIMITE_PARAMS_POR_EVENTO = 25    # parametros por evento   - excedentes ignorados
LIMITE_NOME_USER_PROPERTY = 24   # nome de user property
LIMITE_VALOR_USER_PROPERTY = 36  # valor de user property
LIMITE_USER_PROPERTIES = 25      # user properties distintas na propriedade GA4

# ---- Servidor ----------------------------------------------------------------
PORTA_INICIAL = 8765
TIMEOUT_SEGUNDOS = 300
TOKEN_VALIDADE_SEGUNDOS = 25 * 60

TENTATIVAS_GATEWAY = 5
PAUSA_ENTRE_TENTATIVAS = 3
AQUECER_AGENTE = True

TAMANHO_MAX_PARTE = 2000

# ---- Login com Google (fluxo de dispositivo, estilo "login de TV") -----------
# Funciona em QUALQUER URL (http://maquina:porta, IP interno, sem HTTPS) e sem
# nenhuma pagina externa: o app mostra um codigo curto (ex.: ABCD-EFGH), o
# usuario digita esse codigo em google.com/device (pagina do proprio Google),
# autoriza, e o servidor recebe os dados da conta direto do Google.
#
# Configuracao (1x) em https://console.cloud.google.com/apis/credentials :
# 1) Crie um "ID do cliente OAuth" do tipo:
#        "TVs e dispositivos de entrada limitada"
#    (o fluxo de dispositivo SO funciona com esse tipo; cliente "Aplicativo da
#     Web" NAO serve aqui e nao precisa de URI de redirecionamento)
# 2) Cole o Client ID (...apps.googleusercontent.com) e o Client secret em
#    tagauditor/segredos.py.
# 3) Publique o app na "Tela de permissao OAuth" (Publico-alvo -> Publicar
#    aplicativo) para qualquer conta Google poder entrar.
GOOGLE_CLIENT_ID = _segredo("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = _segredo("GOOGLE_CLIENT_SECRET")
URL_GOOGLE_DEVICE_CODE = "https://oauth2.googleapis.com/device/code"
URL_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
# Proxy corporativo para alcancar o Google (usado SO no login; as APIs internas
# do Bradesco seguem em conexao direta). Vazio = o servidor descobre sozinho
# (proxy do sistema, arquivo PAC do registro do Windows e netsh winhttp) e
# imprime a rota escolhida numa linha [google] ao iniciar/logar.
# Preencha manualmente se a descoberta falhar, ex.:
# PROXY_GOOGLE = "http://proxy.bradesco.com.br:8080"
# http://proxybl.net.bradesco.com.br:80
PROXY_GOOGLE = ""

# ---- Confluence (buscar o plano direto da pagina) ----------------------------
# O usuario cola a URL da pagina e o servidor busca via REST API (conexao
# direta, rede interna): 1) se a pagina tiver anexo .json, baixa o anexo;
# 2) senao, le o conteudo da pagina e monta o mesmo JSON {"sections": ...} do
# download manual, extraindo os blocos de codigo (code_json) por secao.
# Autenticacao: crie um Personal Access Token no Confluence (avatar > Settings
# / Configuracoes > Personal Access Tokens) e cole em tagauditor/segredos.py. Se o seu Confluence
# for antigo e nao tiver PAT, use usuario/senha de rede (Basic).
CONFLUENCE_TOKEN = _segredo("CONFLUENCE_TOKEN")
CONFLUENCE_USUARIO = _segredo("CONFLUENCE_USUARIO")
CONFLUENCE_SENHA = _segredo("CONFLUENCE_SENHA")
CONFLUENCE_TIMEOUT = 30

# ---- Tag Planner (o que taguear a partir da tela + pergunta de negocio) -------
# A pessoa envia imagens da tela (upload ou URL do Figma) e a pergunta de
# negocio; o modelo devolve KPI, elementos obrigatorios x opcionais, plano
# minimo de mensuracao, taxonomia sugerida e as coordenadas para a marcacao
# visual (quem DESENHA a marcacao e o navegador, nao o modelo).
#
# Regras do agente (texto do time, editavel): PLANNER_ARQUIVO_PROMPT.
# Base de conhecimento (documentacao oficial, a mesma do auditor):
# PLANNER_ARQUIVO_DOCUMENTACAO. "" = nao anexa.
PLANNER_ARQUIVO_PROMPT = "prompt_tag_planner.txt"
PLANNER_ARQUIVO_DOCUMENTACAO = "documentacao_base_bradesco.md"
# Modelo: precisa aceitar IMAGEM (os gpt-5.x aceitam). Mesmo formato do
# MODELO_LLM ("<conexao>;<modelo>").
PLANNER_MODELO_LLM = MODELO_LLM
# None = desligado | "low" | "medium" | "high" (como o REASONING_EFFORT)
PLANNER_REASONING_EFFORT = "high"
# A resposta e longa (tabelas + coordenadas) - teto maior que o do auditor.
PLANNER_MAX_TOKENS_RESPOSTA = 20000
# Nivel de detalhe da imagem para o modelo: "high" | "low" | "auto".
# None = omite o campo (use se a Bridge reclamar de "detail").
PLANNER_DETALHE_IMAGEM = "high"
# Limites de uma analise (o navegador ja respeita; o servidor confere de novo).
PLANNER_MAX_TELAS = 6            # imagens/telas por analise
# Telas muito altas sao divididas em partes. O Azure OpenAI aceita no maximo
# 10 imagens por requisicao - NAO passe de 10.
PLANNER_MAX_PARTES = 10
PLANNER_MAX_MB_IMAGENS = 20      # soma das imagens enviadas (MB em base64)
# Maior lado (px) de cada imagem enviada. Em detalhe "high" o modelo ja reduz
# tudo para caber em 2048px - mandar maior so gasta banda.
PLANNER_LADO_MAX_ENVIO = 2048
# Se a Bridge RECUSAR imagem (conteudo multimodal) e a tela veio do Figma, a
# analise e refeita SO com as camadas do Figma (textos + posicoes exatas), com
# um aviso na resposta. False = mostra o erro direto.
PLANNER_SEM_IMAGEM_SE_RECUSAR = True
# Tempo economizado no painel admin (MINUTOS por analise concluida), igual ao
# TEMPO_MANUAL_MIN/TEMPO_COM_FERRAMENTA_MIN do auditor. 0 nos dois = o painel
# nao estima economia do Tag Planner. Preencha com a estimativa do time.
TEMPO_MANUAL_PLANNER_MIN = 0
TEMPO_COM_PLANNER_MIN = 0

# ---- Figma (usado pelo Tag Planner para buscar telas pela URL) ----------------
# Token pessoal do Figma (Personal Access Token): Figma > avatar > Settings >
# Security > Personal access tokens > "Generate new token", escopo
# "File content: Read only" (file_content:read).
# ATENCAO, tres regras do Figma (out/2026):
#  1) O token so enxerga arquivos que a CONTA DONA do token consegue abrir.
#  2) Cota: a conta precisa ter assento Dev ou Full no plano PAGO dono dos
#     arquivos. Assento View/Collab (ou plano Starter) tem cota de poucas
#     chamadas POR MES nas rotas usadas aqui - a busca para de funcionar logo.
#     Cada busca gasta 2 chamadas (camadas + imagem); listar frames gasta 1.
#     Com token unico no servidor, todo mundo divide a cota dessa conta.
#  3) Token pessoal expira (no maximo 90 dias): renove e troque em segredos.py.
# Vazio = cada pessoa pode informar o proprio token na tela do Tag Planner
# (fica so no navegador dela); sem nenhum token, a busca no Figma fica
# desligada e o upload de imagem continua funcionando.
FIGMA_TOKEN = _segredo("FIGMA_TOKEN")
FIGMA_API = "https://api.figma.com"
# Proxy corporativo ate o Figma. Vazio = descobre sozinho (conexao direta,
# depois os mesmos proxies do login Google). Ex.: "http://proxy.bradesco.com.br:8080"
PROXY_FIGMA = ""
# Verifica o certificado TLS da API do Figma (o token dá leitura dos arquivos
# da conta: não pode ir para um servidor falso no meio do caminho). O Python
# no Windows confia nas mesmas autoridades certificadoras do Windows, inclusive
# a corporativa. Só use False se a busca falhar com erro de CERTIFICADO e o
# time de segurança aprovar.
FIGMA_VERIFICAR_TLS = True
FIGMA_TIMEOUT = 60
# Escala da imagem exportada: frames estreitos (celular) saem ampliados ate
# FIGMA_ESCALA_MAX para a IA ler os textos; largos saem com ~FIGMA_LARGURA_ALVO px.
FIGMA_ESCALA_MAX = 2
FIGMA_LARGURA_ALVO = 1600
FIGMA_MAX_FRAMES = 6             # frames por busca (= PLANNER_MAX_TELAS)
FIGMA_MAX_OPCOES = 80            # frames listados quando a URL nao aponta um frame
FIGMA_MAX_ELEMENTOS = 150        # camadas por frame enviadas a IA (inventario)
# Guarda em memoria o resultado de cada busca por este tempo (minutos): buscar
# de novo os mesmos frames nao gasta a cota do Figma. 0 = sem cache.
FIGMA_CACHE_MIN = 30

# Maior corpo de requisicao aceito pelo servidor (MB). As imagens do Tag
# Planner viajam em base64 dentro do JSON.
LIMITE_CORPO_MB = 40

# ---- Tagueamento / Analytics (GA4 via Google Tag Manager) --------------------
# Implementa o mapa "[Bot] Tag Auditor | Home v1" (tagueamento.pdf): 1 evento de
# visualizacao (page_view) + 8 de interacao (Event_Data) na tela Home.
#
# Container do GTM que recebe os eventos do dataLayer. VAZIO = tagueamento
# DESLIGADO: nenhum script do Google e carregado e nenhum evento e disparado
# (util para rodar local/offline sem sujar os dados da propriedade).
GTM_ID = "GTM-NR72PXBG"
# Propriedade GA4 / Stream ID do mapa. Aqui e so registro: quem recebe os
# eventos e configurado DENTRO do container do GTM, nao por este arquivo.
GA4_STREAM_ID = "546784770"
# Bloco "product" enviado em TODO evento do mapa.
TAG_PRODUTO = "tag-auditor"
TAG_FLUXO = "home"
# Bloco "page" enviado no page_view da Home.
TAG_PAGE_LOCATION = "tag-auditor/home"
TAG_PAGE_TITLE = "Tag Auditor | Home"
# Marca da taxonomia nova, na RAIZ de todo push (event_type). O container do
# GTM exige este valor nos triggers "CE - * - New" (condicao
# "DL - event_type equals new") - sem ele as tags do GA4 nao disparam, mesmo com
# o dataLayer correto. NAO esta descrito no tagueamento.pdf: veio do container.
TAG_EVENT_TYPE = "new"
# True = espelha cada disparo no console do navegador (F12) com o objeto exato
# empurrado para o dataLayer. Serve para a homologacao conferir os eventos sem
# depender do Preview do GTM. Deixe False em uso normal.
TAG_DEBUG = False

# ---- Controle de acesso ------------------------------------------------------
# Restricao de acesso (opcional). Com os DOIS vazios, qualquer conta Google
# entra. Ex.: EMAILS_AUTORIZADOS = {"fulano@gmail.com", "ciclano@gmail.com"}
#          ou DOMINIOS_AUTORIZADOS = {"bradesco.com.br"}
EMAILS_AUTORIZADOS = set()
DOMINIOS_AUTORIZADOS = set()

# Administradores: unicas contas que podem acessar a tela administrativa
# (http://servidor:porta/admin — logs de atividade por usuario).
# ATENCAO: compare com o e-mail da conta usada no LOGIN GOOGLE do Tag Auditor.
EMAILS_ADMIN = {
    "lucas.silva@math.group",          # Lucas
    "daniel.n.santos@bradesco.com.br",   # Daniel
    "rafael.rinaldi@bradesco.com.br",    # Rafael
}

# ---- Registros ---------------------------------------------------------------
ARQUIVO_REGISTRO = "registro.txt"   # ultimo login de cada e-mail (1 linha por conta)
PASTA_LOGS = "logs"                 # trilha de atividade (1 arquivo .jsonl por mes)

# Baselines para estimar o TEMPO ECONOMIZADO no painel admin (em MINUTOS por
# mapa/plano auditado):
#   TEMPO_MANUAL_MIN        = quanto leva 100% MANUAL, sem a ferramenta
#   TEMPO_COM_FERRAMENTA_MIN = quanto leva usando o Tag Auditor
# O painel soma (manual - com_ferramenta) por auditoria concluida. Sao
# ESTIMATIVAS informadas pelo time - ajuste aqui se a realidade mudar.
TEMPO_MANUAL_MIN = 150          # 2h30 por mapa (media do processo manual)
TEMPO_COM_FERRAMENTA_MIN = 15   # ~15 min por mapa usando a ferramenta

# ---- Custo estimado (painel admin converte tokens em dinheiro) ---------------
# Precos do modelo em DOLAR por 1.000.000 (um milhao) de tokens - o padrao com
# que a Azure cobra. O painel converte para MOEDA usando COTACAO_DOLAR.
#
# ATENCAO: nao existe preco oficial PUBLICO do gpt-5.4 que desse para confirmar.
# Os valores abaixo sao os da linha gpt-5.2 (a referencia mais proxima da sua
# tabela Azure): US$ 1,75 entrada / US$ 14,00 saida por milhao. Se o 5.4 for
# mais caro, o custo aqui fica SUBESTIMADO - troque pelos numeros da linha do
# gpt-5.4 assim que ela aparecer na sua tabela de custos.
MOEDA = "R$"
COTACAO_DOLAR = 5.50                # quantos R$ vale US$ 1 (atualize quando mudar)
PRECO_ENTRADA_USD_MILHAO = 1.75     # US$ por 1 milhao de tokens de ENTRADA (base 5.2)
PRECO_SAIDA_USD_MILHAO = 14.00      # US$ por 1 milhao de tokens de SAIDA  (base 5.2)

# Pasta para BACKUP automatico dos logs (durabilidade). Aponte para uma pasta
# do OneDrive ou de rede para proteger o historico FORA desta maquina, ex.:
#   PASTA_LOGS_BACKUP = r"C:\Users\voce\OneDrive - Math Group\Backups\ta-logs"
# Vazio = desligado. O servidor copia os .jsonl ao iniciar e a cada 24h.
PASTA_LOGS_BACKUP = ""

# ---- Historico de conversa por sessao ----------------------------------------
MAX_MSGS_HISTORICO = 20        # nº max. de mensagens (user+assistant) por sessao
MAX_SESSOES_HISTORICO = 200    # nº max. de sessoes guardadas (evita vazamento)
