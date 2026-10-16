"""Histórico de conversa por sessão (perguntas de acompanhamento).

Guarda as últimas mensagens de cada sessão (aba do navegador) para o usuário
poder perguntar sobre um plano JÁ auditado sem precisar reenviá-lo. Usado só
quando o cliente NÃO envia o próprio histórico (localStorage).
"""
import threading

from .config import MAX_MSGS_HISTORICO, MAX_SESSOES_HISTORICO

_trava_hist = threading.Lock()
_historico = {}


def historico_sessao(session_id):
    with _trava_hist:
        return list(_historico.get(session_id, []))


def anexar_historico(session_id, role, content):
    with _trava_hist:
        h = _historico.get(session_id)
        if h is None:
            h = _historico[session_id] = []
            while len(_historico) > MAX_SESSOES_HISTORICO:
                _historico.pop(next(iter(_historico)))
        h.append({"role": role, "content": content})
        if len(h) > MAX_MSGS_HISTORICO:
            # preserva as 2 primeiras (1º plano + 1ª auditoria) e as mais
            # recentes, para nao perder o plano em conversas longas.
            h[2:] = h[len(h) - (MAX_MSGS_HISTORICO - 2):]
