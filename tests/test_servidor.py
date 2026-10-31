import html
import http.client
import json
import threading

import pytest

from tagauditor import servidor


@pytest.fixture(scope="module")
def porta():
    srv = servidor.ServidorChat(("127.0.0.1", 0), servidor.ManipuladorChat)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _pedir(porta, metodo, caminho, corpo=None, headers=None):
    conexao = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
    try:
        conexao.request(metodo, caminho, body=corpo, headers=headers or {})
        resposta = conexao.getresponse()
        return resposta.status, dict(resposta.getheaders()), resposta.read()
    finally:
        conexao.close()


# ---- Arquivos estáticos -----------------------------------------------------

@pytest.mark.parametrize("nome, mime", [
    ("index.html", "text/html; charset=utf-8"),
    ("js/chat.js", "text/javascript; charset=utf-8"),
    ("js\\chat.js", "text/javascript; charset=utf-8"),
])
def test_estatico_existente_e_servido(nome, mime):
    dados, tipo = servidor._ler_estatico(nome)
    assert dados and tipo == mime


@pytest.mark.parametrize("nome", [
    "", "../tagauditor/config.py", "..\\tagauditor\\config.py", "/etc/passwd",
    "js/../../tagauditor/segredos.py", "js/nao-existe.js", "../setup.py",
])
def test_estatico_fora_da_lista_e_recusado(nome):
    assert servidor._ler_estatico(nome) == (None, None)


def test_get_pagina_tem_cabecalhos_de_seguranca(porta):
    status, cab, corpo = _pedir(porta, "GET", "/")
    assert status == 200
    assert cab["Content-Type"] == "text/html; charset=utf-8"
    assert cab["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    assert cab["X-Content-Type-Options"] == "nosniff"
    assert int(cab["Content-Length"]) == len(corpo)
    assert b"__CONFIG_JSON__" not in corpo


def test_get_estatico_e_traversal(porta):
    assert _pedir(porta, "GET", "/static/js/chat.js")[0] == 200
    assert _pedir(porta, "GET", "/static/../tagauditor/config.py")[0] == 404
    assert _pedir(porta, "GET", "/static/..%2ftagauditor%2fconfig.py")[0] == 404


# ---- Respostas da API (JSON com html.escape) --------------------------------

def _json_do_servidor(corpo):
    """O que o front faz (TA.textoDoServidor + JSON.parse)."""
    return json.loads(html.unescape(corpo.decode("utf-8")))


def test_api_sem_sessao_responde_401(porta):
    status, cab, corpo = _pedir(porta, "GET", "/api/me")
    assert status == 401
    assert cab["Content-Type"] == "text/plain; charset=utf-8"
    assert cab["X-Content-Type-Options"] == "nosniff"
    assert "erro" in _json_do_servidor(corpo)


def test_rota_post_protegida_exige_sessao(porta):
    corpo = json.dumps({"url": "https://confluence.bradesco.com.br:8443/x/abc"})
    status, _cab, _ = _pedir(porta, "POST", "/api/confluence", corpo,
                             {"Content-Type": "application/json"})
    assert status == 401


class _RespostaFalsa:
    def _enviar(self, status, dados, tipo, extras=()):
        self.status, self.dados, self.tipo = status, dados, tipo


def test_resposta_sai_escapada_e_volta_igual():
    original = {"resposta": "<script>alert('x')</script> & \"<b>\" &lt;"}
    falsa = _RespostaFalsa()
    servidor.ManipuladorChat._responder(falsa, 200, json.dumps(original, ensure_ascii=False))
    assert falsa.tipo == "text/plain; charset=utf-8"
    assert not set(b"<>\"'") & set(falsa.dados)
    assert _json_do_servidor(falsa.dados) == original


def test_texto_simples_tambem_sai_escapado():
    falsa = _RespostaFalsa()
    servidor.ManipuladorChat._responder(falsa, 404, "Não <encontrado>")
    assert falsa.dados == "Não &lt;encontrado&gt;".encode("utf-8")


def test_exportar_csv_vem_dentro_do_json(porta, monkeypatch):
    admin = {"email": "admin@exemplo.com", "nome": "Admin"}
    monkeypatch.setitem(servidor.SESSOES_ATIVAS, "tokadmin", admin)
    monkeypatch.setattr(servidor, "EMAILS_ADMIN", ["admin@exemplo.com"])
    monkeypatch.setattr(servidor.atividade, "registrar", lambda *a, **k: None)
    monkeypatch.setattr(servidor.atividade, "ler", lambda **k: [
        {"quando": "2026-10-30T10:00:00", "acao": "login", "email": "a@b.com",
         "comentario": "=SOMA(1;2) <b>"}])
    status, cab, corpo = _pedir(porta, "GET", "/api/admin/exportar?mes=2026-10",
                                headers={"Authorization": "Bearer tokadmin"})
    assert status == 200
    dados = _json_do_servidor(corpo)
    assert dados["arquivo"] == "tag-auditor-logs_2026-10.csv"
    linhas = dados["csv"].splitlines()
    assert linhas[0].startswith("quando;acao;email;nome;ip;status")
    assert "'=SOMA(1;2) <b>" in linhas[1]       # fórmula neutralizada para o Excel
