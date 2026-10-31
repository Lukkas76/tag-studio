import importlib.util
import io
import json
import ssl
import sys
import urllib.error

import pytest

from tagauditor import config, rede

URL = "https://api.exemplo.interno/v1/recurso"


class RespostaFalsa:
    """Imita o objeto devolvido por urlopen (usado como gerenciador de contexto)."""

    def __init__(self, status, corpo):
        self.status = status
        self._corpo = corpo

    def read(self):
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class HTTPErrorIlegivel(urllib.error.HTTPError):
    """HTTPError cujo corpo não dá para ler (conexão caiu no meio)."""

    def read(self, *args):
        raise OSError("conexão encerrada")


@pytest.fixture
def urlopen_falso(monkeypatch):
    """Troca urllib.request.urlopen por um fake: nenhuma conexão real sai do teste.

    Ajuste `estado["resultado"]` para uma RespostaFalsa (sucesso) ou uma
    exceção (que será levantada). Cada chamada fica em `estado["chamadas"]`."""
    estado = {"resultado": RespostaFalsa(200, b"{}"), "chamadas": []}

    def falso(req, timeout=None, context=None):
        estado["chamadas"].append({"req": req, "timeout": timeout, "context": context})
        if isinstance(estado["resultado"], BaseException):
            raise estado["resultado"]
        return estado["resultado"]

    monkeypatch.setattr(rede.urllib.request, "urlopen", falso)
    return estado


# ---- post_json --------------------------------------------------------------

def test_post_envia_json_utf8_com_tls_verificado(urlopen_falso):
    urlopen_falso["resultado"] = RespostaFalsa(201, "criado – ação".encode("utf-8"))

    status, corpo = rede.post_json(URL, {"texto": "ação"}, {"Authorization": "Bearer abc"})

    assert (status, corpo) == (201, "criado – ação")
    chamada = urlopen_falso["chamadas"][0]
    req = chamada["req"]
    assert req.full_url == URL
    assert req.get_method() == "POST"
    assert req.data == json.dumps({"texto": "ação"}, ensure_ascii=False).encode("utf-8")
    assert req.get_header("Content-type") == "application/json"
    assert req.get_header("Accept") == "application/json, text/event-stream"
    assert req.get_header("Authorization") == "Bearer abc"
    assert chamada["context"] is rede.CTX_SSL
    assert chamada["timeout"] == rede.TIMEOUT_SEGUNDOS


def test_post_sem_headers_extras(urlopen_falso):
    status, _ = rede.post_json(URL, {"a": 1})

    assert status == 200
    req = urlopen_falso["chamadas"][0]["req"]
    assert req.get_header("Authorization") is None


def test_post_corpo_com_bytes_invalidos_nao_quebra(urlopen_falso):
    urlopen_falso["resultado"] = RespostaFalsa(200, b"\xff ok")

    assert rede.post_json(URL, {}) == (200, "� ok")


def test_post_erro_http_devolve_status_e_corpo(urlopen_falso):
    urlopen_falso["resultado"] = urllib.error.HTTPError(
        URL, 401, "Unauthorized", {}, io.BytesIO('{"erro": "token inválido"}'.encode("utf-8")))

    assert rede.post_json(URL, {}) == (401, '{"erro": "token inválido"}')


def test_post_erro_http_sem_corpo_legivel_usa_a_descricao(urlopen_falso):
    urlopen_falso["resultado"] = HTTPErrorIlegivel(
        URL, 500, "Internal Server Error", {}, io.BytesIO(b""))

    assert rede.post_json(URL, {}) == (500, "HTTP Error 500: Internal Server Error")


def test_post_sem_conexao_vira_runtime_error(urlopen_falso):
    urlopen_falso["resultado"] = urllib.error.URLError("Name or service not known")

    with pytest.raises(RuntimeError) as erro:
        rede.post_json(URL, {})

    mensagem = str(erro.value)
    assert "Não consegui conectar em " + URL in mensagem
    assert "rede/VPN" in mensagem
    assert "Name or service not known" in mensagem


# ---- get_json ---------------------------------------------------------------

def test_get_sem_corpo_e_com_headers(urlopen_falso):
    urlopen_falso["resultado"] = RespostaFalsa(200, b'{"status": "COMPLETED"}')

    status, corpo = rede.get_json(URL, {"Authorization": "Bearer xyz"})

    assert (status, corpo) == (200, '{"status": "COMPLETED"}')
    chamada = urlopen_falso["chamadas"][0]
    req = chamada["req"]
    assert req.get_method() == "GET"
    assert req.data is None
    assert req.get_header("Accept") == "application/json"
    assert req.get_header("Authorization") == "Bearer xyz"
    assert chamada["context"] is rede.CTX_SSL
    assert chamada["timeout"] == rede.TIMEOUT_SEGUNDOS


def test_get_sem_headers_extras(urlopen_falso):
    assert rede.get_json(URL) == (200, "{}")
    assert urlopen_falso["chamadas"][0]["req"].get_header("Authorization") is None


def test_get_erro_http_devolve_status_e_corpo(urlopen_falso):
    urlopen_falso["resultado"] = urllib.error.HTTPError(
        URL, 404, "Not Found", {}, io.BytesIO(b"execucao inexistente"))

    assert rede.get_json(URL) == (404, "execucao inexistente")


def test_get_erro_http_sem_corpo_legivel_usa_a_descricao(urlopen_falso):
    urlopen_falso["resultado"] = HTTPErrorIlegivel(
        URL, 503, "Service Unavailable", {}, io.BytesIO(b""))

    assert rede.get_json(URL) == (503, "HTTP Error 503: Service Unavailable")


def test_get_sem_conexao_vira_runtime_error(urlopen_falso):
    urlopen_falso["resultado"] = urllib.error.URLError(ConnectionRefusedError("recusada"))

    with pytest.raises(RuntimeError, match="Não consegui conectar em .*recusada"):
        rede.get_json(URL)


# ---- CA interna (ARQUIVO_CA) ------------------------------------------------

def _rede_com_ca(monkeypatch, arquivo_ca):
    """Executa rede.py de novo, num módulo à parte, com ARQUIVO_CA preenchido.

    O tagauditor.rede original (e o CTX_SSL compartilhado com Confluence,
    Google e Figma) não é tocado."""
    monkeypatch.setattr(config, "ARQUIVO_CA", arquivo_ca)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location("tagauditor._rede_teste_ca", rede.__file__)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_ca_inexistente_avisa_e_segue_com_as_cas_do_sistema(monkeypatch, tmp_path, capsys):
    arquivo = str(tmp_path / "nao_existe.pem")

    modulo = _rede_com_ca(monkeypatch, arquivo)

    assert "Não consegui carregar a CA de " + arquivo in capsys.readouterr().out
    assert modulo.CTX_SSL is not rede.CTX_SSL
    assert modulo.CTX_SSL.verify_mode == ssl.CERT_REQUIRED
    assert modulo.CTX_SSL.check_hostname is True


def test_ca_invalida_avisa_e_segue_com_as_cas_do_sistema(monkeypatch, tmp_path, capsys):
    arquivo = tmp_path / "invalida.pem"
    arquivo.write_text("isto não é um certificado", encoding="utf-8")

    modulo = _rede_com_ca(monkeypatch, str(arquivo))

    assert "Não consegui carregar a CA de " + str(arquivo) in capsys.readouterr().out
    assert modulo.CTX_SSL.verify_mode == ssl.CERT_REQUIRED
