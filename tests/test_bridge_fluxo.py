"""Bridge: token, disparo assíncrono, polling do workflow, modo síncrono legado.

Nenhuma chamada sai do teste: post_json/get_json do bridge viram uma rede
falsa com respostas roteiradas, e o relógio (time) do bridge é falso — as
pausas só avançam o relógio, sem esperar de verdade."""
import copy
import json
from types import SimpleNamespace

import pytest

from tagauditor import bridge, historico


def token_n(n):
    """Token que o login falso devolve na n-ésima chamada."""
    return "tok{:02d}-".format(n) + "x" * 30


def bearer(n):
    return {"Authorization": "Bearer " + token_n(n)}


class RedeFalsa:
    """Substitui post_json/get_json do bridge.

    logins: respostas do login-servico, em ordem (vazia = token novo, token_n)
    posts:  respostas dos demais POSTs, em ordem
    gets:   respostas dos GETs (status do workflow), em ordem
    Uma resposta é (status, corpo) ou uma exceção (levantada na chamada)."""

    def __init__(self):
        self.logins, self.posts, self.gets = [], [], []
        self.chamadas = []
        self.tokens_emitidos = 0

    @staticmethod
    def _proxima(fila):
        resposta = fila.pop(0)
        if isinstance(resposta, BaseException):
            raise resposta
        return resposta

    def post_json(self, url, corpo, headers_extras=None):
        self.chamadas.append(("POST", url, copy.deepcopy(corpo), dict(headers_extras or {})))
        if url == bridge.URL_LOGIN:
            if self.logins:
                return self._proxima(self.logins)
            self.tokens_emitidos += 1
            return 200, json.dumps({"access_token": token_n(self.tokens_emitidos)})
        return self._proxima(self.posts)

    def get_json(self, url, headers_extras=None):
        self.chamadas.append(("GET", url, None, dict(headers_extras or {})))
        return self._proxima(self.gets)

    def logins_feitos(self):
        return [c for c in self.chamadas if c[1] == bridge.URL_LOGIN]

    def envios(self, url=None):
        """Chamadas que não são login (opcionalmente só de uma URL)."""
        return [c for c in self.chamadas
                if c[1] != bridge.URL_LOGIN and (url is None or c[1] == url)]


class RelogioFalso:
    """Substitui o módulo time dentro do bridge: sleep só avança o relógio."""

    def __init__(self, agora=1_000_000.0):
        self.agora = agora
        self.pausas = []

    def time(self):
        return self.agora

    def sleep(self, segundos):
        self.pausas.append(segundos)
        self.agora += segundos


@pytest.fixture(autouse=True)
def amb(monkeypatch):
    rede = RedeFalsa()
    relogio = RelogioFalso()
    monkeypatch.setattr(bridge, "post_json", rede.post_json)
    monkeypatch.setattr(bridge, "get_json", rede.get_json)
    monkeypatch.setattr(bridge, "time", relogio)
    # estado global do módulo isolado por teste
    monkeypatch.setitem(bridge._token_cache, "valor", None)
    monkeypatch.setitem(bridge._token_cache, "obtido_em", 0.0)
    monkeypatch.setitem(bridge._cache_system_prompt, "texto", None)
    monkeypatch.setattr(historico, "_historico", {})
    # credenciais e configuração fixas (com ou sem segredos.py)
    for nome, valor in {
        "IDENTIFICADOR": "servico-teste",
        "SENHA": "senha-teste",
        "TOKEN_VALIDADE_SEGUNDOS": 25 * 60,
        "SYSTEM_PROMPT_REGRAS": "REGRAS DE TESTE",
        "ENVIAR_SYSTEM_PROMPT": True,
        "MODO_ASSINCRONO": True,
        "ASYNC_VIA_TEXTGEN": True,
        "MODELO_LLM": "az001;modelo-teste",
        "OPENAI_API_VERSION": "2025-01-01-preview",
        "REASONING_EFFORT": "high",
        "MAX_TOKENS_RESPOSTA": 16000,
        "TEMPERATURA": None,
        "WORKFLOW_CONFIG_CODE": "CD_TESTE",
        "WORKFLOW_FORMATO": "messages",
        "WORKFLOW_INPUT_PATH": "input_text",
        "MODO_STREAM": False,
        "STREAM_NO_PAYLOAD": True,
        "ASYNC_NO_PAYLOAD": False,
        "TENTATIVAS_GATEWAY": 3,
        "PAUSA_ENTRE_TENTATIVAS": 2,
        "POLL_INTERVALO_SEG": 3,
        "POLL_TIMEOUT_SEG": 30,
    }.items():
        monkeypatch.setattr(bridge, nome, valor)
    # o filtro de falsos positivos tem testes próprios; aqui só marca que rodou
    monkeypatch.setattr(bridge, "filtrar_falsos_positivos", lambda t: t + " [filtrado]")
    return SimpleNamespace(rede=rede, relogio=relogio, mp=monkeypatch)


@pytest.fixture
def sincrono(amb):
    amb.mp.setattr(bridge, "MODO_ASSINCRONO", False)
    return amb


# ---- Token de serviço -------------------------------------------------------

def test_login_devolve_token_e_reaproveita_o_cache(amb):
    assert bridge.obter_token() == token_n(1)
    assert bridge.obter_token() == token_n(1)

    logins = amb.rede.logins_feitos()
    assert len(logins) == 1
    assert logins[0][2] == {"identificador": "servico-teste", "senha": "senha-teste"}


def test_forcar_ignora_o_cache(amb):
    bridge.obter_token()
    assert bridge.obter_token(forcar=True) == token_n(2)
    assert bridge.obter_token() == token_n(2)


def test_token_vencido_e_renovado(amb):
    bridge.obter_token()
    amb.relogio.agora += 25 * 60 + 1

    assert bridge.obter_token() == token_n(2)


def test_login_com_erro_http(amb):
    amb.rede.logins = [(401, "credencial inválida")]

    with pytest.raises(RuntimeError, match=r"Falha no login \(401\): credencial inválida"):
        bridge.obter_token()
    assert bridge._token_cache["valor"] is None


def test_login_que_responde_o_token_em_texto_puro(amb):
    token = "eyJ" + "b" * 40
    amb.rede.logins = [(200, token + "\n")]

    assert bridge.obter_token() == token


@pytest.mark.parametrize("corpo", ["Bem-vindo ao serviço de login", '{"ok": true}'])
def test_login_sem_token_na_resposta(amb, corpo):
    amb.rede.logins = [(200, corpo)]

    with pytest.raises(RuntimeError, match="não encontrei o token"):
        bridge.obter_token()


# ---- Renovador de token (thread de 2º plano) --------------------------------

class _FimDoLoop(Exception):
    pass


def _parar_na_segunda_pausa(amb):
    """O renovador é um loop infinito: deixa rodar 1 volta e interrompe."""
    pausa_original = amb.relogio.sleep

    def pausa(segundos):
        if amb.relogio.pausas:
            raise _FimDoLoop
        pausa_original(segundos)

    amb.mp.setattr(amb.relogio, "sleep", pausa)


def test_renovador_busca_token_quando_nao_ha_nenhum(amb):
    _parar_na_segunda_pausa(amb)

    with pytest.raises(_FimDoLoop):
        bridge.renovador_token()

    assert amb.relogio.pausas == [60]
    assert bridge._token_cache["valor"] == token_n(1)


def test_renovador_nao_renova_token_fresco(amb):
    bridge._token_cache.update(valor="token-fresco", obtido_em=amb.relogio.agora)
    _parar_na_segunda_pausa(amb)

    with pytest.raises(_FimDoLoop):
        bridge.renovador_token()

    assert amb.rede.logins_feitos() == []
    assert bridge._token_cache["valor"] == "token-fresco"


def test_renovador_renova_token_perto_de_vencer(amb):
    # faltam 4 min; depois da pausa de 1 min, faltam 3 (< margem de 5 min)
    bridge._token_cache.update(valor="token-velho",
                               obtido_em=amb.relogio.agora - (25 * 60 - 4 * 60))
    _parar_na_segunda_pausa(amb)

    with pytest.raises(_FimDoLoop):
        bridge.renovador_token()

    assert bridge._token_cache["valor"] == token_n(1)


def test_renovador_sobrevive_a_falha_de_login(amb, capsys):
    amb.rede.logins = [(500, "fora do ar")]
    _parar_na_segunda_pausa(amb)

    with pytest.raises(_FimDoLoop):
        bridge.renovador_token()

    assert "Falha na renovação em 2º plano" in capsys.readouterr().out
    assert bridge._token_cache["valor"] is None


# ---- Disparo com retry (_postar_disparo) ------------------------------------

URL_X = "https://bridge.teste/disparo"


def test_disparo_aceito_de_primeira(amb):
    amb.rede.posts = [(202, '{"workflow_execution_id": "w1"}')]

    assert bridge._postar_disparo(URL_X, {"a": 1}, "teste") == (
        202, '{"workflow_execution_id": "w1"}', 1)
    assert amb.rede.envios() == [("POST", URL_X, {"a": 1}, bearer(1))]
    assert amb.relogio.pausas == []


def test_disparo_com_401_renova_o_token_e_repete(amb):
    amb.rede.posts = [(401, "expirado"), (200, "ok")]

    assert bridge._postar_disparo(URL_X, {}, "teste") == (200, "ok", 1)
    assert [c[3] for c in amb.rede.envios()] == [bearer(1), bearer(2)]


def test_disparo_transitorio_e_retentado(amb, capsys):
    amb.rede.posts = [(504, "<html>504 Gateway Time-out</html>"), (200, "ok")]

    assert bridge._postar_disparo(URL_X, {}, "teste") == (200, "ok", 2)
    assert amb.relogio.pausas == [2]
    saida = capsys.readouterr().out
    assert "HTTP 504 (transitório)" in saida
    assert "disparo aceito na tentativa 2" in saida


def test_disparo_com_erro_definitivo_nao_retenta(amb):
    amb.rede.posts = [(400, "payload inválido")]

    assert bridge._postar_disparo(URL_X, {}, "teste") == (400, "payload inválido", 1)
    assert len(amb.rede.envios()) == 1
    assert amb.relogio.pausas == []


def test_disparo_transitorio_em_todas_as_tentativas(amb):
    amb.rede.posts = [(504, "a"), (502, "b"), (429, "c")]

    assert bridge._postar_disparo(URL_X, {}, "teste") == (429, "c", 3)
    assert amb.relogio.pausas == [2, 2]


def test_disparo_sem_conexao_em_todas_as_tentativas(amb, capsys):
    amb.rede.posts = [RuntimeError("sem rede")] * 3

    with pytest.raises(RuntimeError,
                       match=r"Não consegui conectar para teste após 3 tentativas.*sem rede"):
        bridge._postar_disparo(URL_X, {}, "teste")
    assert len(amb.rede.envios()) == 3
    assert amb.relogio.pausas == [2, 2]
    assert "sem resposta - tentando de novo" in capsys.readouterr().out


def test_disparo_com_login_fora_do_ar(amb):
    amb.rede.logins = [(503, "login indisponível")] * 3

    with pytest.raises(RuntimeError, match=r"após 3 tentativas.*Falha no login \(503\)"):
        bridge._postar_disparo(URL_X, {}, "teste")
    assert amb.rede.envios() == []


# ---- Workflow: montagem e início --------------------------------------------

def test_props_workflow_formato_messages(amb):
    assert bridge._montar_props_workflow("plano") == [{
        "full_path": "messages",
        "actual_value": [{"role": "system", "content": "REGRAS DE TESTE"},
                         {"role": "user", "content": "plano"}],
        "is_valid": True,
    }]


def test_props_workflow_formato_messages_sem_system_prompt(amb):
    amb.mp.setattr(bridge, "ENVIAR_SYSTEM_PROMPT", False)

    props = bridge._montar_props_workflow("plano")

    assert props[0]["actual_value"] == [{"role": "user", "content": "plano"}]


def test_props_workflow_formato_campo_de_entrada(amb):
    amb.mp.setattr(bridge, "WORKFLOW_FORMATO", "input")

    assert bridge._montar_props_workflow("plano") == [
        {"full_path": "input_text", "actual_value": "plano", "is_valid": True},
        {"full_path": "system_prompt", "actual_value": "REGRAS DE TESTE", "is_valid": True},
    ]

    amb.mp.setattr(bridge, "ENVIAR_SYSTEM_PROMPT", False)
    assert bridge._montar_props_workflow("plano") == [
        {"full_path": "input_text", "actual_value": "plano", "is_valid": True},
    ]


def test_iniciar_workflow_devolve_o_execution_id(amb):
    amb.rede.posts = [(202, '{"data": {"execution_id": "e-9"}}')]

    assert bridge._iniciar_workflow("plano") == "e-9"
    _, url, payload, _ = amb.rede.envios()[0]
    assert url == bridge.URL_WORKFLOW_START
    assert payload["workflow_configuration_code"] == "CD_TESTE"
    passo = payload["input_collection"]["input_properties"][0]
    assert passo["workflow_step_order"] == 0
    assert passo["workflow_step_properties"][0]["full_path"] == "messages"


def test_iniciar_workflow_com_erro_http(amb):
    amb.rede.posts = [(400, "configuração inexistente")]

    with pytest.raises(RuntimeError, match=r"Falha ao iniciar o workflow \(HTTP 400 em 1 "
                                           r"tentativa\(s\)\): configuração inexistente"):
        bridge._iniciar_workflow("plano")


def test_iniciar_workflow_sem_execution_id(amb):
    amb.rede.posts = [(200, '{"ok": true}')]

    with pytest.raises(RuntimeError, match="não encontrei o workflow_execution_id"):
        bridge._iniciar_workflow("plano")


# ---- Workflow: polling do resultado -----------------------------------------

def _status(**campos):
    return 200, json.dumps(campos)


def test_aguardar_ate_concluir_com_uso_real(amb, capsys):
    amb.rede.gets = [
        _status(status="RUNNING"),
        _status(status="RUNNING"),
        _status(status="COMPLETED", data={"output_text": "Laudo"},
                usage={"prompt_tokens": 10, "completion_tokens": 2}),
    ]

    texto, uso = bridge._aguardar_workflow("w1")

    assert texto == "Laudo"
    assert uso == {"entrada": 10, "saida": 2, "total": 12, "fonte": "api"}
    assert [c[1] for c in amb.rede.envios()] == [bridge.URL_WORKFLOW_STATUS + "w1"] * 3
    assert amb.relogio.pausas == [3, 3]
    saida = capsys.readouterr().out
    assert saida.count("1ª resposta de status") == 1
    assert saida.count("status: running") == 1
    assert "status: completed" in saida


def test_aguardar_com_401_renova_o_token(amb):
    amb.rede.gets = [(401, "expirado"), _status(status="completed", output_text="ok")]

    assert bridge._aguardar_workflow("w1") == ("ok", None)
    assert [c[3] for c in amb.rede.envios()] == [bearer(1), bearer(2)]


def test_aguardar_tolera_queda_e_erro_transitorio(amb):
    amb.rede.gets = [
        RuntimeError("queda de conexão"),
        (503, "indisponível"),
        _status(foo="sem status ainda"),
        _status(status="done", message="Resposta lida pelo extrator"),
    ]

    assert bridge._aguardar_workflow("w1") == ("Resposta lida pelo extrator", None)
    assert amb.relogio.pausas == [3, 3, 3]


@pytest.mark.parametrize("corpo, detalhe", [
    ({"status": "WF_COMPLETED_ERROR", "error": "modelo indisponível"}, "modelo indisponível"),
    ({"status": "failed"}, '{"status": "failed"}'),
])
def test_aguardar_workflow_que_termina_com_erro(amb, corpo, detalhe):
    amb.rede.gets = [(200, json.dumps(corpo))]

    with pytest.raises(RuntimeError) as erro:
        bridge._aguardar_workflow("w1")
    assert str(erro.value).startswith("O workflow terminou com erro (")
    assert str(erro.value).endswith(detalhe)


def test_aguardar_concluido_sem_texto_util(amb):
    amb.rede.gets = [_status(status="completed", data={"output_text": "Elapsed time: 4.2s"})]

    with pytest.raises(RuntimeError, match=r"O workflow w1 concluiu \(completed\), mas não "
                                           r"encontrei o texto"):
        bridge._aguardar_workflow("w1")


def test_aguardar_com_erro_http_definitivo(amb):
    amb.rede.gets = [(404, "execução inexistente")]

    with pytest.raises(RuntimeError, match=r"status do workflow \(HTTP 404\): execução inexistente"):
        bridge._aguardar_workflow("w1")
    assert amb.relogio.pausas == []


def test_aguardar_estoura_o_tempo_em_andamento(amb):
    amb.rede.gets = [_status(status="processing")] * 50

    with pytest.raises(RuntimeError) as erro:
        bridge._aguardar_workflow("w1")

    assert str(erro.value) == ("Tempo esgotado (30s) aguardando o workflow w1. "
                               "Último status: 'processing'.")
    # desiste na 1ª consulta depois de PASSAR do limite (30s), sem esperar além de 1 intervalo
    assert 30 < sum(amb.relogio.pausas) <= 30 + 3


def test_aguardar_estoura_o_tempo_com_consultas_falhando(amb):
    amb.rede.gets = [(503, "indisponível")] * 50

    with pytest.raises(RuntimeError) as erro:
        bridge._aguardar_workflow("w1")

    consultas = len(amb.rede.envios())
    assert str(erro.value) == (
        "Tempo esgotado (30s) aguardando o workflow w1. Último status: 'desconhecido'. "
        "As últimas {} consultas falharam (HTTP 503).".format(consultas))


# ---- Textgenerator assíncrono -----------------------------------------------

ACK = (200, '{"workflow_execution_id": "w1"}')


def test_textgen_payload_com_a_configuracao_do_auditor(amb):
    amb.rede.posts = [ACK]

    assert bridge._iniciar_textgen([{"role": "user", "content": "plano"}]) == "w1"

    _, url, payload, _ = amb.rede.envios()[0]
    assert url == bridge.URL_TEXTGEN
    assert payload == {
        "async_mode": True,
        "stream": False,
        "model": "az001;modelo-teste",
        "openai_api_version": "2025-01-01-preview",
        "messages": [{"role": "system", "content": "REGRAS DE TESTE"},
                     {"role": "user", "content": "plano"}],
        "max_tokens": 16000,
        "reasoning_effort": "high",
    }


def test_textgen_payload_com_parametros_proprios(amb):
    amb.mp.setattr(bridge, "TEMPERATURA", 0.2)
    amb.rede.posts = [ACK]

    bridge._iniciar_textgen([{"role": "user", "content": "x"}], system_prompt="",
                            modelo="az001;outro", max_tokens=10, reasoning_effort="")

    payload = amb.rede.envios()[0][2]
    assert payload["messages"] == [{"role": "user", "content": "x"}]
    assert payload["model"] == "az001;outro"
    assert payload["max_tokens"] == 10
    assert payload["temperature"] == 0.2
    assert "reasoning_effort" not in payload


def test_textgen_sem_system_prompt_configurado(amb):
    amb.mp.setattr(bridge, "ENVIAR_SYSTEM_PROMPT", False)
    amb.rede.posts = [ACK]

    bridge._iniciar_textgen([{"role": "user", "content": "x"}])

    assert amb.rede.envios()[0][2]["messages"] == [{"role": "user", "content": "x"}]


def test_textgen_falha_no_disparo(amb):
    amb.rede.posts = [(500, "erro interno")] * 3

    with pytest.raises(RuntimeError, match=r"Falha ao iniciar a geração assíncrona \(HTTP 500 "
                                           r"em 3 tentativa\(s\)\): erro interno"):
        bridge._iniciar_textgen([{"role": "user", "content": "x"}])


def test_textgen_sem_execution_id(amb):
    # "id" sozinho não serve como execution_id do textgenerator
    amb.rede.posts = [(200, '{"id": "123"}')]

    with pytest.raises(RuntimeError, match="Geração iniciada, mas não encontrei"):
        bridge._iniciar_textgen([{"role": "user", "content": "x"}])


# ---- Auditoria assíncrona (enviar_para_agente com MODO_ASSINCRONO) ----------

def test_auditoria_async_usa_e_atualiza_o_historico_do_servidor(amb):
    historico.anexar_historico("s1", "user", "plano antigo")
    historico.anexar_historico("s1", "assistant", "auditoria antiga")
    amb.rede.posts = [ACK]
    amb.rede.gets = [_status(status="completed", output_text="Laudo final")]

    resposta, uso = bridge.enviar_para_agente("e agora?", "s1")

    assert resposta == "Laudo final [filtrado]"
    mensagens = amb.rede.envios(bridge.URL_TEXTGEN)[0][2]["messages"]
    assert mensagens == [
        {"role": "system", "content": "REGRAS DE TESTE"},
        {"role": "user", "content": "plano antigo"},
        {"role": "assistant", "content": "auditoria antiga"},
        {"role": "user", "content": "e agora?"},
    ]
    chars = len("REGRAS DE TESTE") + len("plano antigo") + len("auditoria antiga") + len("e agora?")
    assert uso == bridge._uso_estimado(chars, "Laudo final")
    assert historico.historico_sessao("s1")[-2:] == [
        {"role": "user", "content": "e agora?"},
        {"role": "assistant", "content": "Laudo final [filtrado]"},
    ]


def test_auditoria_async_com_historico_do_cliente_nao_mexe_no_servidor(amb):
    amb.rede.posts = [ACK]
    amb.rede.gets = [_status(status="completed", output_text="Laudo",
                             usage={"total_tokens": 42})]

    resposta, uso = bridge.enviar_para_agente(
        "pergunta", "s1", historico=[{"role": "user", "content": "do navegador"}])

    assert resposta == "Laudo [filtrado]"
    assert uso == {"entrada": 0, "saida": 0, "total": 42, "fonte": "api"}
    mensagens = amb.rede.envios(bridge.URL_TEXTGEN)[0][2]["messages"]
    assert mensagens[1:] == [{"role": "user", "content": "do navegador"},
                             {"role": "user", "content": "pergunta"}]
    assert historico.historico_sessao("s1") == []


def test_auditoria_async_via_workflow(amb):
    amb.mp.setattr(bridge, "ASYNC_VIA_TEXTGEN", False)
    amb.mp.setattr(bridge, "ENVIAR_SYSTEM_PROMPT", False)
    amb.rede.posts = [(200, '{"execution_id": "e1"}')]
    amb.rede.gets = [_status(status="completed", output_text="Laudo wf")]

    resposta, uso = bridge.enviar_para_agente("plano", "s2")

    assert resposta == "Laudo wf [filtrado]"
    assert len(amb.rede.envios(bridge.URL_WORKFLOW_START)) == 1
    assert amb.rede.envios(bridge.URL_TEXTGEN) == []
    assert amb.rede.envios()[-1][1] == bridge.URL_WORKFLOW_STATUS + "e1"
    assert uso == bridge._uso_estimado(len("plano"), "Laudo wf")


# ---- gerar_texto (Tag Planner) ----------------------------------------------

def test_gerar_texto_estima_uso_com_imagens_e_nao_filtra(amb):
    amb.rede.posts = [ACK]
    amb.rede.gets = [_status(status="completed", output_text="Plano sugerido")]
    mensagens = [{"role": "user", "content": [
        {"type": "text", "text": "descreva"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]}]

    texto, uso = bridge.gerar_texto(mensagens, "Você é o planner", rotulo="planner",
                                    tokens_extra_estimados=100)

    assert texto == "Plano sugerido"
    esperado = bridge._uso_estimado(len("Você é o planner") + len("descreva"), "Plano sugerido")
    esperado["entrada"] += 100
    esperado["total"] += 100
    assert uso == esperado
    payload = amb.rede.envios(bridge.URL_TEXTGEN)[0][2]
    assert payload["messages"][0] == {"role": "system", "content": "Você é o planner"}


def test_gerar_texto_com_uso_real_e_sem_system_prompt(amb):
    amb.rede.posts = [ACK]
    amb.rede.gets = [_status(status="completed", output_text="ok",
                             usage={"input_tokens": 5, "output_tokens": 1})]

    texto, uso = bridge.gerar_texto([{"role": "user", "content": "x"}], None,
                                    tokens_extra_estimados=999)

    assert texto == "ok"
    assert uso == {"entrada": 5, "saida": 1, "total": 6, "fonte": "api"}
    # sem system prompt próprio: NÃO usa as regras do auditor
    assert amb.rede.envios(bridge.URL_TEXTGEN)[0][2]["messages"] == [
        {"role": "user", "content": "x"}]


# ---- Modo síncrono legado (agente com retries) ------------------------------

def test_sincrono_responde_de_primeira(sincrono):
    sincrono.rede.posts = [(200, '{"text": "Laudo síncrono", "usage": {"total_tokens": 7}}')]

    resposta, uso = bridge.enviar_para_agente("plano", "s1")

    assert resposta == "Laudo síncrono"
    assert uso == {"entrada": 0, "saida": 0, "total": 7, "fonte": "api"}
    _, url, payload, headers = sincrono.rede.envios()[0]
    assert url == bridge.URL_AGENTE
    assert payload == {"messages": [{"type": "text", "content": {"text": "plano"}}],
                       "session_id": "s1", "metadata": {}}
    assert headers == bearer(1)


def test_sincrono_texto_puro_com_uso_estimado(sincrono):
    sincrono.rede.posts = [(200, "Laudo em texto puro")]

    resposta, uso = bridge.enviar_para_agente("plano", "s1")

    assert resposta == "Laudo em texto puro"
    assert uso == bridge._uso_estimado(len("plano"), "Laudo em texto puro")


def test_sincrono_403_renova_o_token(sincrono, capsys):
    sincrono.rede.posts = [(403, "proibido"), (200, '{"text": "ok"}')]

    assert bridge.enviar_para_agente("plano", "s1")[0] == "ok"
    assert [c[3] for c in sincrono.rede.envios()] == [bearer(1), bearer(2)]
    assert "403 recebido - renovando token" in capsys.readouterr().out


def test_sincrono_com_stream_concluido(sincrono):
    sincrono.mp.setattr(bridge, "MODO_STREAM", True)
    sincrono.mp.setattr(bridge, "ASYNC_NO_PAYLOAD", True)
    sincrono.rede.posts = [(200, 'data: {"status": "processing"}\n'
                                 'data: {"status": "completed", "data": {"output_text": "Laudo do stream"}}')]

    resposta, uso = bridge.enviar_para_agente("plano", "s1")

    assert resposta == "Laudo do stream"
    assert uso == bridge._uso_estimado(len("plano"), "Laudo do stream")
    _, _, payload, headers = sincrono.rede.envios()[0]
    assert payload["stream"] is True
    assert payload["async"] is True
    assert headers["Accept"] == "text/event-stream, application/json"


def test_sincrono_stream_com_erro_e_retentado(sincrono, capsys):
    sincrono.rede.posts = [(200, '{"status": "failed", "data": {"error": "timeout interno"}}'),
                           (200, '{"text": "recuperado"}')]

    assert bridge.enviar_para_agente("plano", "s1")[0] == "recuperado"
    assert sincrono.relogio.pausas == [2]
    saida = capsys.readouterr().out
    assert 'Stream retornou erro ("timeout interno")' in saida
    assert "Token renovado antes da tentativa 2" in saida


def test_sincrono_stream_com_erro_em_todas_as_tentativas(sincrono):
    sincrono.rede.posts = [(200, '{"status": "failed", "data": {"error": "timeout interno"}}')] * 3

    with pytest.raises(RuntimeError, match=r'falhou mesmo após 3 tentativas \("timeout interno"\)'):
        bridge.enviar_para_agente("plano", "s1")
    assert sincrono.relogio.pausas == [2, 2]


def test_sincrono_erro_interno_textual_e_retentado(sincrono):
    sincrono.rede.posts = [(200, '{"message": "Erro interno: tente mais tarde"}'),
                           (200, '{"text": "Laudo ok"}')]

    assert bridge.enviar_para_agente("plano", "s1")[0] == "Laudo ok"
    assert sincrono.relogio.pausas == [2]


def test_sincrono_erro_interno_textual_em_todas_as_tentativas(sincrono):
    sincrono.rede.posts = [(200, '{"message": "Service Unavailable"}')] * 3

    with pytest.raises(RuntimeError) as erro:
        bridge.enviar_para_agente("plano", "s1")

    assert "O serviço interno do agente falhou mesmo após 3 tentativas" in str(erro.value)
    assert '("Service Unavailable")' in str(erro.value)
    assert sincrono.relogio.pausas == [2, 2]


def test_sincrono_falha_ao_renovar_token_segue_com_o_atual(sincrono, capsys):
    sincrono.rede.logins = [(200, json.dumps({"token": token_n(1)})), (500, "login fora")]
    sincrono.rede.posts = [(200, '{"message": "erro interno"}'), (200, '{"text": "ok"}')]

    assert bridge.enviar_para_agente("plano", "s1")[0] == "ok"
    assert [c[3] for c in sincrono.rede.envios()] == [bearer(1), bearer(1)]
    assert "Não consegui renovar o token agora" in capsys.readouterr().out


def test_sincrono_recibo_assincrono_vai_para_o_polling(sincrono):
    sincrono.rede.posts = [(200, '{"workflow_execution_id": "w7"}')]
    sincrono.rede.gets = [_status(status="completed", output_text="Via workflow")]

    resposta, uso = bridge.enviar_para_agente("plano", "s1")

    assert resposta == "Via workflow"
    assert uso == bridge._uso_estimado(len("plano"), "Via workflow")
    assert sincrono.rede.envios()[-1][1] == bridge.URL_WORKFLOW_STATUS + "w7"


def test_sincrono_queda_de_conexao_e_retentada(sincrono):
    sincrono.rede.posts = [RuntimeError("queda"), (200, '{"text": "ok"}')]

    assert bridge.enviar_para_agente("plano", "s1")[0] == "ok"
    assert sincrono.relogio.pausas == [2]


def test_sincrono_sem_conexao_em_todas_as_tentativas(sincrono):
    sincrono.rede.posts = [RuntimeError("queda")] * 3

    with pytest.raises(RuntimeError, match="^queda$"):
        bridge.enviar_para_agente("plano", "s1")
    assert len(sincrono.rede.envios()) == 3
    assert sincrono.relogio.pausas == [2, 2]


@pytest.mark.parametrize("stream, async_no_payload", [(True, False), (False, True)])
def test_sincrono_erro_http_com_stream_refaz_em_modo_classico(sincrono, stream, async_no_payload):
    sincrono.mp.setattr(bridge, "MODO_STREAM", stream)
    sincrono.mp.setattr(bridge, "ASYNC_NO_PAYLOAD", async_no_payload)
    sincrono.rede.posts = [(415, "stream não suportado"), (200, '{"text": "ok clássico"}')]

    assert bridge.enviar_para_agente("plano", "s1")[0] == "ok clássico"

    (_, _, payload_1, _), (_, _, payload_2, headers_2) = sincrono.rede.envios()
    assert "stream" in payload_1 or "async" in payload_1
    assert "stream" not in payload_2 and "async" not in payload_2
    assert "Accept" not in headers_2
    # refazer em modo clássico não consome tentativa nem pausa
    assert sincrono.relogio.pausas == []
    assert len(sincrono.rede.logins_feitos()) == 1


def test_sincrono_http_transitorio_e_retentado(sincrono):
    sincrono.rede.posts = [(502, "bad gateway"), (200, '{"text": "ok"}')]

    assert bridge.enviar_para_agente("plano", "s1")[0] == "ok"
    assert sincrono.relogio.pausas == [2]


def test_sincrono_http_transitorio_em_todas_as_tentativas(sincrono):
    sincrono.rede.posts = [(503, "")] * 3

    with pytest.raises(RuntimeError, match=r"O serviço do agente falhou \(HTTP 503\) mesmo após "
                                           r"3 tentativas\. Detalhe: \(sem corpo\)"):
        bridge.enviar_para_agente("plano", "s1")
    assert sincrono.relogio.pausas == [2, 2]


def test_sincrono_http_definitivo_nao_retenta(sincrono):
    sincrono.rede.posts = [(400, "requisição inválida")]

    with pytest.raises(RuntimeError, match="O agente retornou erro HTTP 400: requisição inválida"):
        bridge.enviar_para_agente("plano", "s1")
    assert len(sincrono.rede.envios()) == 1


def test_sincrono_sem_nenhuma_tentativa_configurada(sincrono):
    sincrono.mp.setattr(bridge, "TENTATIVAS_GATEWAY", 0)

    with pytest.raises(RuntimeError, match="Não consegui conectar ao agente após 0 tentativas"):
        bridge.enviar_para_agente("plano", "s1")
    assert sincrono.rede.chamadas == []


# ---- Aquecimento ------------------------------------------------------------

def test_aquecer_agente_manda_oi_numa_sessao_descartavel(amb, capsys):
    enviados = []
    amb.mp.setattr(bridge, "enviar_para_agente", lambda *args: enviados.append(args))

    bridge.aquecer_agente()

    assert enviados == [("oi", "999001000000")]
    assert "Agente acordado" in capsys.readouterr().out


def test_aquecer_agente_nao_derruba_o_servidor_se_falhar(amb, capsys):
    def falha(*args):
        raise RuntimeError("agente lento")

    amb.mp.setattr(bridge, "enviar_para_agente", falha)

    bridge.aquecer_agente()

    assert "Agente ainda lento (agente lento)" in capsys.readouterr().out
