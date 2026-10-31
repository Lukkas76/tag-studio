"""Partes internas do ManipuladorChat testadas sem socket: descarte do corpo,
401 de rota protegida, navegador que fecha a conexão antes da resposta,
Content-Type fora da lista e a config do front sem GTM."""
import io
import json

import pytest

from tagauditor import atividade
from tagauditor import servidor


USUARIO = {"email": "fulano@exemplo.com", "nome": "Fulano", "foto": ""}


@pytest.fixture(autouse=True)
def _isolado(monkeypatch, tmp_path):
    monkeypatch.setattr(atividade, "_PASTA", str(tmp_path / "logs"))
    monkeypatch.setattr(servidor, "atualizar_registro_login", lambda email: None)


@pytest.fixture
def eventos(monkeypatch):
    registrados = []

    def registrar(acao, usuario=None, ip=None, **campos):
        registrados.append(dict(campos, acao=acao, usuario=usuario, ip=ip))

    monkeypatch.setattr(servidor.atividade, "registrar", registrar)
    return registrados


def _manipulador(caminho="/", headers=None, corpo=b""):
    """ManipuladorChat sem socket (não passa pelo __init__ que já atende)."""
    h = object.__new__(servidor.ManipuladorChat)
    h.path = caminho
    h.headers = dict(headers or {})
    h.rfile = io.BytesIO(corpo)
    h.wfile = io.BytesIO()
    h.client_address = ("10.0.0.1", 50000)
    h.close_connection = False
    h.request_version = "HTTP/1.1"
    h.requestline = "GET / HTTP/1.1"
    h.command = "GET"
    return h


class _Gravador:
    """Faz as vezes de _responder/_descartar_corpo: guarda as chamadas."""

    def __init__(self):
        self.chamadas = []

    def __call__(self, *args):
        self.chamadas.append(args)


class _Conexao:
    def __init__(self, falha_get=None, falha_set=None):
        self.falha_get, self.falha_set = falha_get, falha_set
        self.timeouts = []

    def gettimeout(self):
        if self.falha_get:
            raise self.falha_get
        return 7.5

    def settimeout(self, valor):
        self.timeouts.append(valor)
        if self.falha_set:
            raise self.falha_set


class _LeituraQueFalha:
    def __init__(self, erro):
        self.erro = erro

    def read(self, n):
        raise self.erro


# ---- _descartar_corpo -------------------------------------------------------

def test_descartar_le_em_blocos_e_devolve_o_timeout(monkeypatch):
    monkeypatch.setattr(servidor.ManipuladorChat, "_BLOCO_DESCARTE", 4)
    h = _manipulador(corpo=b"x" * 10 + b"FICA")
    h.connection = _Conexao()
    h._descartar_corpo(10)
    assert h.rfile.read() == b"FICA"           # leu só o que foi anunciado
    assert h.connection.timeouts == [servidor.ManipuladorChat._TIMEOUT_DESCARTE_S, 7.5]


def test_descartar_para_quando_o_cliente_para_de_mandar():
    h = _manipulador(corpo=b"abc")
    h.connection = _Conexao(falha_get=OSError("socket fechado"))
    h._descartar_corpo(10 ** 6)           # anunciou 1 MB e mandou 3 bytes
    assert h.rfile.read() == b""
    assert h.connection.timeouts[-1] is None   # sem timeout anterior conhecido


def test_descartar_tem_teto(monkeypatch):
    monkeypatch.setattr(servidor.ManipuladorChat, "_MAX_DESCARTE", 5)
    h = _manipulador(corpo=b"0123456789")
    h.connection = _Conexao()
    h._descartar_corpo(10 ** 12)
    assert h.rfile.read() == b"56789"


@pytest.mark.parametrize("erro", [OSError("timed out"), ValueError("I/O em arquivo fechado")])
def test_descartar_engole_conexao_caida(erro):
    h = _manipulador()
    h.rfile = _LeituraQueFalha(erro)
    h.connection = _Conexao()
    h._descartar_corpo(100)          # não levanta
    assert len(h.connection.timeouts) == 2


def test_descartar_com_settimeout_quebrado_nao_levanta():
    h = _manipulador(corpo=b"abc")
    h.connection = _Conexao(falha_get=RuntimeError("x"), falha_set=OSError("fechado"))
    h._descartar_corpo(3)
    assert h.connection.timeouts == [servidor.ManipuladorChat._TIMEOUT_DESCARTE_S, None]


# ---- _recusou_corpo / _negar_sem_sessao -------------------------------------

def test_recusou_corpo_sem_content_length_nao_recusa():
    h = _manipulador(headers={})
    h._responder = _Gravador()
    assert h._recusou_corpo("/api/enviar") is False
    assert h._responder.chamadas == []


def test_recusou_corpo_recusa_413_e_escreve_rota_em_ascii(monkeypatch, capsys):
    monkeypatch.setattr(servidor, "LIMITE_CORPO_MB", 0)
    h = _manipulador(headers={"Content-Length": " 3 "}, corpo=b"abc")
    h.connection = _Conexao()
    h._responder = _Gravador()
    assert h._recusou_corpo("/api/ação") is True
    assert h.close_connection is True
    status, corpo = h._responder.chamadas[0]
    assert status == 413
    assert "(0,0 MB)" in json.loads(corpo)["erro"]
    assert "recusada em /api/a??o" in capsys.readouterr().out


@pytest.mark.parametrize("valor", ["abc", "", None])
def test_negar_sem_sessao_com_tamanho_invalido_ou_ausente_nao_descarta(valor):
    h = _manipulador(headers={"Content-Length": valor})
    h._responder = _Gravador()
    h._descartar_corpo = _Gravador()
    h._negar_sem_sessao()
    assert h._descartar_corpo.chamadas == []
    assert h.close_connection is True
    status, corpo = h._responder.chamadas[0]
    assert status == 401 and "Sessão inválida" in json.loads(corpo)["erro"]


def test_negar_sem_sessao_descarta_o_corpo_anunciado():
    h = _manipulador(headers={"Content-Length": "42"})
    h._responder = _Gravador()
    h._descartar_corpo = _Gravador()
    h._negar_sem_sessao()
    assert h._descartar_corpo.chamadas == [(42,)]


# ---- _ip / _token_sessao / _ler_corpo_json ----------------------------------

def test_ip_sem_endereco_do_cliente_vira_texto_vazio():
    h = _manipulador()
    del h.client_address
    assert h._ip() == ""


def test_ip_do_cliente():
    assert _manipulador()._ip() == "10.0.0.1"


@pytest.mark.parametrize("auth, esperado", [
    ("Bearer  abc ", "abc"), ("Basic abc", None), ("", None),
])
def test_token_sessao(auth, esperado):
    assert _manipulador(headers={"Authorization": auth})._token_sessao() == esperado


def test_ler_corpo_json_sem_corpo_e_dict_vazio():
    assert _manipulador(headers={})._ler_corpo_json() == {}


# ---- Navegador fechou a conexão antes da resposta ---------------------------

def _quebra(erro):
    def responder(status, corpo):
        raise erro
    return responder


@pytest.mark.parametrize("erro", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError])
def test_planner_conexao_fechada_nao_levanta(monkeypatch, capsys, erro):
    monkeypatch.setitem(servidor.SESSOES_ATIVAS, "tok-x", USUARIO)
    chamadas = []
    monkeypatch.setattr(servidor.planner, "rota_analisar",
                        lambda dados, usuario, ip: chamadas.append(dados) or (200, {"ok": 1}))
    corpo = json.dumps({"tela": 1}).encode()
    h = _manipulador("/api/planner/analisar",
                     {"Authorization": "Bearer tok-x", "Content-Length": str(len(corpo))}, corpo)
    h._responder = _quebra(erro())
    h._api_planner("/api/planner/analisar")
    assert chamadas == [{"tela": 1}]
    assert "fechou a conexão antes da resposta (/api/planner/analisar)" in capsys.readouterr().out


def test_enviar_conexao_fechada_registra_so_a_auditoria_ok(monkeypatch, capsys, eventos):
    monkeypatch.setitem(servidor.SESSOES_ATIVAS, "tok-x", USUARIO)
    monkeypatch.setattr(servidor, "enviar_para_agente", lambda m, s, h: ("tudo certo", None))
    monkeypatch.setattr(servidor, "filtrar_falsos_positivos", lambda r: r)
    monkeypatch.setattr(servidor, "aplicar_limites_ga4", lambda r, j: r)
    corpo = json.dumps({"texto": "audite", "session_id": "s-1"}).encode()
    h = _manipulador("/api/enviar",
                     {"Authorization": "Bearer tok-x", "Content-Length": str(len(corpo))}, corpo)
    h._responder = _quebra(BrokenPipeError())
    h.do_POST()
    assert [(e["acao"], e["status"]) for e in eventos] == [("auditoria", "ok")]
    assert eventos[0]["ip"] == "10.0.0.1"
    assert "fechou a conexão antes da resposta (sessão s-1)" in capsys.readouterr().out


# ---- _enviar / _responder ---------------------------------------------------

def test_enviar_com_tipo_fora_da_lista_vira_octet_stream():
    h = _manipulador()
    h._enviar(200, b"dados", "text/html; charset=evil", [("X-Extra", "1")])
    saida = h.wfile.getvalue()
    cabecalho, _, corpo = saida.partition(b"\r\n\r\n")
    assert corpo == b"dados"
    assert b"Content-Type: application/octet-stream" in cabecalho
    assert b"Content-Length: 5" in cabecalho
    assert b"X-Extra: 1" in cabecalho
    assert b"X-Content-Type-Options: nosniff" in cabecalho


def test_responder_com_surrogate_solto_nao_quebra():
    h = _manipulador()
    h._responder(200, "meio emoji \ud83d")
    assert h.wfile.getvalue().endswith("meio emoji ?".encode("utf-8"))


def test_log_message_fica_em_silencio(capsys):
    _manipulador().log_message("%s", "nada")
    assert capsys.readouterr().out == ""


# ---- Config do front --------------------------------------------------------

def test_config_js_sem_gtm_nao_carrega_o_loader(monkeypatch):
    monkeypatch.setattr(servidor, "GTM_ID", "")
    texto = servidor._script_config()
    assert texto.startswith("window.TAG_CONFIG = ")
    assert "googletagmanager" not in texto
    assert texto.count("\n") == 1


def test_config_js_com_gtm_injeta_o_id_como_texto_js(monkeypatch):
    monkeypatch.setattr(servidor, "GTM_ID", "GTM-TESTE")
    texto = servidor._script_config()
    assert "'dataLayer',\"GTM-TESTE\");" in texto


@pytest.mark.parametrize("assincrono, esperado", [(True, 800000), (False, "TAMANHO")])
def test_config_frontend_limite_da_parte(monkeypatch, assincrono, esperado):
    monkeypatch.setattr(servidor, "MODO_ASSINCRONO", assincrono)
    config = json.loads(servidor._config_frontend_json())
    esperado = servidor.TAMANHO_MAX_PARTE if esperado == "TAMANHO" else esperado
    assert config["limiteParte"] == esperado
    assert config["plataforma"] == {"nome": servidor.NOME_PLATAFORMA}


@pytest.mark.parametrize("usuario, esperado", [
    (None, False), ({}, False), ({"email": "x@y.com"}, False),
    ({"email": " ADMIN@exemplo.com "}, True),
])
def test_eh_admin(monkeypatch, usuario, esperado):
    monkeypatch.setattr(servidor, "EMAILS_ADMIN", ["admin@exemplo.com"])
    assert servidor._eh_admin(usuario) is esperado
