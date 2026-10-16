"""Log de atividade por usuário — trilha de auditoria do sistema.

Cada ação relevante (login, logout, busca no Confluence, auditoria enviada,
erros) vira UMA linha JSON no arquivo logs/atividade-AAAA-MM.jsonl, ex.:

    {"quando": "2026-07-22T14:03:11", "acao": "auditoria", "email": "fulano@gmail.com",
     "nome": "Fulano", "ip": "10.2.3.4", "status": "ok", "caracteres": 18400, ...}

Um arquivo por mês (JSON Lines): fácil de abrir no Excel/pandas e é a fonte
de dados da futura tela administrativa (ver EMAILS_ADMIN no config.py).
As funções ler()/listar_meses() já servem essa tela.
"""
import json
import os
import threading
from datetime import datetime

from .config import BASE_DIR, PASTA_LOGS

_trava = threading.Lock()
_PASTA = os.path.join(BASE_DIR, PASTA_LOGS)
_PREFIXO = "atividade-"


def _arquivo_do_mes():
    return os.path.join(_PASTA, "{}{}.jsonl".format(
        _PREFIXO, datetime.now().strftime("%Y-%m")))


def registrar(acao, usuario=None, ip=None, **campos):
    """Grava um evento na trilha de auditoria. Nunca derruba a requisição:
    se o disco falhar, só avisa no console."""
    evento = {
        "quando": datetime.now().isoformat(timespec="seconds"),
        "acao": acao,
        "email": (usuario or {}).get("email", ""),
        "nome": (usuario or {}).get("nome", ""),
        "ip": ip or "",
    }
    evento.update(campos)
    try:
        with _trava:
            os.makedirs(_PASTA, exist_ok=True)
            with open(_arquivo_do_mes(), "a", encoding="utf-8") as f:
                f.write(json.dumps(evento, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[atividade] Falha ao gravar o log ({}).".format(str(e)[:100]))


def listar_meses():
    """Meses com atividade registrada, ex.: ["2026-07", "2026-06"]."""
    try:
        nomes = os.listdir(_PASTA)
    except OSError:
        return []
    meses = [n[len(_PREFIXO):-len(".jsonl")] for n in nomes
             if n.startswith(_PREFIXO) and n.endswith(".jsonl")]
    return sorted(meses, reverse=True)


def ler(mes=None, limite=1000, email=None):
    """Eventos do mês (AAAA-MM; padrão = mês atual), mais recentes primeiro.
    `email` filtra por usuário. Base da futura tela administrativa."""
    caminho = (os.path.join(_PASTA, "{}{}.jsonl".format(_PREFIXO, mes))
               if mes else _arquivo_do_mes())
    eventos = []
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            for linha in f:
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    ev = json.loads(linha)
                except json.JSONDecodeError:
                    continue
                if email and ev.get("email") != email:
                    continue
                eventos.append(ev)
    except OSError:
        return []
    eventos.reverse()
    return eventos[:max(1, int(limite))]


def _arquivos_de_log():
    """Caminhos de todos os arquivos de trilha (atividade-*.jsonl)."""
    try:
        nomes = os.listdir(_PASTA)
    except OSError:
        return []
    return [os.path.join(_PASTA, n) for n in nomes
            if n.startswith(_PREFIXO) and n.endswith(".jsonl")]


def backup(destino):
    """Copia os arquivos de log para `destino` (durabilidade fora da máquina).
    Devolve o nº de arquivos copiados. Nunca levanta — só avisa em falha."""
    if not destino:
        return 0
    import shutil
    copiados = 0
    try:
        os.makedirs(destino, exist_ok=True)
        for caminho in _arquivos_de_log():
            shutil.copy2(caminho, os.path.join(destino, os.path.basename(caminho)))
            copiados += 1
    except Exception as e:
        print("[backup] Falha ao copiar os logs para {} ({}).".format(
            destino, str(e)[:120]))
    return copiados
