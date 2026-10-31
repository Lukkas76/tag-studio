"""Login Google por fluxo de dispositivo: claims do id_token, POST nos
endpoints OAuth e o andamento do pedido (pendente, slow_down, negado,
expirado, sucesso). Nada sai para a rede: o endpoint do Google é um stub."""
import base64
import io
import json
import types
import urllib.error

import pytest

from tagauditor import login_google

CLIENTE = "cliente-teste.apps.googleusercontent.com"


def _jwt(claims):
    """id_token no formato do Google (header.payload.assinatura), sem padding."""
    def parte(dados):
        texto = json.dumps(dados).encode("utf-8")
        return base64.urlsafe_b64encode(texto).rstrip(b"=").decode("ascii")
    return "{}.{}.assinatura-falsa".format(parte({"alg": "RS256", "typ": "JWT"}), parte(claims))


def _claims(**extra):
    claims = {
        "aud": CLIENTE,
        "email": "  Pessoa@Exemplo.com ",
        "email_verified": True,
        "name": "Pessoa Teste",
        "picture": "https://foto/pessoa.png",
    }
    claims.update(extra)
    return claims


class _GoogleFalso:
    """Substitui _post_form_google: devolve as respostas na ordem e anota as
    chamadas. Uma exceção na fila vira falha de rede."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    def __call__(self, url, campos, timeout=20):
        self.chamadas.append((url, dict(campos)))
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


@pytest.fixture
def google(monkeypatch):
    """Credenciais preenchidas, sem restrição de acesso, pedidos zerados e um
    relógio controlado pelo teste (login_google.time só neste módulo)."""
    monkeypatch.setattr(login_google, "GOOGLE_CLIENT_ID", CLIENTE)
    monkeypatch.setattr(login_google, "GOOGLE_CLIENT_SECRET", "segredo-teste")
    monkeypatch.setattr(login_google, "EMAILS_AUTORIZADOS", set())
    monkeypatch.setattr(login_google, "DOMINIOS_AUTORIZADOS", set())
    monkeypatch.setattr(login_google, "_pedidos_device", {})
    relogio = {"agora": 1_000_000.0}
    monkeypatch.setattr(login_google, "time", types.SimpleNamespace(
        time=lambda: relogio["agora"], sleep=lambda segundos: None))
    return relogio


def _usar_google(monkeypatch, *respostas):
    falso = _GoogleFalso(*respostas)
    monkeypatch.setattr(login_google, "_post_form_google", falso)
    return falso


def _pedido(relogio, pedido_id="p1", **extra):
    dados = {"device_code": "dev-123", "interval": 5, "ultimo_poll": 0.0,
             "expira_em": relogio["agora"] + 600}
    dados.update(extra)
    login_google._pedidos_device[pedido_id] = dados
    return dados


# ---- id_token ---------------------------------------------------------------

def test_jwt_decodifica_o_payload_sem_padding():
    for email in ("a@b.c", "ab@b.c", "abc@b.c"):     # tamanhos que pedem 0, 1 e 2 "="
        assert login_google._decodificar_jwt_payload(_jwt({"email": email})) == {"email": email}


@pytest.mark.parametrize("token", [
    "sem-ponto",
    "a.!!!.c",
    "a." + base64.urlsafe_b64encode(b"nao e json").decode("ascii") + ".c",
    None,
])
def test_jwt_invalido_devolve_none(token):
    assert login_google._decodificar_jwt_payload(token) is None


# ---- claims -----------------------------------------------------------------

def test_claims_validos_viram_usuario(google):
    usuario = login_google._usuario_dos_claims(_claims())
    assert usuario == {
        "email": "pessoa@exemplo.com",
        "nome": "Pessoa Teste",
        "foto": "https://foto/pessoa.png",
        "user_id": login_google._user_id_tagueamento("pessoa@exemplo.com"),
    }


def test_sem_nome_e_foto_usa_o_inicio_do_email(google):
    usuario = login_google._usuario_dos_claims(_claims(name="   ", picture=None))
    assert usuario["nome"] == "pessoa"
    assert usuario["foto"] == ""


@pytest.mark.parametrize("verificado", [True, "true", "TRUE", "1", 1])
def test_email_verificado_aceita_as_formas_do_google(google, verificado):
    assert login_google._usuario_dos_claims(_claims(email_verified=verificado))["email"]


def test_claims_que_nao_sao_dict_sao_recusados(google):
    with pytest.raises(ValueError, match="não devolveu os dados"):
        login_google._usuario_dos_claims(None)


def test_audience_de_outro_aplicativo_e_recusada(google):
    with pytest.raises(ValueError, match="outro aplicativo"):
        login_google._usuario_dos_claims(_claims(aud="outro-cliente"))


@pytest.mark.parametrize("extra", [
    {"email_verified": False},
    {"email_verified": "false"},
    {"email_verified": None},
    {"email": ""},
    {"email": None},
])
def test_email_ausente_ou_nao_verificado_e_recusado(google, extra):
    with pytest.raises(ValueError, match="e-mail verificado"):
        login_google._usuario_dos_claims(_claims(**extra))


def test_lista_de_emails_autorizados(google, monkeypatch):
    monkeypatch.setattr(login_google, "EMAILS_AUTORIZADOS", {"pessoa@exemplo.com"})
    assert login_google._usuario_dos_claims(_claims())["email"] == "pessoa@exemplo.com"
    with pytest.raises(ValueError, match="não está autorizada") as erro:
        login_google._usuario_dos_claims(_claims(email="intrusa@exemplo.com"))
    assert login_google.NOME_PLATAFORMA in str(erro.value)


def test_dominio_autorizado(google, monkeypatch):
    monkeypatch.setattr(login_google, "DOMINIOS_AUTORIZADOS", {"bradesco.com.br"})
    assert login_google._usuario_dos_claims(_claims(email="fulano@bradesco.com.br"))
    with pytest.raises(ValueError, match="não está autorizada"):
        login_google._usuario_dos_claims(_claims(email="fulano@bradesco.com.br.evil.com"))


# ---- POST nos endpoints OAuth ----------------------------------------------

class _Resposta:
    def __init__(self, status, corpo):
        self.status = status
        self._corpo = corpo

    def read(self):
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *erro):
        return False


class _Abridor:
    def __init__(self, resultado):
        self.resultado = resultado
        self.pedidos = []

    def open(self, req, timeout=None):
        self.pedidos.append((req, timeout))
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


@pytest.fixture
def abridor(monkeypatch):
    """Troca a rota e o opener: devolve uma função que instala o resultado."""
    rotas = []
    monkeypatch.setattr(login_google, "rota_google", lambda: "http://proxy.teste:8080")

    def instalar(resultado):
        falso = _Abridor(resultado)

        def construir(proxy):
            rotas.append(proxy)
            return falso
        monkeypatch.setattr(login_google, "_abridor_google", construir)
        falso.rotas = rotas
        return falso
    return instalar


def _erro_http(codigo, corpo):
    return urllib.error.HTTPError("https://oauth2.googleapis.com/token", codigo,
                                  "erro", None, io.BytesIO(corpo))


def test_post_envia_formulario_pela_rota_descoberta(abridor):
    falso = abridor(_Resposta(200, '{"device_code": "x", "nome": "çã"}'.encode("utf-8")))
    status, dados = login_google._post_form_google(
        "https://oauth2.googleapis.com/device/code", {"client_id": "a b", "scope": "openid"},
        timeout=7)
    assert (status, dados) == (200, {"device_code": "x", "nome": "çã"})
    req, timeout = falso.pedidos[0]
    assert timeout == 7
    assert falso.rotas == ["http://proxy.teste:8080"]
    assert req.get_method() == "POST"
    assert req.full_url == "https://oauth2.googleapis.com/device/code"
    assert req.data == b"client_id=a+b&scope=openid"
    assert req.get_header("Content-type") == "application/x-www-form-urlencoded"


def test_post_erro_http_com_json_vira_dict(abridor):
    abridor(_erro_http(428, b'{"error": "authorization_pending"}'))
    assert login_google._post_form_google("https://x", {}) == (428, {"error": "authorization_pending"})


def test_post_erro_http_sem_json_vira_dict_vazio(abridor):
    abridor(_erro_http(502, b"<html>Bad Gateway</html>"))
    assert login_google._post_form_google("https://x", {}) == (502, {})


def test_post_falha_de_rede_propaga(abridor):
    abridor(urllib.error.URLError("sem rota"))
    with pytest.raises(urllib.error.URLError):
        login_google._post_form_google("https://x", {})


# ---- início do fluxo --------------------------------------------------------

def test_inicio_sem_credenciais_explica_a_configuracao(google, monkeypatch):
    monkeypatch.setattr(login_google, "GOOGLE_CLIENT_SECRET", "")
    falso = _usar_google(monkeypatch)
    with pytest.raises(ValueError, match="não configurado"):
        login_google.google_device_inicio()
    assert falso.chamadas == []


def test_inicio_pede_codigo_e_guarda_o_pedido(google, monkeypatch):
    falso = _usar_google(monkeypatch, (200, {
        "device_code": "dev-abc", "user_code": "ABCD-EFGH", "interval": 7,
        "expires_in": 900, "verification_url": "https://www.google.com/device"}))
    resposta = login_google.google_device_inicio()

    assert falso.chamadas == [(login_google.URL_GOOGLE_DEVICE_CODE,
                               {"client_id": CLIENTE, "scope": "openid email profile"})]
    assert resposta["user_code"] == "ABCD-EFGH"
    assert resposta["verification_url"] == "https://www.google.com/device"
    assert len(resposta["pedido_id"]) == 24
    assert login_google._pedidos_device[resposta["pedido_id"]] == {
        "device_code": "dev-abc", "interval": 7, "ultimo_poll": 0.0,
        "expira_em": google["agora"] + 900}


def test_inicio_usa_padroes_quando_o_google_omite_campos(google, monkeypatch):
    _usar_google(monkeypatch, (200, {"device_code": "dev-abc"}))
    resposta = login_google.google_device_inicio()
    pedido = login_google._pedidos_device[resposta["pedido_id"]]
    assert resposta["user_code"] == ""
    assert resposta["verification_url"] == "https://www.google.com/device"
    assert pedido["interval"] == 5
    assert pedido["expira_em"] == google["agora"] + 1800


def test_inicio_limpa_expirados_e_respeita_o_maximo(google, monkeypatch):
    monkeypatch.setattr(login_google, "MAX_PEDIDOS_DEVICE", 2)
    _pedido(google, "velho", expira_em=google["agora"] - 1)
    _pedido(google, "antigo")
    _pedido(google, "recente")
    _usar_google(monkeypatch, (200, {"device_code": "dev-novo"}))
    novo = login_google.google_device_inicio()["pedido_id"]
    assert set(login_google._pedidos_device) == {"recente", novo}


def test_inicio_com_falha_de_rede_aponta_o_proxy(google, monkeypatch):
    _usar_google(monkeypatch, OSError("conexão recusada"))
    with pytest.raises(ValueError, match=r"Não consegui falar com o Google \(conexão recusada\)"):
        login_google.google_device_inicio()


@pytest.mark.parametrize("status, dados", [
    (401, {"error": "unauthorized_client"}),
    (403, {}),
    (400, {"error": "invalid_client"}),
])
def test_inicio_com_chaves_recusadas(google, monkeypatch, status, dados):
    _usar_google(monkeypatch, (status, dados))
    with pytest.raises(ValueError, match="não aceitou as chaves"):
        login_google.google_device_inicio()


def test_inicio_invalid_client_com_descricao_cai_na_mensagem_generica(google, monkeypatch):
    # Comportamento atual: com error_description o "invalid_client" do campo
    # error não é olhado (detalhe = descrição). Só o status 401/403 salva.
    _usar_google(monkeypatch, (400, {"error": "invalid_client",
                                     "error_description": "The OAuth client was not found."}))
    with pytest.raises(ValueError, match="recusou o pedido de código"):
        login_google.google_device_inicio()


def test_inicio_com_outro_erro_mostra_o_http(google, monkeypatch):
    _usar_google(monkeypatch, (400, {"error": "invalid_scope", "error_description": "Escopo ruim"}))
    with pytest.raises(ValueError, match=r"recusou o pedido de código \(HTTP 400\): Escopo ruim"):
        login_google.google_device_inicio()


def test_inicio_200_sem_device_code_e_erro(google, monkeypatch):
    _usar_google(monkeypatch, (200, {"user_code": "ABCD"}))
    with pytest.raises(ValueError, match=r"HTTP 200"):
        login_google.google_device_inicio()
    assert login_google._pedidos_device == {}


# ---- andamento do pedido ----------------------------------------------------

@pytest.mark.parametrize("pedido_id", [None, "", "nao-existe"])
def test_status_de_pedido_desconhecido(google, monkeypatch, pedido_id):
    falso = _usar_google(monkeypatch)
    estado, msg = login_google.google_device_status(pedido_id)
    assert estado == "ERRO" and "não encontrado" in msg
    assert falso.chamadas == []


def test_status_de_codigo_expirado_remove_o_pedido(google, monkeypatch):
    _pedido(google, expira_em=google["agora"] - 1)
    falso = _usar_google(monkeypatch)
    estado, msg = login_google.google_device_status("p1")
    assert estado == "ERRO" and "expirou" in msg
    assert "p1" not in login_google._pedidos_device
    assert falso.chamadas == []


def test_status_respeita_o_intervalo_minimo(google, monkeypatch):
    _pedido(google, ultimo_poll=google["agora"] - 2, interval=5)
    falso = _usar_google(monkeypatch)
    assert login_google.google_device_status("p1") == ("AGUARDANDO", None)
    assert falso.chamadas == []


def test_status_pendente_continua_aguardando(google, monkeypatch):
    pedido = _pedido(google)
    falso = _usar_google(monkeypatch, (428, {"error": "authorization_pending"}))
    assert login_google.google_device_status("p1") == ("AGUARDANDO", None)
    assert falso.chamadas == [(login_google.URL_GOOGLE_TOKEN, {
        "client_id": CLIENTE,
        "client_secret": "segredo-teste",
        "device_code": "dev-123",
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    })]
    assert pedido["ultimo_poll"] == google["agora"]
    assert "p1" in login_google._pedidos_device


def test_status_com_falha_de_rede_tenta_de_novo_depois(google, monkeypatch):
    _pedido(google)
    _usar_google(monkeypatch, OSError("oscilação"))
    assert login_google.google_device_status("p1") == ("AGUARDANDO", None)
    assert "p1" in login_google._pedidos_device


def test_slow_down_aumenta_o_intervalo(google, monkeypatch):
    pedido = _pedido(google, interval=5)
    _usar_google(monkeypatch, (400, {"error": "slow_down"}))
    assert login_google.google_device_status("p1") == ("AGUARDANDO", None)
    assert pedido["interval"] == 10


def test_slow_down_de_pedido_que_sumiu_nao_quebra(google, monkeypatch):
    _pedido(google)

    def responder(url, campos, timeout=20):
        login_google._pedidos_device.clear()     # outro poll terminou o pedido
        return 400, {"error": "slow_down"}
    monkeypatch.setattr(login_google, "_post_form_google", responder)
    assert login_google.google_device_status("p1") == ("AGUARDANDO", None)


def test_sucesso_devolve_o_usuario_e_encerra_o_pedido(google, monkeypatch):
    _pedido(google)
    _usar_google(monkeypatch, (200, {"id_token": _jwt(_claims()), "access_token": "x"}))
    estado, usuario = login_google.google_device_status("p1")
    assert estado == "OK"
    assert usuario["email"] == "pessoa@exemplo.com"
    assert "p1" not in login_google._pedidos_device


@pytest.mark.parametrize("claims, trecho", [
    ({"aud": "outro"}, "outro aplicativo"),
    ({"email_verified": False}, "e-mail verificado"),
    ({"email": "fulano@gmail.com"}, "não está autorizada"),
])
def test_sucesso_com_claims_invalidos_vira_erro(google, monkeypatch, claims, trecho):
    monkeypatch.setattr(login_google, "DOMINIOS_AUTORIZADOS", {"exemplo.com"})
    _pedido(google)
    _usar_google(monkeypatch, (200, {"id_token": _jwt(_claims(**claims))}))
    estado, msg = login_google.google_device_status("p1")
    assert estado == "ERRO" and trecho in msg
    assert "p1" not in login_google._pedidos_device


def test_sucesso_com_id_token_ilegivel_vira_erro(google, monkeypatch):
    _pedido(google)
    _usar_google(monkeypatch, (200, {"id_token": "nao-e-jwt"}))
    estado, msg = login_google.google_device_status("p1")
    assert estado == "ERRO" and "não devolveu os dados" in msg


@pytest.mark.parametrize("status, dados, trecho", [
    (403, {"error": "access_denied"}, "negou o acesso"),
    (400, {"error": "expired_token"}, "expirou"),
    (400, {"error": "invalid_grant"}, "expirou"),
    (400, {"error": "invalid_client", "error_description": "Unauthorized"}, "(Unauthorized)"),
    (400, {"error": "bad_request"}, "(bad_request)"),
    (500, {}, "(HTTP 500)"),
    (200, {"access_token": "sem id_token"}, "(HTTP 200)"),
])
def test_erros_finais_encerram_o_pedido(google, monkeypatch, status, dados, trecho):
    _pedido(google)
    _usar_google(monkeypatch, (status, dados))
    estado, msg = login_google.google_device_status("p1")
    assert estado == "ERRO" and trecho in msg
    assert "p1" not in login_google._pedidos_device
