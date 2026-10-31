# Imagem base homologada ("Lista de Imagem Base v2", Confluence ECIS): Python
# 3.12 sobre UBI 9, com atualizacao diaria de pacotes. Ja roda como o usuario
# 1001 (sem root), com a pasta de trabalho em /opt/app-root/src.
# A pipeline le a imagem da primeira instrucao de base para o scan do StackRox;
# nao escreva a palavra-chave em maiusculas em comentarios acima dela.
FROM artefatos.bradesco.com.br/imagembase-docker-central/ubi9_custom/python-312:daily-update

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/app-root/src

# So biblioteca padrao do Python: nao ha pip install. Os arquivos ficam do
# usuario 1001, porque o app grava logs/ e registro.txt na propria pasta.
COPY --chown=1001:0 tag_auditor_chat.py ./
COPY --chown=1001:0 tagauditor/ tagauditor/
COPY --chown=1001:0 static/ static/
COPY --chown=1001:0 *.txt *.md ./

USER 1001

EXPOSE 8765

CMD ["python", "tag_auditor_chat.py"]
