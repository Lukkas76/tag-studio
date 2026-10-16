"""Sessões ativas (token -> usuário) e registro do último login por conta."""
import os
from datetime import datetime

from .config import BASE_DIR, ARQUIVO_REGISTRO

# token de sessao -> {"email": ..., "nome": ..., "foto": ...}
SESSOES_ATIVAS = {}

_CAMINHO_REGISTRO = os.path.join(BASE_DIR, ARQUIVO_REGISTRO)


def atualizar_registro_login(email):
    registro = {}

    # 1. Lê o arquivo existente para não perder os logins anteriores
    if os.path.exists(_CAMINHO_REGISTRO):
        with open(_CAMINHO_REGISTRO, "r", encoding="utf-8") as f:
            for linha in f:
                partes = linha.strip().split(" | ")
                if len(partes) == 2:
                    registro[partes[0]] = partes[1]

    # 2. Atualiza a data/hora do usuário atual
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    registro[email] = agora

    # 3. Reescreve o arquivo com os dados atualizados
    with open(_CAMINHO_REGISTRO, "w", encoding="utf-8") as f:
        for email_salvo, data_salva in registro.items():
            f.write(f"{email_salvo} | {data_salva}\n")
