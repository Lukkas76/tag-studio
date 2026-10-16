"""Tag Studio (plataforma do Tag Auditor) — pacote do servidor.

Depois do login a pessoa cai na Home da plataforma e escolhe a ferramenta:
    Tag Auditor  (/auditor)  audita o plano de tagueamento (.json/Confluence)
    Tag Planner  (/planner)  a partir da tela (imagem ou Figma) e da pergunta
                             de negócio, sugere o que deve e o que não deve
                             ser tagueado (KPI, plano mínimo de mensuração)

Mapa dos módulos:
    config.py       Todas as configurações editáveis (URLs, chaves, flags)
    rede.py         HTTP básico (POST/GET JSON) para as APIs internas
    login_google.py Login com conta Google (fluxo de dispositivo) + proxy
    sessoes.py      Sessões ativas e registro de logins (registro.txt)
    bridge.py       Conversa com a IA (token, textgen, workflow, retries)
    plano.py        Limpeza/enxugo do plano JSON e montagem da mensagem
    filtro.py       Remoção de falsos positivos da resposta da IA (auditor)
    limites.py      Limites técnicos do GA4 (contagem de caracteres em código)
    confluence.py   Busca do plano direto de páginas do Confluence
    historico.py    Histórico de conversa por sessão (cache do servidor)
    planner.py      Tag Planner: prompt, mensagem com imagens, coordenadas
    figma.py        Tag Planner: frames e camadas via API REST do Figma
    atividade.py    Log de atividade por usuário (trilha de auditoria)
    servidor.py     Servidor HTTP, rotas da API e páginas estáticas

Para rodar: python tag_auditor_chat.py (na raiz do projeto).
"""
