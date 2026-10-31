"""Rotas GET/POST do servidor (tagauditor/servidor.py) com servidor real em
porta efêmera. Nada de rede/IA: as funções que o servidor importa (login
Google, Confluence, Planner, Bridge) são trocadas por monkeypatch."""
import html
import http.client
import json
import threading

import pytest

from tagauditor import atividade
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
        return resposta.status, resposta.msg, resposta.read()
    finally:
        conexao.close()


def _postar(porta, caminho, dados, headers=None):
    """POST com corpo JSON (dict) ou texto/bytes já prontos."""
    corpo = dados if isinstance(dados, (str, bytes)) else json.dumps(dados)
    cab = {"Content-Type": "application/json"}
    cab.update(headers or {})
    return _pedir(porta, "POST", caminho, corpo, cab)


def _json_do_servidor(corpo):
    """O que o front faz (TA.textoDoServidor + JSON.parse)."""
    return json.loads(html.unescape(corpo.decode("utf-8")))


@pytest.fixture(autouse=True)
def _isolado(monkeypatch, tmp_path):
    """Nenhum teste grava em logs/ nem em registro.txt do projeto."""
    monkeypatch.setattr(atividade, "_PASTA", str(tmp_path / "logs"))
    monkeypatch.setattr(servidor, "atualizar_registro_login", lambda email: None)


@pytest.fixture
def eventos(monkeypatch):
    """Troca atividade.registrar por um gravador em memória."""
    registrados = []

    def registrar(acao, usuario=None, ip=None, **campos):
        registrados.append(dict(campos, acao=acao, usuario=usuario, ip=ip))

    monkeypatch.setattr(servidor.atividade, "registrar", registrar)
    return registrados


USUARIO = {"email": "fulano@exemplo.com", "nome": "Fulano", "foto": ""}


@pytest.fixture
def logado(monkeypatch):
    monkeypatch.setitem(servidor.SESSOES_ATIVAS, "tok-fulano", USUARIO)
    monkeypatch.setattr(servidor, "EMAILS_ADMIN", ["admin@exemplo.com"])
    return {"Authorization": "Bearer tok-fulano"}


@pytest.fixture
def sessoes_restauradas():
    """O login OK cria uma sessão nova: devolve SESSOES_ATIVAS como estava."""
    antes = dict(servidor.SESSOES_ATIVAS)
    yield
    servidor.SESSOES_ATIVAS.clear()
    servidor.SESSOES_ATIVAS.update(antes)


# ---- GET --------------------------------------------------------------------

@pytest.mark.parametrize("caminho", ["/health", "/healthz", "/ready"])
def test_health_responde_ok(porta, caminho):
    status, cab, corpo = _pedir(porta, "GET", caminho)
    assert status == 200
    assert _json_do_servidor(corpo) == {"status": "ok"}


def test_get_rota_desconhecida_responde_404(porta):
    status, cab, corpo = _pedir(porta, "GET", "/nao-existe")
    assert status == 404
    assert corpo.decode("utf-8") == "Não encontrado"


def test_pagina_aceita_query_string(porta):
    status, cab, _ = _pedir(porta, "GET", "/auditor?origem=home")
    assert status == 200
    assert cab["Content-Type"] == "text/html; charset=utf-8"


def test_estatico_fora_da_lista_responde_404(porta):
    assert _pedir(porta, "GET", "/static/nao-existe.css")[0] == 404


def test_api_me_com_sessao_devolve_usuario(porta, logado):
    status, cab, corpo = _pedir(porta, "GET", "/api/me", headers=logado)
    assert status == 200
    assert _json_do_servidor(corpo) == {"usuario": USUARIO, "admin": False}


def test_api_me_admin(porta, monkeypatch):
    admin = {"email": " Admin@Exemplo.com ", "nome": "Admin"}
    monkeypatch.setitem(servidor.SESSOES_ATIVAS, "tok-admin", admin)
    monkeypatch.setattr(servidor, "EMAILS_ADMIN", ["admin@exemplo.com"])
    status, _, corpo = _pedir(porta, "GET", "/api/me",
                              headers={"Authorization": "Bearer tok-admin"})
    assert status == 200
    assert _json_do_servidor(corpo)["admin"] is True


def test_api_me_token_sem_bearer_e_401(porta, logado):
    status, _, _ = _pedir(porta, "GET", "/api/me", headers={"Authorization": "tok-fulano"})
    assert status == 401


def test_resumo_sem_sessao_e_401(porta):
    status, _, corpo = _pedir(porta, "GET", "/api/resumo")
    assert status == 401
    assert "erro" in _json_do_servidor(corpo)


def test_resumo_com_sessao(porta, logado, monkeypatch):
    pedidos = []

    def ler(**k):
        pedidos.append(k)
        return [{"acao": "auditoria", "email": "fulano@exemplo.com", "status": "ok",
                 "quando": "2026-10-10T10:00:00"}]

    monkeypatch.setattr(servidor.atividade, "ler", ler)
    status, _, corpo = _pedir(porta, "GET", "/api/resumo", headers=logado)
    assert status == 200
    dados = _json_do_servidor(corpo)
    assert dados["usuario"] == {"auditorias": 1, "analises": 0,
                                "ultima_atividade": "2026-10-10T10:00:00"}
    assert dados["plataforma"] == {"auditorias": 1, "analises": 0, "usuarios": 1}
    assert pedidos[0]["limite"] == servidor._LIMITE_LEITURA_RESUMO


def test_resumo_com_falha_na_leitura_e_500(porta, logado, monkeypatch):
    def ler(**k):
        raise RuntimeError("disco <sumiu>")

    monkeypatch.setattr(servidor.atividade, "ler", ler)
    status, _, corpo = _pedir(porta, "GET", "/api/resumo", headers=logado)
    assert status == 500
    assert _json_do_servidor(corpo)["erro"] == "Falha ao montar o resumo de uso: disco <sumiu>"
    assert b"<sumiu>" not in corpo       # saiu escapado


# ---- Limite do corpo (413 / 400) --------------------------------------------

def test_corpo_acima_do_limite_responde_413(porta, monkeypatch, logado, eventos):
    monkeypatch.setattr(servidor, "LIMITE_CORPO_MB", 1)
    corpo = b"x" * (1024 * 1024 + 512 * 1024)       # 1,5 MB
    status, cab, resposta = _pedir(porta, "POST", "/api/feedback", corpo,
                                   {**logado, "Content-Type": "application/json"})
    assert status == 413
    erro = _json_do_servidor(resposta)["erro"]
    assert "(1,5 MB)" in erro and "limite de 1 MB" in erro
    assert eventos == []          # a rota nem chegou a rodar


@pytest.mark.parametrize("valor", ["abc", "-5"])
def test_content_length_invalido_responde_400(porta, valor):
    status, _, corpo = _pedir(porta, "POST", "/api/feedback", None,
                              {"Content-Length": valor})
    assert status == 400
    assert "Content-Length" in _json_do_servidor(corpo)["erro"]


def test_post_sem_content_length_segue_para_a_rota(porta, logado, eventos):
    """Sem o cabeçalho Content-Length o limite não se aplica (logout sem corpo)."""
    conexao = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
    try:
        conexao.putrequest("POST", "/api/logout")
        conexao.putheader("Authorization", logado["Authorization"])
        conexao.endheaders()
        resposta = conexao.getresponse()
        status, corpo = resposta.status, resposta.read()
    finally:
        conexao.close()
    assert status == 200
    assert _json_do_servidor(corpo) == {"success": True}
    assert "tok-fulano" not in servidor.SESSOES_ATIVAS
    assert [e["acao"] for e in eventos] == ["logout"]


# ---- Login Google -----------------------------------------------------------

def test_login_inicio_devolve_o_pedido(porta, monkeypatch):
    pedido = {"pedido_id": "p1", "codigo": "ABCD-EFGH", "url": "https://www.google.com/device"}
    monkeypatch.setattr(servidor, "google_device_inicio", lambda: pedido)
    status, _, corpo = _postar(porta, "/api/login/inicio", b"")
    assert status == 200
    assert _json_do_servidor(corpo) == pedido


def test_login_inicio_nao_configurado_responde_401(porta, monkeypatch, eventos):
    def inicio():
        raise ValueError("Login Google não configurado.")

    monkeypatch.setattr(servidor, "google_device_inicio", inicio)
    status, _, corpo = _postar(porta, "/api/login/inicio", b"")
    assert status == 401
    assert _json_do_servidor(corpo) == {"erro": "Login Google não configurado."}
    assert eventos[0]["acao"] == "login_falha"
    assert eventos[0]["motivo"] == "Login Google não configurado."
    assert eventos[0]["ip"] == "127.0.0.1"


def test_login_inicio_com_falha_inesperada_responde_400(porta, monkeypatch):
    def inicio():
        raise RuntimeError("rede caiu")

    monkeypatch.setattr(servidor, "google_device_inicio", inicio)
    status, _, corpo = _postar(porta, "/api/login/inicio", b"")
    assert status == 400
    assert _json_do_servidor(corpo) == {"erro": "Requisição inválida."}


def test_login_status_aguardando(porta, monkeypatch):
    recebidos = []

    def status_google(pedido_id):
        recebidos.append(pedido_id)
        return "AGUARDANDO", None

    monkeypatch.setattr(servidor, "google_device_status", status_google)
    status, _, corpo = _postar(porta, "/api/login/status", {"pedido_id": "  p1  "})
    assert status == 200
    assert _json_do_servidor(corpo) == {"status": "aguardando"}
    assert recebidos == ["p1"]


def test_login_status_ok_cria_sessao(porta, monkeypatch, eventos, sessoes_restauradas):
    usuario = {"email": "admin@exemplo.com", "nome": "Admin", "foto": "f.png"}
    registros = []
    monkeypatch.setattr(servidor, "google_device_status", lambda pedido_id: ("OK", usuario))
    monkeypatch.setattr(servidor, "atualizar_registro_login", registros.append)
    monkeypatch.setattr(servidor, "EMAILS_ADMIN", ["admin@exemplo.com"])
    status, _, corpo = _postar(porta, "/api/login/status", {"pedido_id": "p1"})
    assert status == 200
    dados = _json_do_servidor(corpo)
    assert dados["success"] is True and dados["admin"] is True
    assert dados["usuario"] == usuario
    assert len(dados["token"]) == 32
    assert servidor.SESSOES_ATIVAS[dados["token"]] == usuario
    assert registros == ["admin@exemplo.com"]
    assert [e["acao"] for e in eventos] == ["login"]

    # o token recebido abre a sessão em /api/me
    status, _, corpo = _pedir(porta, "GET", "/api/me",
                              headers={"Authorization": "Bearer " + dados["token"]})
    assert status == 200 and _json_do_servidor(corpo)["usuario"] == usuario


def test_login_status_negado_responde_401(porta, monkeypatch, eventos):
    monkeypatch.setattr(servidor, "google_device_status",
                        lambda pedido_id: ("ERRO", "Acesso negado pelo usuário."))
    status, _, corpo = _postar(porta, "/api/login/status", {"pedido_id": "p1"})
    assert status == 401
    assert _json_do_servidor(corpo) == {"erro": "Acesso negado pelo usuário."}
    assert eventos[0]["acao"] == "login_falha"


def test_login_status_corpo_invalido_responde_400(porta, monkeypatch):
    monkeypatch.setattr(servidor, "google_device_status",
                        lambda pedido_id: pytest.fail("não devia chamar o Google"))
    status, _, corpo = _postar(porta, "/api/login/status", "isto não é json")
    assert status == 400
    assert _json_do_servidor(corpo) == {"erro": "Requisição inválida."}


# ---- Logout -----------------------------------------------------------------

def test_logout_encerra_a_sessao(porta, logado, eventos):
    status, _, corpo = _postar(porta, "/api/logout", b"", logado)
    assert status == 200
    assert _json_do_servidor(corpo) == {"success": True}
    assert "tok-fulano" not in servidor.SESSOES_ATIVAS
    assert eventos[0]["acao"] == "logout" and eventos[0]["usuario"] == USUARIO
    # sessão já encerrada: o 2º logout responde ok e não registra nada
    status, _, _ = _postar(porta, "/api/logout", b"", logado)
    assert status == 200
    assert len(eventos) == 1


# ---- Feedback ---------------------------------------------------------------

def test_feedback_sem_sessao_descarta_o_corpo_e_responde_401(porta, eventos):
    status, cab, corpo = _postar(porta, "/api/feedback", {"voto": "like", "x": "y" * 5000})
    assert status == 401
    assert "erro" in _json_do_servidor(corpo)
    assert eventos == []


def test_feedback_registra_o_voto(porta, logado, eventos):
    dados = {"voto": " LIKE ", "ferramenta": "Planner", "session_id": " s-1 ",
             "msg_id": "m-9", "comentario": "  ótimo \ud83d  "}
    status, _, corpo = _postar(porta, "/api/feedback", dados, logado)
    assert status == 200
    assert _json_do_servidor(corpo) == {"success": True}
    ev = eventos[0]
    assert ev["acao"] == "feedback" and ev["usuario"] == USUARIO
    assert ev["voto"] == "like" and ev["ferramenta"] == "planner"
    assert ev["sessao"] == "s-1" and ev["msg_id"] == "m-9"
    assert ev["comentario"] == "ótimo �"      # meio emoji vira U+FFFD


def test_feedback_ferramenta_desconhecida_vira_auditor(porta, logado, eventos):
    status, _, _ = _postar(porta, "/api/feedback",
                           {"voto": "dislike", "ferramenta": "outra"}, logado)
    assert status == 200
    assert eventos[0]["ferramenta"] == "auditor"
    assert eventos[0]["voto"] == "dislike"
    assert eventos[0]["comentario"] == "" and eventos[0]["sessao"] == ""


def test_feedback_voto_invalido_responde_400(porta, logado, eventos):
    status, _, corpo = _postar(porta, "/api/feedback", {"voto": "talvez"}, logado)
    assert status == 400
    assert _json_do_servidor(corpo) == {"erro": "Voto inválido."}
    assert eventos == []


def test_feedback_corpo_invalido_responde_400(porta, logado):
    status, _, corpo = _postar(porta, "/api/feedback", "[1, 2", logado)
    assert status == 400
    assert _json_do_servidor(corpo) == {"erro": "Requisição inválida."}


# ---- Confluence -------------------------------------------------------------

URL_CONFLUENCE = "https://confluence.exemplo.com/x/abc"


def test_confluence_devolve_o_arquivo(porta, logado, eventos, monkeypatch):
    recebidos = []
    resultado = {"arquivo": {"nome": "plano.json", "conteudo": '{"eventos": []}'}}

    def baixar(url, nome):
        recebidos.append((url, nome))
        return resultado

    monkeypatch.setattr(servidor, "confluence_baixar_json", baixar)
    status, _, corpo = _postar(porta, "/api/confluence",
                               {"url": "  " + URL_CONFLUENCE + " ", "nome": "plano.json"}, logado)
    assert status == 200
    assert _json_do_servidor(corpo) == resultado
    assert recebidos == [(URL_CONFLUENCE, "plano.json")]
    ev = eventos[0]
    assert ev["acao"] == "confluence" and ev["status"] == "ok"
    assert ev["arquivo"] == "plano.json" and ev["caracteres"] == len('{"eventos": []}')


def test_confluence_com_varios_anexos_devolve_opcoes(porta, logado, eventos, monkeypatch):
    resultado = {"opcoes": [{"nome": "a.json"}, {"nome": "b.json"}]}
    monkeypatch.setattr(servidor, "confluence_baixar_json", lambda url, nome: resultado)
    status, _, corpo = _postar(porta, "/api/confluence", {"url": URL_CONFLUENCE}, logado)
    assert status == 200
    assert _json_do_servidor(corpo) == resultado
    assert eventos[0]["status"] == "opcoes" and eventos[0]["anexos"] == 2


def test_confluence_url_invalida_responde_400(porta, logado, eventos, monkeypatch):
    def baixar(url, nome):
        raise ValueError("URL do Confluence inválida.")

    monkeypatch.setattr(servidor, "confluence_baixar_json", baixar)
    status, _, corpo = _postar(porta, "/api/confluence", {"url": "x"}, logado)
    assert status == 400
    assert _json_do_servidor(corpo) == {"erro": "URL do Confluence inválida."}
    assert eventos[0]["status"] == "erro" and eventos[0]["url"] == "x"


def test_confluence_fora_do_ar_responde_502(porta, logado, eventos, monkeypatch):
    def baixar(url, nome):
        raise RuntimeError("timeout")

    monkeypatch.setattr(servidor, "confluence_baixar_json", baixar)
    status, _, corpo = _postar(porta, "/api/confluence", {"url": URL_CONFLUENCE}, logado)
    assert status == 502
    assert _json_do_servidor(corpo) == {"erro": "Falha ao buscar no Confluence: timeout"}
    assert eventos[0]["erro"] == "timeout"


# ---- Planner ----------------------------------------------------------------

def test_planner_sem_sessao_responde_401(porta, monkeypatch):
    monkeypatch.setattr(servidor.planner, "rota_figma",
                        lambda *a: pytest.fail("não devia chamar o Planner"))
    status, _, _ = _postar(porta, "/api/planner/figma", {"url": "x"})
    assert status == 401


@pytest.mark.parametrize("rota, funcao", [
    ("/api/planner/figma", "rota_figma"),
    ("/api/planner/analisar", "rota_analisar"),
])
def test_planner_repassa_para_o_modulo(porta, logado, monkeypatch, rota, funcao):
    chamadas = []

    def falsa(dados, usuario, ip):
        chamadas.append((funcao, dados, usuario, ip))
        return 201, {"ok": funcao, "html": "<b>"}

    monkeypatch.setattr(servidor.planner, "rota_figma", falsa)
    monkeypatch.setattr(servidor.planner, "rota_analisar", falsa)
    status, _, corpo = _postar(porta, rota + "?v=1", {"telas": [1]}, logado)
    assert status == 201
    assert _json_do_servidor(corpo) == {"ok": funcao, "html": "<b>"}
    assert chamadas == [(funcao, {"telas": [1]}, USUARIO, "127.0.0.1")]


def test_planner_corpo_que_nao_e_json_chega_como_none(porta, logado, monkeypatch):
    recebidos = []

    def analisar(dados, usuario, ip):
        recebidos.append(dados)
        return 400, {"erro": "Corpo inválido."}

    monkeypatch.setattr(servidor.planner, "rota_analisar", analisar)
    status, _, corpo = _postar(porta, "/api/planner/analisar", "{quebrado", logado)
    assert status == 400
    assert recebidos == [None]


def test_planner_com_excecao_responde_500(porta, logado, monkeypatch):
    def figma(dados, usuario, ip):
        raise RuntimeError("bug")

    monkeypatch.setattr(servidor.planner, "rota_figma", figma)
    status, _, corpo = _postar(porta, "/api/planner/figma", {}, logado)
    assert status == 500
    assert _json_do_servidor(corpo) == {"erro": "Erro interno do Planner. Tente de novo."}


# ---- Rota inválida e /api/enviar ---------------------------------------------

def test_post_rota_desconhecida_responde_404(porta):
    status, _, corpo = _postar(porta, "/api/nada", b"")
    assert status == 404
    assert _json_do_servidor(corpo) == {"erro": "Rota inválida."}


def test_enviar_sem_sessao_responde_401(porta, monkeypatch):
    monkeypatch.setattr(servidor, "enviar_para_agente",
                        lambda *a: pytest.fail("não devia chamar a IA"))
    status, _, _ = _postar(porta, "/api/enviar", {"texto": "oi"})
    assert status == 401


@pytest.fixture
def ia_falsa(monkeypatch):
    """IA e pós-processamento falsos: guarda o que recebeu."""
    chamadas = {"agente": [], "filtro": [], "limites": []}

    def agente(mensagem, sessao, historico):
        chamadas["agente"].append((mensagem, sessao, historico))
        return "| Evento | Status |\n<ok>", {"entrada": 10, "saida": 5, "total": 15, "fonte": "api"}

    def filtro(texto):
        chamadas["filtro"].append(texto)
        return texto + " [filtrado]"

    def limites(texto, json_anexo):
        chamadas["limites"].append(json_anexo)
        return texto + " [limites]"

    monkeypatch.setattr(servidor, "enviar_para_agente", agente)
    monkeypatch.setattr(servidor, "filtrar_falsos_positivos", filtro)
    monkeypatch.setattr(servidor, "aplicar_limites_ga4", limites)
    return chamadas


def test_enviar_audita_e_registra(porta, logado, eventos, ia_falsa):
    historico = [{"role": "user", "content": "antes"}]
    dados = {"texto": "Audite o plano", "session_id": " s-7 ", "historico": historico}
    status, _, corpo = _postar(porta, "/api/enviar", dados, logado)
    assert status == 200
    resposta = _json_do_servidor(corpo)
    assert resposta["resposta"] == "| Evento | Status |\n<ok> [filtrado] [limites]"
    assert resposta["user_content"] == "Audite o plano"
    assert ia_falsa["agente"] == [("Audite o plano", "s-7", historico)]
    assert ia_falsa["limites"] == [None]
    ev = eventos[0]
    assert ev["acao"] == "auditoria" and ev["status"] == "ok"
    assert ev["sessao"] == "s-7" and ev["com_anexo"] is False
    assert ev["caracteres"] == len("Audite o plano")
    assert ev["caracteres_resposta"] == len(resposta["resposta"])
    assert (ev["tokens_entrada"], ev["tokens_saida"], ev["tokens_total"],
            ev["tokens_fonte"]) == (10, 5, 15, "api")


def test_enviar_sem_sessao_id_usa_a_do_servidor_e_historico_invalido_vira_none(
        porta, logado, eventos, ia_falsa, monkeypatch):
    monkeypatch.setattr(servidor, "SESSION_ID", "865000000001")
    status, _, _ = _postar(porta, "/api/enviar",
                           {"texto": "oi", "historico": "não é lista"}, logado)
    assert status == 200
    assert ia_falsa["agente"] == [("oi", "865000000001", None)]


def test_enviar_sem_sessao_nenhuma_usa_sem_sessao(porta, logado, eventos, ia_falsa, monkeypatch):
    monkeypatch.setattr(servidor, "SESSION_ID", None)
    monkeypatch.setattr(servidor, "enviar_para_agente",
                        lambda m, s, h: (ia_falsa["agente"].append((m, s, h)) or "ok", None))
    status, _, _ = _postar(porta, "/api/enviar", {"texto": "oi"}, logado)
    assert status == 200
    assert ia_falsa["agente"][0][1] == "sem-sessao"
    ev = eventos[0]
    assert (ev["tokens_entrada"], ev["tokens_total"], ev["tokens_fonte"]) == (0, 0, "")


def test_enviar_com_anexo_json(porta, logado, eventos, ia_falsa):
    anexo = json.dumps({"eventos": [{"nome": "clique"}]})
    status, _, corpo = _postar(porta, "/api/enviar",
                               {"texto": "audite", "json_anexo": anexo}, logado)
    assert status == 200
    assert ia_falsa["limites"] == [anexo]
    mensagem = ia_falsa["agente"][0][0]
    assert mensagem.startswith("audite") and "clique" in mensagem
    assert eventos[0]["com_anexo"] is True


def test_enviar_anexo_invalido_responde_400(porta, logado, eventos, ia_falsa):
    status, _, corpo = _postar(porta, "/api/enviar",
                               {"texto": "audite", "json_anexo": "{nao é json"}, logado)
    assert status == 400
    assert "não é um JSON válido" in _json_do_servidor(corpo)["erro"]
    assert ia_falsa["agente"] == []
    ev = eventos[0]
    assert ev["acao"] == "auditoria" and ev["status"] == "erro" and ev["com_anexo"] is True


def test_enviar_sem_texto_nem_anexo_responde_400(porta, logado, eventos, ia_falsa):
    status, _, corpo = _postar(porta, "/api/enviar", {"texto": "   "}, logado)
    assert status == 400
    assert _json_do_servidor(corpo) == {"erro": "Digite uma mensagem ou anexe um arquivo JSON."}


def test_enviar_com_falha_da_ia_responde_502(porta, logado, eventos, monkeypatch):
    def agente(mensagem, sessao, historico):
        raise RuntimeError("Bridge fora do ar")

    monkeypatch.setattr(servidor, "enviar_para_agente", agente)
    status, _, corpo = _postar(porta, "/api/enviar", {"texto": "oi", "session_id": "s"}, logado)
    assert status == 502
    assert _json_do_servidor(corpo) == {"erro": "Bridge fora do ar"}
    ev = eventos[0]
    assert ev["status"] == "erro" and ev["erro"] == "Bridge fora do ar"
    assert ev["caracteres"] == 2
