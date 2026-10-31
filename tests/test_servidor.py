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


# ---- Respostas JSON ---------------------------------------------------------

def test_api_sem_sessao_responde_401_em_json(porta):
    status, cab, corpo = _pedir(porta, "GET", "/api/me")
    assert status == 401
    assert cab["Content-Type"] == "application/json; charset=utf-8"
    assert cab["X-Content-Type-Options"] == "nosniff"
    assert "erro" in json.loads(corpo)


def test_rota_post_protegida_exige_sessao(porta):
    corpo = json.dumps({"url": "https://confluence.bradesco.com.br:8443/x/abc"})
    status, _cab, _ = _pedir(porta, "POST", "/api/confluence", corpo,
                             {"Content-Type": "application/json"})
    assert status == 401


class _RespostaFalsa:
    def _enviar(self, status, dados, tipo, extras=()):
        self.status, self.dados, self.tipo = status, dados, tipo


def test_json_sai_sem_caracteres_de_html():
    original = {"resposta": "<script>alert(1)</script> & <b>"}
    falsa = _RespostaFalsa()
    servidor.ManipuladorChat._responder(falsa, 200, json.dumps(original, ensure_ascii=False))
    assert not set(b"<>&") & set(falsa.dados)
    assert json.loads(falsa.dados) == original


def test_texto_simples_nao_e_alterado():
    falsa = _RespostaFalsa()
    servidor.ManipuladorChat._responder(falsa, 404, "Não <encontrado>", "text/plain; charset=utf-8")
    assert falsa.dados == "Não <encontrado>".encode("utf-8")
