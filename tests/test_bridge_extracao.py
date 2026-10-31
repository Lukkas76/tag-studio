"""Bridge: leitura das respostas da IA (JSON/SSE), status e uso de tokens.

Só funções puras (sem rede); o system prompt usa arquivos em tmp_path."""
import json

import pytest

from tagauditor import bridge

TOKEN = "eyJ" + "a" * 40


# ---- Token no corpo do login ------------------------------------------------

@pytest.mark.parametrize("resposta, esperado", [
    ({"access_token": TOKEN}, TOKEN),
    ({"dados": {"usuario": "servico", "token": TOKEN}}, TOKEN),
    ({"lista": [{"nada": 1}, {"jwt": TOKEN}]}, TOKEN),
    ([{"idToken": TOKEN}], TOKEN),
    ({"token": "curto-demais"}, None),
    ({"outra_chave": TOKEN}, None),
    ("texto solto", None),
])
def test_achar_token(resposta, esperado):
    assert bridge._achar_token(resposta) == esperado


# ---- Desescapar / maior string ---------------------------------------------

def test_desescapar_converte_sequencias_escapadas():
    assert bridge._desescapar('a\\r\\nb\\nc\\td\\re \\"f\\"') == 'a\nb\nc\td\ne "f"'


@pytest.mark.parametrize("valor", [None, 42, "texto normal\ncom quebra real"])
def test_desescapar_deixa_o_resto_como_esta(valor):
    assert bridge._desescapar(valor) == valor


@pytest.mark.parametrize("obj, esperado", [
    ("solta", "solta"),
    ({"a": "curto", "b": ["muito mais longo", 3]}, "muito mais longo"),
    ([1, None, {"x": "abc"}], "abc"),
    (42, ""),
])
def test_maior_string(obj, esperado):
    assert bridge._maior_string(obj) == esperado


# ---- extrair_texto_resposta -------------------------------------------------

@pytest.mark.parametrize("corpo", [None, "", "   \n "])
def test_resposta_vazia(corpo):
    assert bridge.extrair_texto_resposta(corpo) == "(o agente respondeu vazio)"


def test_sse_junta_os_textos_sem_heartbeat_telemetria_nem_repeticao():
    corpo = "\n".join([
        "event: message",
        'data: {"status": "processing"}',
        'data: {"text": "Primeira parte"}',
        "data: texto cru",
        "data:",
        'data: {"content": "Primeira parte"}',
        'data: {"message": "Elapsed time: 2.1s"}',
        "data: [DONE]",
    ])
    assert bridge.extrair_texto_resposta(corpo) == "Primeira parte\n\ntexto cru"


def test_sse_detectado_no_meio_do_corpo():
    corpo = ': comentario do servidor\ndata: {"answer": "ok\\nfim"}'
    assert bridge.extrair_texto_resposta(corpo) == "ok\nfim"


def test_sse_sem_linhas_de_dados_devolve_o_corpo():
    assert bridge.extrair_texto_resposta("event: ping") == "event: ping"


def test_sse_so_com_heartbeat_cai_na_leitura_json():
    assert bridge.extrair_texto_resposta('data: {"status": "running"}') == "running"


def test_json_com_texto_em_qualquer_nivel():
    corpo = json.dumps({"choices": [{"message": {"content": "Olá\\n mundo"}}]})
    assert bridge.extrair_texto_resposta(corpo) == "Olá\n mundo"


def test_json_sem_chave_de_texto_usa_a_maior_string():
    corpo = json.dumps({"x": {"y": "texto mais longo aqui"}, "n": 1, "z": "curto"})
    assert bridge.extrair_texto_resposta(corpo) == "texto mais longo aqui"


@pytest.mark.parametrize("dados", [{"n": 1}, {"tempo": "Elapsed time: 1s"}])
def test_json_sem_texto_util_devolve_o_json_formatado(dados):
    assert bridge.extrair_texto_resposta(json.dumps(dados)) == json.dumps(dados, indent=2)


def test_texto_puro_e_desescapado():
    assert bridge.extrair_texto_resposta("Resposta\\ncom quebra") == "Resposta\ncom quebra"


# ---- Erro interno / telemetria ----------------------------------------------

@pytest.mark.parametrize("texto, esperado", [
    (None, False),
    ("", False),
    ("x" * 401 + " erro 500", False),
    ("| erro 500 | linha de tabela |", False),
    ("Internal Server Error", True),
    ("O serviço está temporariamente indisponível", True),
    ("Falha ao executar a auditoria.", True),
    ("Laudo: sem ajustes.", False),
])
def test_resposta_eh_erro_interno(texto, esperado):
    assert bridge._resposta_eh_erro_interno(texto) is esperado


@pytest.mark.parametrize("texto, esperado", [
    ("Elapsed Time: 6.45s", True),
    ("  tempo decorrido 3s", True),
    ("Tempo de processamento: 1s", True),
    ("Laudo final", False),
    ("", False),
    (None, False),
])
def test_eh_telemetria(texto, esperado):
    assert bridge._eh_telemetria(texto) is esperado


# ---- Texto da resposta de status --------------------------------------------

@pytest.mark.parametrize("data, esperado", [
    ("Elapsed time: 1s", ""),
    ("laudo", "laudo"),
    ({"output": "saída"}, "saída"),
    ({"output": "  ", "outro": "fallback"}, "fallback"),
    ({"x": "Elapsed time: 2s"}, ""),
    (None, ""),
    (5, ""),
])
def test_texto_de_data(data, esperado):
    assert bridge._texto_de_data(data) == esperado


@pytest.mark.parametrize("obj, esperado", [
    (None, ""),
    ([{"text": "lista não é resposta de status"}], ""),
    ({"data": "laudo do stream"}, "laudo do stream"),
    ({"data": "  ", "output_collection": {"output_properties": [
        {"output_text": "curto"}, {"outputText": "mais longo"}]}}, "mais longo"),
    ({"resultado": {"text": "texto genérico"}, "actual_value": "valor"}, "texto genérico"),
    ({"props": [{"actual_value": "valor final"}]}, "valor final"),
    ({"text": "Elapsed time: 3s"}, ""),
    ({"status": "completed"}, ""),
])
def test_texto_de_saida(obj, esperado):
    assert bridge._texto_de_saida(obj) == esperado


# ---- Protocolo de status do stream ------------------------------------------

def test_stream_sem_estado_final():
    corpo = 'data: {"status": "processing"}\n\ndata: [DONE]\nlixo\ndata: [1, 2]\n'
    assert bridge._resultado_stream(corpo) == ("NONE", None)
    assert bridge._resultado_stream(None) == ("NONE", None)


def test_stream_concluido_junta_partes_sem_repetir():
    corpo = "\n".join([
        'data: {"status": "PROCESSING"}',
        'data: {"status": "COMPLETED", "data": {"output_text": "parte 1"}}',
        'data: {"status": "completed", "data": "parte 1"}',
        '{"status": "success", "data": {"text": "parte 2"}}',
    ])
    assert bridge._resultado_stream(corpo) == ("COMPLETED", "parte 1\n\nparte 2")


def test_stream_concluido_sem_texto():
    assert bridge._resultado_stream('{"status": "completed"}') == ("COMPLETED", "")


@pytest.mark.parametrize("corpo, detalhe", [
    ('data: {"status": "failed", "data": {"error": "cota excedida"}}', "cota excedida"),
    ('{"status": "WF_COMPLETED_ERROR", "message": "falhou feio"}', "falhou feio"),
    ('{"status": "cancelled", "data": "x"}', "cancelled"),
])
def test_stream_com_erro(corpo, detalhe):
    assert bridge._resultado_stream(corpo) == ("ERRO", detalhe)


@pytest.mark.parametrize("status, classe", [
    (None, "DESCONHECIDO"),
    ("", "DESCONHECIDO"),
    ("FAILED", "ERRO"),
    ("WF_COMPLETED_ERROR", "ERRO"),
    ("Timed_Out", "ERRO"),
    ("COMPLETED", "OK"),
    ("WF_COMPLETED", "OK"),
    ("Concluído com sucesso", "OK"),
    ("running", "ANDAMENTO"),
    ("WF_PROCESSANDO", "ANDAMENTO"),
    ("algo_estranho", "DESCONHECIDO"),
])
def test_classificar_estado(status, classe):
    assert bridge._classificar_estado(status) == classe


# ---- JSON / campos ----------------------------------------------------------

@pytest.mark.parametrize("corpo, esperado", [
    ('{"a": 1}', {"a": 1}),
    ("não é json", None),
    (None, None),
])
def test_carregar_json(corpo, esperado):
    assert bridge._carregar_json(corpo) == esperado


@pytest.mark.parametrize("obj, esperado", [
    ({"Execution_ID": 123}, "123"),
    ({"execution_id": "  ", "dados": {"id": "abc"}}, "abc"),
    ([{"x": 1}, {"workflow_execution_id": "w1"}], "w1"),
    ({"x": [1, 2]}, None),
    ("texto", None),
])
def test_achar_campo(obj, esperado):
    assert bridge._achar_campo(obj, bridge._CAMPOS_ID) == esperado


# ---- Uso de tokens ----------------------------------------------------------

@pytest.mark.parametrize("valor, esperado", [
    (True, None),
    (7, 7),
    (7.9, 7),
    (-1, None),
    (" 42 ", 42),
    ("4a", None),
    (None, None),
])
def test_como_inteiro(valor, esperado):
    assert bridge._como_inteiro(valor) == esperado


@pytest.mark.parametrize("obj, esperado", [
    ({"usage": {"prompt_tokens": 100, "completion_tokens": 50}},
     {"entrada": 100, "saida": 50, "total": 150, "fonte": "api"}),
    ({"resultado": [{"Input-Tokens": "10", "tempo": 3},
                    {"total_tokens": 99, "output_tokens": 5}]},
     {"entrada": 10, "saida": 5, "total": 99, "fonte": "api"}),
    ({"prompt_tokens": 1, "x": {"prompt_tokens": 2}},
     {"entrada": 1, "saida": 0, "total": 1, "fonte": "api"}),
    ({"saida": {"completion_tokens": 8}},
     {"entrada": 0, "saida": 8, "total": 8, "fonte": "api"}),
])
def test_achar_uso(obj, esperado):
    assert bridge._achar_uso(obj) == esperado


@pytest.mark.parametrize("obj", [{"texto": "sem uso", "n": None}, None, []])
def test_achar_uso_sem_contagem(obj):
    assert bridge._achar_uso(obj) is None


def test_uso_estimado_por_caracteres():
    assert bridge._uso_estimado(400, "x" * 40) == {
        "entrada": 100, "saida": 10, "total": 110, "fonte": "estimativa"}
    assert bridge._uso_estimado(-5, None) == {
        "entrada": 0, "saida": 0, "total": 0, "fonte": "estimativa"}


# ---- Recibo do modo assíncrono ----------------------------------------------

@pytest.mark.parametrize("obj, esperado", [
    ({"workflow_execution_id": "w1"}, "w1"),
    ({"execution_id": "w1", "message": "Elapsed time: 1s"}, "w1"),
    ({"execution_id": "w1", "output_text": "laudo pronto"}, None),
    ({"text": "laudo"}, None),
    (None, None),
])
def test_parece_ack_async(obj, esperado):
    assert bridge._parece_ack_async(obj) == esperado


@pytest.mark.parametrize("conteudo, esperado", [
    ("abc", 3),
    ([{"type": "text", "text": "abcd"},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
      {"type": "text", "text": None},
      "parte solta"], 4),
    (None, 0),
])
def test_chars_de_texto(conteudo, esperado):
    assert bridge._chars_de_texto(conteudo) == esperado


# ---- System prompt (regras + documentação) ----------------------------------

@pytest.fixture
def prompt_em_tmp(monkeypatch, tmp_path):
    """System prompt lido de arquivos em tmp_path, com o cache zerado."""
    monkeypatch.setattr(bridge, "SYSTEM_PROMPT_REGRAS", "")
    monkeypatch.setattr(bridge, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(bridge, "ARQUIVO_SYSTEM_PROMPT", "regras.txt")
    monkeypatch.setattr(bridge, "ARQUIVO_DOCUMENTACAO", "doc.md")
    monkeypatch.setitem(bridge._cache_system_prompt, "texto", None)
    return tmp_path


def test_regras_fixas_na_config_tem_prioridade(monkeypatch):
    monkeypatch.setattr(bridge, "SYSTEM_PROMPT_REGRAS", "  regras fixas ")
    assert bridge._obter_system_prompt() == "  regras fixas "


def test_regras_mais_documentacao_completa_e_cache(prompt_em_tmp, capsys):
    (prompt_em_tmp / "regras.txt").write_text(" Regras do auditor \n", encoding="utf-8")
    (prompt_em_tmp / "doc.md").write_text("Documentação X\n", encoding="utf-8")

    esperado = ("Regras do auditor\n\n---\n\n"
                "# DOCUMENTACAO OFICIAL (fonte unica de verdade)\n\nDocumentação X")
    assert bridge._obter_system_prompt() == esperado
    saida = capsys.readouterr().out
    assert "Regras do auditor carregadas de regras.txt" in saida
    assert "Documentação completa carregada de doc.md" in saida

    # 2ª chamada vem do cache: não relê os arquivos
    (prompt_em_tmp / "regras.txt").unlink()
    (prompt_em_tmp / "doc.md").unlink()
    assert bridge._obter_system_prompt() == esperado


def test_regras_que_ja_trazem_a_doc_nao_duplicam(prompt_em_tmp):
    regras = "Regras\n# DOCUMENTACAO OFICIAL\nconteúdo"
    (prompt_em_tmp / "regras.txt").write_text(regras, encoding="utf-8")
    (prompt_em_tmp / "doc.md").write_text("Documentação X", encoding="utf-8")

    assert bridge._obter_system_prompt() == regras


def test_sem_arquivo_de_documentacao_configurado(prompt_em_tmp, monkeypatch):
    monkeypatch.setattr(bridge, "ARQUIVO_DOCUMENTACAO", "")
    (prompt_em_tmp / "regras.txt").write_text("Só regras", encoding="utf-8")

    assert bridge._obter_system_prompt() == "Só regras"


def test_documentacao_ilegivel_segue_so_com_as_regras(prompt_em_tmp, capsys):
    (prompt_em_tmp / "regras.txt").write_text("Só regras", encoding="utf-8")

    assert bridge._obter_system_prompt() == "Só regras"
    assert "Não consegui ler doc.md" in capsys.readouterr().out


def test_regras_ilegiveis_segue_so_com_a_documentacao(prompt_em_tmp, capsys):
    (prompt_em_tmp / "doc.md").write_text("Documentação X", encoding="utf-8")

    assert bridge._obter_system_prompt() == (
        "---\n\n# DOCUMENTACAO OFICIAL (fonte unica de verdade)\n\nDocumentação X")
    assert "Não consegui ler regras.txt" in capsys.readouterr().out


def test_nenhum_arquivo_legivel_devolve_prompt_vazio(prompt_em_tmp, capsys):
    assert bridge._obter_system_prompt() == ""
    saida = capsys.readouterr().out
    assert "Não consegui ler regras.txt" in saida
    assert "Não consegui ler doc.md" in saida
