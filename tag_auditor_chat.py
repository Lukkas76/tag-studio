"""Tag Studio (Tag Auditor + Tag Planner) — ponto de entrada do servidor.

Para rodar:  python tag_auditor_chat.py

A janela mostra o endereço da plataforma. Depois do login a pessoa cai na
Home e escolhe a ferramenta:
    /          login + Home da plataforma
    /auditor   Tag Auditor  — audita o plano de tagueamento (.json/Confluence)
    /planner   Tag Planner  — do KPI ao plano mínimo de tagueamento
    /admin     painel administrativo (só EMAILS_ADMIN)

O código vive no pacote tagauditor/ (um módulo por responsabilidade) e as
CONFIGURAÇÕES (URLs, chaves, flags, e-mails de admin, nome da plataforma...)
ficam TODAS em tagauditor/config.py. O front fica em static/ (JS em
static/js/). O nome do arquivo continua tag_auditor_chat.py para não quebrar
atalhos e scripts que já o chamam.

Backup da versão antiga (arquivo único): pasta "Tag Auditor - backups".
"""
from tagauditor.servidor import main

if __name__ == "__main__":
    main()
