"""Tag Planner: rotas POST /api/planner/analisar (modos analise, pergunta e
esclarecer) e POST /api/planner/figma, com a IA e o Figma simulados e a
trilha de auditoria capturada (nada vai para o disco nem para a rede)."""
import base64
import importlib.util
import json
import sys
import threading
import types

import pytest

import tagauditor
from tagauditor import planner


# ---- Ambiente: prompt em tmp_path, config fixa, log capturado, IA simulada ---

@pytest.fixture(autouse=True)
def ambiente(tmp_path, monkeypatch):
    regras = tmp_path / "regras.txt"
    regras.write_text("REGRAS DO TIME", encoding="utf-8")
    monkeypatch.setattr(planner, "PLANNER_ARQUIVO_PROMPT", str(regras))
    monkeypatch.setattr(planner, "PLANNER_ARQUIVO_DOCUMENTACAO", "")
    monkeypatch.setitem(planner._cache_prompt, "chave", None)
    monkeypatch.setitem(planner._cache_prompt, "texto", None)
    for nome, valor in {
        "PLANNER_MODELO_LLM": "modelo-teste", "PLANNER_REASONING_EFFORT": "high",
        "PLANNER_DETALHE_IMAGEM": "high", "PLANNER_MAX_TELAS": 6, "PLANNER_MAX_PARTES": 10,
        "PLANNER_MAX_MB_IMAGENS": 20, "PLANNER_SEM_IMAGEM_SE_RECUSAR": True,
        "PLANNER_MARCACAO_POR_IMAGEM": False, "PLANNER_MARCACAO_REFAZER": False,
        "PLANNER_MARCACAO_PARALELAS": 2, "PLANNER_ESCLARECER": True,
        "PLANNER_ESCLARECER_MAX_PERGUNTAS": 3, "PLANNER_ESCLARECER_MAX_TOKENS": 6000,
        "PLANNER_ESCLARECER_REASONING_EFFORT": "medium", "FIGMA_MAX_FRAMES": 6,
    }.items():
        monkeypatch.setattr(planner, nome, valor)

    def sem_simulacao(*_a, **_k):
        raise AssertionError("IA chamada sem simulação")
    monkeypatch.setattr(planner.bridge, "gerar_texto", sem_simulacao)
    return regras


@pytest.fixture(autouse=True)
def log(monkeypatch):
    """Linhas da trilha de auditoria: [(acao, usuario, ip, campos)]."""
    registros = []

    def registrar(acao, usuario=None, ip=None, **campos):
        registros.append((acao, usuario, ip, campos))
    monkeypatch.setattr(planner.atividade, "registrar", registrar)
    return registros


class IAFalsa:
    """Substitui bridge.gerar_texto: responde pelo rótulo ("planner",
    "planner-marcacao-2", "planner-esclarecimento"). Resposta = texto,
    (texto, uso), exceção ou lista delas (uma por chamada)."""

    def __init__(self, respostas):
        self.respostas = {k: (list(v) if isinstance(v, list) else v)
                          for k, v in respostas.items()}
        self.chamadas = []
        self._trava = threading.Lock()

    def __call__(self, mensagens, system_prompt, **kw):
        with self._trava:
            self.chamadas.append(dict(kw, mensagens=mensagens, system_prompt=system_prompt))
            resposta = self.respostas[kw["rotulo"]]
            if isinstance(resposta, list):
                resposta = resposta.pop(0)
        if isinstance(resposta, BaseException):
            raise resposta
        if isinstance(resposta, tuple):
            return resposta
        return resposta, {"entrada": 100, "saida": 10, "total": 110, "fonte": "api"}

    def rotulos(self):
        return [c["rotulo"] for c in self.chamadas]


@pytest.fixture
def ia(monkeypatch):
    def instalar(respostas):
        falsa = IAFalsa(respostas)
        monkeypatch.setattr(planner.bridge, "gerar_texto", falsa)
        return falsa
    return instalar


# ---- Pedido ------------------------------------------------------------------

def _png(largura=100, altura=200):
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + largura.to_bytes(4, "big")
            + altura.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00" + b"\x00" * 4)


PNG_B64 = base64.b64encode(_png()).decode("ascii")
INVENTARIO = [{"ref": "F1", "tipo": "TEXT", "texto": "Solicitar", "interativo": True,
               "bbox": {"x": 0.1, "y": 0.8, "largura": 0.8, "altura": 0.05}}]


def _tela(nome="Home", partes=1, inventario=None):
    tela = {"nome": nome, "origem": "upload",
            "partes": [{"base64": PNG_B64, "mime": "image/png"} for _ in range(partes)]}
    if inventario is not None:
        tela.update(origem="figma", figma={"file_key": "abc", "node_id": "1:2",
                                           "inventario": inventario})
    return tela


def _pedido(telas=None, modo="analise", **contexto):
    ctx = {"pergunta": "Quantos contratam o cartão?"}
    ctx.update(contexto)
    return {"session_id": "sessao-1", "modo": modo, "contexto": ctx,
            "telas": [_tela()] if telas is None else telas}


def _el(ident="T1", imagem=1, classe="OBRIGATÓRIO"):
    return {"id": ident, "imagem": imagem, "parte": 1, "texto": "Solicitar",
            "classificacao": classe, "decisao": "TAGUEAR", "legenda": ident + " — TAGUEAR",
            "bbox_px": {"x": 10, "y": 20, "largura": 30, "altura": 40}}


def _resposta(*elementos, **extra):
    corpo = {"elementos": list(elementos)}
    corpo.update(extra)
    return "## KPI\nTaxa de conversão.\n\n```marcacao\n{}\n```".format(
        json.dumps(corpo, ensure_ascii=False))


def _ultimo(log, acao="planner_analise"):
    linhas = [campos for a, _u, _ip, campos in log if a == acao]
    return linhas[-1]


# ---- Modo "analise" -----------------------------------------------------------

def test_analise_com_marcacao(ia, log, capsys):
    falsa = ia({"planner": _resposta(_el())})
    status, corpo = planner.rota_analisar(_pedido(canal="web"), "ana@x", "10.0.0.1")
    assert status == 200
    assert corpo["resposta"] == "## KPI\nTaxa de conversão."
    assert corpo["resposta_modelo"] == _resposta(_el())
    assert corpo["user_content"].endswith(planner.NOTA_IMAGENS_HISTORICO)
    assert "**Canal:** Web" in corpo["user_content"]
    assert [e["id"] for e in corpo["marcacao"]["elementos"]] == ["T1"]
    assert corpo["marcacao_erro"] is None and corpo["aviso"] is None

    chamada = falsa.chamadas[0]
    assert chamada["rotulo"] == "planner"
    assert chamada["system_prompt"].startswith("REGRAS DO TIME")
    assert chamada["modelo"] == "modelo-teste" and chamada["reasoning_effort"] == "high"
    assert chamada["tokens_extra_estimados"] == 34
    assert chamada["mensagens"][0]["content"][2]["image_url"]["detail"] == "high"

    acao, usuario, ip, campos = log[0]
    assert (acao, usuario, ip) == ("planner_analise", "ana@x", "10.0.0.1")
    esperado = {"sessao": "sessao-1", "modo": "analise", "status": "ok", "telas": 1,
                "partes": 1, "origem": "upload", "canal": "Web", "plataforma": "",
                "pergunta": "Quantos contratam o cartão?", "bytes_imagens": len(_png()),
                "obrigatorios": 1, "opcionais": 0, "elementos": 1, "marcacao": "ok",
                "elementos_por_imagem": "1", "elementos_analise": "1",
                "imagens_sem_elementos": 0, "imagens_vazias": 0, "sem_imagem": False,
                "tokens_entrada": 100, "tokens_saida": 10, "tokens_total": 110,
                "tokens_fonte": "api"}
    assert {k: campos[k] for k in esperado} == esperado
    assert campos["caracteres"] > 0
    for ausente in ("erro", "marcacao_chamadas", "duvidas", "telas_sem_imagem"):
        assert ausente not in campos
    assert "Análise concluída" in capsys.readouterr().out


def test_analise_com_duvidas_respondidas_vai_para_o_log(ia, log):
    ia({"planner": _resposta(_el(), _el("T2", classe="OPCIONAL/DIAGNÓSTICO"))})
    esclarecimentos = [{"pergunta": "Qual a conversão?", "resposta": "Contrato"},
                       {"pergunta": "Inclui PJ?", "premissa": "Só PF"}]
    status, corpo = planner.rota_analisar(_pedido(esclarecimentos=esclarecimentos), "u", "ip")
    assert status == 200
    assert "## Esclarecimentos antes da análise" in corpo["user_content"]
    campos = _ultimo(log)
    assert (campos["duvidas"], campos["duvidas_respondidas"]) == (2, 1)
    assert (campos["obrigatorios"], campos["opcionais"], campos["elementos"]) == (1, 1, 2)


def test_analise_com_tela_vazia_e_tela_sem_elementos(ia, log):
    ia({"planner": _resposta(_el(), sem_elementos=[{"imagem": 3, "motivo": "carregando"}])})
    status, corpo = planner.rota_analisar(
        _pedido([_tela("A"), _tela("B"), _tela("C")]), "u", "ip")
    assert status == 200
    assert corpo["marcacao"]["sem_elementos"] == [{"imagem": 3, "motivo": "carregando"}]
    campos = _ultimo(log)
    assert campos["elementos_por_imagem"] == "1,0,0"
    assert (campos["imagens_sem_elementos"], campos["imagens_vazias"]) == (1, 1)


def test_analise_descarta_elemento_de_imagem_que_nao_existe(ia, log, capsys):
    ia({"planner": _resposta(_el(), _el("T5", imagem=5))})
    status, corpo = planner.rota_analisar(_pedido(), "u", "ip")
    assert status == 200
    assert [e["id"] for e in corpo["marcacao"]["elementos"]] == ["T1"]
    assert _ultimo(log)["marcacao_descartados"] == 1
    assert "marcação ok (1 elemento)." in capsys.readouterr().out


def test_analise_com_coordenadas_so_de_imagens_inexistentes(ia, log, capsys):
    ia({"planner": _resposta(_el("T5", imagem=5), _el("T6", imagem=6))})
    status, corpo = planner.rota_analisar(_pedido(), "u", "ip")
    assert status == 200
    assert corpo["marcacao"] is None
    assert corpo["marcacao_erro"].startswith("As coordenadas devolvidas apontam para imagens")
    assert _ultimo(log)["marcacao"] == "invalida"
    assert "marcação inválida." in capsys.readouterr().out


def test_analise_com_bloco_invalido(ia, log):
    ia({"planner": "Texto\n```marcacao\n{quebrado\n```"})
    status, corpo = planner.rota_analisar(_pedido(), "u", "ip")
    assert status == 200
    assert corpo["resposta"] == "Texto"
    assert corpo["marcacao"] is None
    assert "não é um JSON válido" in corpo["marcacao_erro"]
    assert _ultimo(log)["marcacao"] == "invalida"


def test_analise_sem_bloco_de_coordenadas(ia, log):
    ia({"planner": "Só texto, sem coordenadas."})
    status, corpo = planner.rota_analisar(_pedido(), "u", "ip")
    assert status == 200
    assert corpo["marcacao"] is None
    assert corpo["marcacao_erro"] == planner.MSG_SEM_BLOCO_MARCACAO
    campos = _ultimo(log)
    assert campos["marcacao"] == "ausente"
    assert campos["elementos_por_imagem"] == "0"
    assert campos["imagens_vazias"] == 1


def test_analise_refaz_a_marcacao_da_tela_que_ficou_vazia(ia, log, monkeypatch):
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_REFAZER", True)
    falsa = ia({"planner": _resposta(_el()),
                "planner-marcacao-2": "```marcacao\n" + json.dumps(
                    {"elementos": [_el("T2", imagem=1)]}) + "\n```"})
    status, corpo = planner.rota_analisar(_pedido([_tela("A"), _tela("B")]), "u", "ip")
    assert status == 200
    assert [(e["id"], e["imagem"]) for e in corpo["marcacao"]["elementos"]] == [
        ("T1", 1), ("T2", 2)]
    assert falsa.rotulos() == ["planner", "planner-marcacao-2"]
    campos = _ultimo(log)
    assert campos["elementos_analise"] == "1,0"
    assert campos["elementos_por_imagem"] == "1,1"
    assert (campos["marcacao_chamadas"], campos["marcacao_refeitas"]) == (1, 1)
    assert "marcacao_falhas" not in campos
    assert campos["tokens_total"] == 220


def test_analise_sem_bloco_e_nova_tentativa_tambem_sem_bloco(ia, log, monkeypatch):
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_REFAZER", True)
    ia({"planner": "Sem coordenadas.", "planner-marcacao-1": "Também sem."})
    status, corpo = planner.rota_analisar(_pedido(), "u", "ip")
    assert status == 200
    assert corpo["marcacao"] is None
    assert corpo["marcacao_erro"] == planner.MSG_SEM_BLOCO_MARCACAO
    campos = _ultimo(log)
    assert campos["marcacao_falhas"] == 1
    assert campos["tokens_total"] == 220


def test_analise_por_imagem_corrige_a_marcacao_invalida(ia, log, monkeypatch):
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_POR_IMAGEM", True)
    ia({"planner": "Texto\n```marcacao\n{quebrado\n```",
        "planner-marcacao-1": "```marcacao\n" + json.dumps({"elementos": [_el()]}) + "\n```"})
    status, corpo = planner.rota_analisar(_pedido(), "u", "ip")
    assert status == 200
    assert corpo["marcacao_erro"] is None
    assert [e["id"] for e in corpo["marcacao"]["elementos"]] == ["T1"]
    assert _ultimo(log)["marcacao"] == "ok"


# ---- Recusa de imagem e falhas da IA na análise --------------------------------

RECUSA = RuntimeError("HTTP 400: image_url não suportado nesta rota")


def test_recusa_de_imagem_refaz_so_com_as_camadas_do_figma(ia, log, monkeypatch):
    monkeypatch.setattr(planner, "PLANNER_MARCACAO_REFAZER", True)
    falsa = ia({"planner": [RECUSA, _resposta(_el())]})
    status, corpo = planner.rota_analisar(_pedido([_tela(inventario=INVENTARIO)]), "u", "ip")
    assert status == 200
    assert corpo["aviso"] == planner.AVISO_SEM_IMAGEM_USUARIO
    assert corpo["user_content"].endswith("\n\n" + planner.AVISO_SEM_IMAGEM_MODELO)
    segunda = falsa.chamadas[1]
    assert segunda["mensagens"][0]["content"] == corpo["user_content"]
    assert segunda["tokens_extra_estimados"] == 0
    assert falsa.rotulos() == ["planner", "planner"]       # sem 2ª etapa: não viu as telas
    campos = _ultimo(log)
    assert campos["sem_imagem"] is True
    assert campos["caracteres"] == len(corpo["user_content"])


def test_recusa_de_imagem_sem_camadas_do_figma(ia, log):
    ia({"planner": RECUSA})
    status, corpo = planner.rota_analisar(_pedido(), "u", "ip")
    assert status == 502
    assert corpo["erro"].startswith("A rota do modelo de IA não aceitou as imagens. Traga as "
                                    "telas pela URL do Figma")
    assert corpo["erro"].endswith("Detalhe: HTTP 400: image_url não suportado nesta rota")
    campos = _ultimo(log)
    assert campos["status"] == "erro"
    assert campos["erro"] == corpo["erro"][:200]


def test_recusa_de_imagem_com_analise_so_por_camadas_desligada(ia, monkeypatch):
    monkeypatch.setattr(planner, "PLANNER_SEM_IMAGEM_SE_RECUSAR", False)
    ia({"planner": RECUSA})
    status, corpo = planner.rota_analisar(_pedido([_tela(inventario=INVENTARIO)]), "u", "ip")
    assert status == 502
    assert "PLANNER_SEM_IMAGEM_SE_RECUSAR" in corpo["erro"]


@pytest.mark.parametrize("segunda, mensagem", [
    (RuntimeError("HTTP 503"), "A rota do modelo não aceitou as imagens e a nova tentativa só "
                               "com as camadas do Figma também falhou: HTTP 503"),
    ("", "A IA respondeu vazio. Tente de novo em instantes."),
])
def test_recusa_de_imagem_e_nova_tentativa_falha(ia, segunda, mensagem):
    ia({"planner": [RECUSA, segunda]})
    status, corpo = planner.rota_analisar(_pedido([_tela(inventario=INVENTARIO)]), "u", "ip")
    assert (status, corpo) == (502, {"erro": mensagem})


@pytest.mark.parametrize("resposta, mensagem", [
    (RuntimeError("HTTP 500 na Bridge"), "Não consegui concluir a análise com a IA: HTTP 500 na "
                                         "Bridge"),
    ("   ", "A IA respondeu vazio. Tente de novo em instantes."),
])
def test_falha_da_ia_na_analise(ia, log, capsys, resposta, mensagem):
    ia({"planner": resposta})
    status, corpo = planner.rota_analisar(_pedido(), "u", "ip")
    assert (status, corpo) == (502, {"erro": mensagem})
    assert "[planner][erro] " + mensagem in capsys.readouterr().out
    assert _ultimo(log)["status"] == "erro"


# ---- Telas do Figma só com camadas ------------------------------------------------

def test_tela_do_figma_sem_imagem_e_analisada_pelas_camadas(ia, log):
    falsa = ia({"planner": _resposta(_el())})
    status, corpo = planner.rota_analisar(_pedido([_tela(partes=0, inventario=INVENTARIO)]),
                                          "u", "ip")
    assert status == 200
    assert corpo["aviso"].startswith('A tela "Home" veio do Figma sem imagem')
    assert isinstance(falsa.chamadas[0]["mensagens"][0]["content"], str)
    assert corpo["user_content"] == falsa.chamadas[0]["mensagens"][0]["content"]
    campos = _ultimo(log)
    assert (campos["telas_sem_imagem"], campos["partes"], campos["origem"]) == (1, 0, "figma")


def test_aviso_de_varias_telas_so_com_camadas():
    telas = [{"nome": "A"}, {"nome": "B"}, {"nome": "C"}]
    assert planner._aviso_so_camadas(telas[:2]).startswith('As telas "A" e "B" vieram do Figma')
    assert planner._aviso_so_camadas(telas).startswith('As telas "A", "B" e "C" vieram')


def test_tela_so_com_camadas_nao_tem_recusa_de_imagem_a_contornar(ia):
    ia({"planner": RECUSA})
    status, corpo = planner.rota_analisar(_pedido([_tela(partes=0, inventario=INVENTARIO)]),
                                          "u", "ip")
    assert status == 502
    assert corpo["erro"].startswith("Não consegui concluir a análise com a IA:")


def test_origem_mista_no_log(ia, log):
    ia({"planner": _resposta(_el())})
    planner.rota_analisar(_pedido([_tela(), _tela(inventario=INVENTARIO)]), "u", "ip")
    assert _ultimo(log)["origem"] == "misto"


def test_origem_resumida():
    assert planner._origem_resumo([]) == ""
    assert planner._origem_resumo([{"origem": "colada"}]) == "colada"


def test_contagem_por_tela():
    assert planner._contagem_por_tela(None, 0) == ""
    assert planner._contagem_por_tela(None, 2) == "0,0"
    marcacao = {"elementos": [{"imagem": 2}, {"imagem": 2}, {"imagem": 9}]}
    assert planner._contagem_por_tela(marcacao, 2) == "0,2"


# ---- Modo "pergunta" -------------------------------------------------------------

def test_pergunta_de_acompanhamento_sem_marcacao_nova(ia, log, capsys):
    falsa = ia({"planner": "O T2 é opcional porque..."})
    historico = [{"role": "user", "content": "pedido"}, {"role": "assistant", "content": "análise"}]
    status, corpo = planner.rota_analisar(
        {"modo": "PERGUNTA", "texto": "E o T2?", "historico": historico}, "u", "ip")
    assert (status, corpo) == (200, {"resposta": "O T2 é opcional porque...",
                                     "resposta_modelo": "O T2 é opcional porque...",
                                     "user_content": "E o T2?", "marcacao": None,
                                     "marcacao_erro": None, "aviso": None})
    assert falsa.chamadas[0]["mensagens"] == historico + [{"role": "user", "content": "E o T2?"}]
    assert falsa.chamadas[0]["tokens_extra_estimados"] == 0
    campos = _ultimo(log)
    assert (campos["modo"], campos["marcacao"], campos["pergunta"]) == ("pergunta", "ausente",
                                                                         "E o T2?")
    assert campos["caracteres"] == len("E o T2?")
    assert "elementos_por_imagem" not in campos
    assert "Resposta concluída" in capsys.readouterr().out


def test_pergunta_com_marcacao_nova(ia, log, capsys):
    ia({"planner": _resposta(_el(), _el("T2"))})
    status, corpo = planner.rota_analisar({"modo": "pergunta", "texto": "Refaça"}, "u", "ip")
    assert status == 200
    assert [e["id"] for e in corpo["marcacao"]["elementos"]] == ["T1", "T2"]
    assert _ultimo(log)["marcacao"] == "ok"
    assert "(2 elementos)" in capsys.readouterr().out


def test_pergunta_com_marcacao_invalida(ia):
    ia({"planner": "Texto\n```marcacao\n{quebrado\n```"})
    status, corpo = planner.rota_analisar({"modo": "pergunta", "texto": "Refaça"}, "u", "ip")
    assert status == 200
    assert "não é um JSON válido" in corpo["marcacao_erro"]


@pytest.mark.parametrize("resposta, status, mensagem", [
    (RuntimeError("HTTP 504"), 502, "Não consegui concluir a análise com a IA: HTTP 504"),
    ("", 502, "A IA respondeu vazio. Tente de novo em instantes."),
])
def test_pergunta_com_falha_da_ia(ia, resposta, status, mensagem):
    ia({"planner": resposta})
    assert planner.rota_analisar({"modo": "pergunta", "texto": "E aí?"}, "u", "ip") == (
        status, {"erro": mensagem})


def test_pergunta_vazia(log):
    status, corpo = planner.rota_analisar({"modo": "pergunta", "texto": ""}, "u", "ip")
    assert (status, corpo) == (400, {"erro": "Digite a pergunta de acompanhamento."})
    assert _ultimo(log)["status"] == "erro"


# ---- Erros gerais de rota_analisar ------------------------------------------------

def test_pedido_que_nao_e_json_objeto(log):
    status, corpo = planner.rota_analisar(["x"], "u", "ip")
    assert status == 400
    assert corpo == {"erro": "Requisição inválida: envie o pedido de análise em JSON."}
    acao, _u, _ip, campos = log[0]
    assert acao == "planner_analise"
    assert (campos["status"], campos["modo"], campos["sem_imagem"]) == ("erro", "", False)


def test_modo_invalido(log):
    status, corpo = planner.rota_analisar({"modo": "outro"}, "u", "ip")
    assert status == 400
    assert corpo["erro"].startswith('Modo inválido: use "esclarecer"')
    assert _ultimo(log)["modo"] == "outro"


def test_modo_vazio_e_analise(ia, log):
    ia({"planner": _resposta(_el())})
    pedido = _pedido()
    pedido["modo"] = ""
    assert planner.rota_analisar(pedido, "u", "ip")[0] == 200
    assert _ultimo(log)["modo"] == "analise"


def test_erro_de_validacao_registra_o_que_ja_se_sabe(log):
    status, corpo = planner.rota_analisar(_pedido(telas=[], canal="App"), "u", "ip")
    assert status == 400
    assert corpo["erro"].startswith("Envie ao menos uma tela")
    campos = _ultimo(log)
    assert (campos["canal"], campos["pergunta"], campos["telas"]) == (
        "App nativo", "Quantos contratam o cartão?", 0)


def test_sem_arquivo_de_regras_responde_500(ambiente, log, capsys):
    ambiente.unlink()
    status, corpo = planner.rota_analisar(_pedido(), "u", "ip")
    assert status == 500
    assert "não foi encontrado" in corpo["erro"]
    assert "[planner][erro]" in capsys.readouterr().out


def test_falha_inesperada_responde_500(monkeypatch, log, capsys):
    def quebra():
        raise KeyError("chave")
    monkeypatch.setattr(planner, "obter_system_prompt", quebra)
    status, corpo = planner.rota_analisar(_pedido(), "u", "ip")
    assert status == 500
    assert corpo == {"erro": "Erro interno ao processar a análise: 'chave'"}
    assert _ultimo(log)["erro"] == "'chave'"
    assert "Falha inesperada" in capsys.readouterr().out


# ---- Modo "esclarecer" -------------------------------------------------------------

ESCLARECIMENTO = """Antes da análise:

```esclarecimento
{"entendimento": "Jornada de contratação do cartão.",
 "perguntas": [
   {"pergunta": "A conversão é o contrato assinado?", "por_que": "Define o KPI.",
    "opcoes": ["Sim", "Não", "Sim"], "premissa": "Contrato assinado"},
   "Inclui PJ?"
 ]}
```"""


def test_duvidas_antes_da_analise(ia, log, capsys):
    falsa = ia({"planner-esclarecimento": ESCLARECIMENTO})
    status, corpo = planner.rota_analisar(_pedido(modo="esclarecer", canal="web"), "ana", "ip")
    assert status == 200
    assert corpo == {
        "entendimento": "Jornada de contratação do cartão.",
        "perguntas": [
            {"id": "P1", "pergunta": "A conversão é o contrato assinado?",
             "por_que": "Define o KPI.", "opcoes": ["Sim", "Não"],
             "premissa": "Contrato assinado"},
            {"id": "P2", "pergunta": "Inclui PJ?", "por_que": "", "opcoes": [], "premissa": ""},
        ],
        "aviso": None,
    }
    chamada = falsa.chamadas[0]
    assert chamada["max_tokens"] == 6000 and chamada["reasoning_effort"] == "medium"
    assert chamada["tokens_extra_estimados"] == 34
    assert "Faça no máximo 3 perguntas" in chamada["system_prompt"]
    assert planner.INSTRUCOES_FERRAMENTA not in chamada["system_prompt"]
    assert len(log) == 1
    acao, usuario, _ip, campos = log[0]
    assert (acao, usuario) == ("planner_esclarecimento", "ana")
    esperado = {"sessao": "sessao-1", "status": "ok", "telas": 1, "partes": 1, "perguntas": 2,
                "canal": "Web", "formato": "ok", "bytes_imagens": len(_png()),
                "tokens_total": 110, "tokens_fonte": "api"}
    assert {k: campos[k] for k in esperado} == esperado
    assert "Dúvidas prontas" in capsys.readouterr().out


def test_duvidas_em_formato_inesperado(ia, log):
    ia({"planner-esclarecimento": ("Não tenho dúvidas.", None)})
    status, corpo = planner.rota_analisar(_pedido(modo="esclarecer"), "u", "ip")
    assert status == 200
    assert corpo["perguntas"] == [] and corpo["entendimento"] == ""
    assert corpo["aviso"].startswith("A IA não devolveu as dúvidas no formato esperado")
    campos = _ultimo(log, "planner_esclarecimento")
    assert campos["formato"] == "invalido"
    assert "tokens_total" not in campos


def test_etapa_de_duvidas_desligada(monkeypatch, log):
    monkeypatch.setattr(planner, "PLANNER_ESCLARECER", False)
    status, corpo = planner.rota_analisar(_pedido(modo="esclarecer"), "u", "ip")
    assert status == 400
    assert "PLANNER_ESCLARECER" in corpo["erro"]
    assert _ultimo(log, "planner_esclarecimento")["status"] == "erro"


def test_duvidas_com_falha_da_ia(ia, log):
    ia({"planner-esclarecimento": RuntimeError("HTTP 503")})
    status, corpo = planner.rota_analisar(_pedido(modo="esclarecer"), "u", "ip")
    assert (status, corpo) == (502, {"erro": "Não consegui preparar as dúvidas com a IA: "
                                             "HTTP 503"})
    assert _ultimo(log, "planner_esclarecimento")["erro"] == corpo["erro"]


def test_duvidas_sem_arquivo_de_regras(ambiente, ia):
    ambiente.unlink()
    ia({"planner-esclarecimento": ESCLARECIMENTO})
    status, corpo = planner.rota_analisar(_pedido(modo="esclarecer"), "u", "ip")
    assert status == 500
    assert "não foi encontrado" in corpo["erro"]


def test_duvidas_com_pedido_invalido(log):
    status, corpo = planner.rota_analisar({"modo": "esclarecer", "contexto": {}}, "u", "ip")
    assert status == 400
    assert "pergunta de negócio" in corpo["erro"]


def test_duvidas_com_falha_inesperada(monkeypatch, log, capsys):
    def quebra(*_a):
        raise ValueError("quebrou")
    monkeypatch.setattr(planner, "montar_mensagem_analise", quebra)
    status, corpo = planner.rota_analisar(_pedido(modo="esclarecer"), "u", "ip")
    assert (status, corpo) == (500, {"erro": "Erro interno ao preparar as dúvidas: quebrou"})
    assert _ultimo(log, "planner_esclarecimento")["erro"] == "quebrou"
    assert "Falha inesperada nas dúvidas" in capsys.readouterr().out


# ---- Leitura do bloco de dúvidas ---------------------------------------------------

@pytest.mark.parametrize("texto", [None, "", "   ", 42, "Sem JSON nenhum."])
def test_resposta_sem_duvidas_legiveis(texto):
    assert planner.extrair_esclarecimento(texto) is None


def test_duvidas_no_ultimo_json_com_perguntas():
    texto = ('```json\n{"x": 1}\n```\n```json\n{"perguntas": ["A?"]}\n```\n'
             '```json\n{"questions": [{"question": "B?", "options": "não é lista"}],'
             ' "resumo": "Resumo"}\n```')
    assert planner.extrair_esclarecimento(texto) == {
        "entendimento": "Resumo",
        "perguntas": [{"id": "P1", "pergunta": "B?", "por_que": "", "opcoes": [],
                       "premissa": ""}]}


def test_duvidas_no_texto_inteiro_como_json():
    assert planner.extrair_esclarecimento('[{"pergunta": "C?"}]')["perguntas"][0]["pergunta"] == "C?"


def test_bloco_de_duvidas_quebrado_tenta_o_anterior():
    texto = ("```esclarecimento\n{\"perguntas\": [\"A?\"]}\n```\n"
             "```esclarecimento\n{quebrado\n```")
    assert planner.extrair_esclarecimento(texto)["perguntas"][0]["pergunta"] == "A?"


def test_normalizar_duvidas(monkeypatch):
    monkeypatch.setattr(planner, "PLANNER_ESCLARECER_MAX_PERGUNTAS", 2)
    assert planner._normalizar_esclarecimento(5) is None
    assert planner._normalizar_esclarecimento({"perguntas": "x"}) == {
        "entendimento": "", "perguntas": []}
    resultado = planner._normalizar_esclarecimento({
        "Dúvidas": [None, {"pergunta": " "}, {"Texto": "Um?", "Opções": ["a", "b", "c", "d", "e"],
                                               "Why": "x", "Assumption": "y"},
                    "Dois?", "Três?"],
        "Understanding": "Entendi."})
    assert resultado["entendimento"] == "Entendi."
    assert [p["id"] for p in resultado["perguntas"]] == ["P1", "P2"]
    assert resultado["perguntas"][0] == {"id": "P1", "pergunta": "Um?", "por_que": "x",
                                         "opcoes": ["a", "b", "c", "d"], "premissa": "y"}


# ---- Log da análise (_registrar_analise) -------------------------------------------

def test_log_da_analise_so_traz_contadores_da_marcacao_que_aconteceram(log):
    info = planner._info_analise()
    info.update(modo="analise", telas=2, marcacao_chamadas=2, marcacao_falhas=1,
                marcacao_descartados=0, telas_sem_imagem=1, duvidas=0)
    marcacao = {"elementos": [{"imagem": 1, "classificacao": "NAO_TAGUEAR"}],
                "sem_elementos": [{"imagem": 2, "motivo": "vazia"}]}
    planner._registrar_analise("u", "ip", info, "ok", 0, uso={"total": 5}, marcacao=marcacao,
                               estado="ok")
    campos = _ultimo(log)
    assert (campos["obrigatorios"], campos["opcionais"], campos["elementos"]) == (0, 0, 1)
    assert (campos["imagens_sem_elementos"], campos["imagens_vazias"]) == (1, 0)
    assert (campos["marcacao_chamadas"], campos["marcacao_falhas"]) == (2, 1)
    assert "marcacao_descartados" not in campos and "duvidas" not in campos
    assert campos["telas_sem_imagem"] == 1
    assert (campos["tokens_entrada"], campos["tokens_total"], campos["tokens_fonte"]) == (0, 5, "")


def test_log_de_pergunta_sem_estado_de_marcacao(log):
    info = planner._info_analise()
    info["modo"] = "pergunta"
    planner._registrar_analise("u", "ip", info, "ok", 0)
    campos = _ultimo(log)
    assert campos["marcacao"] == "ausente" and campos["elementos"] == 0
    assert "elementos_por_imagem" not in campos and "tokens_total" not in campos


# ---- Rota /api/planner/figma ----------------------------------------------------------

class ErroFigmaFalso(Exception):
    def __init__(self, mensagem, status=400):
        super().__init__(mensagem)
        self.status = status


TOKEN = "figd_TokenDaPessoa123"
URL = "https://www.figma.com/design/AbC123/Arquivo?node-id=1-2"


@pytest.fixture
def figma(monkeypatch):
    """Módulo do Figma simulado: buscar() devolve/levanta o que o teste mandar."""
    falso = types.SimpleNamespace(ErroFigma=ErroFigmaFalso, configurado=lambda: True,
                                  chamadas=[], resultado=None)

    def buscar(url, ids, token):
        falso.chamadas.append((url, ids, token))
        if isinstance(falso.resultado, BaseException):
            raise falso.resultado
        return falso.resultado
    falso.buscar = buscar
    monkeypatch.setattr(planner, "figma", falso)
    return falso


def test_figma_exporta_os_frames_escolhidos(figma, log, capsys):
    figma.resultado = {"arquivo": {"nome": "Cartão", "key": "AbC123"},
                       "frames": [{"id": "1:2", "inventario": [{"ref": "F1"}, {"ref": "F2"}]},
                                  {"id": "3:4"}, "lixo"]}
    status, corpo = planner.rota_figma({"url": URL, "ids": ["1-2", "3:4"],
                                        "token_figma": " " + TOKEN + " "}, "ana", "ip")
    assert (status, corpo) == (200, figma.resultado)
    assert figma.chamadas == [(URL, ["1:2", "3:4"], TOKEN)]
    acao, usuario, _ip, campos = log[0]
    assert (acao, usuario) == ("planner_figma", "ana")
    esperado = {"status": "ok", "url": URL, "arquivo": "Cartão", "file_key": "AbC123",
                "frames": 3, "ids": "1:2,3:4", "elementos": 2, "token_proprio": True}
    assert {k: campos[k] for k in esperado} == esperado
    assert TOKEN not in json.dumps(campos)
    assert '3 frame(s) exportado(s) de "Cartão" (2 camadas)' in capsys.readouterr().out


def test_figma_lista_os_frames_para_escolher(figma, log, capsys):
    figma.resultado = {"arquivo": "sem formato", "opcoes": [{"id": "1:2"}, {"id": "3:4"}]}
    status, corpo = planner.rota_figma({"url": "figma.com/design/AbC123/x"}, "u", "ip")
    assert status == 200
    assert figma.chamadas == [("https://figma.com/design/AbC123/x", None, None)]
    campos = _ultimo(log, "planner_figma")
    assert (campos["status"], campos["frames"], campos["arquivo"], campos["token_proprio"]) == (
        "opcoes", 0, "", False)
    assert '2 frame(s) listado(s) em ""' in capsys.readouterr().out


@pytest.mark.parametrize("resultado", [None, ["x"], {"frames": "x"}])
def test_figma_com_resposta_inesperada(figma, log, capsys, resultado):
    figma.resultado = resultado
    status, corpo = planner.rota_figma({"url": URL}, "u", "ip")
    assert status == 502
    assert corpo["erro"].startswith("Não consegui buscar no Figma agora")
    assert "resposta inesperada do módulo do Figma" in capsys.readouterr().out
    assert _ultimo(log, "planner_figma")["status"] == "erro"


def test_erro_do_figma_mantem_o_status_e_esconde_o_token(figma, log, capsys):
    figma.resultado = ErroFigmaFalso("Token {} sem acesso ao arquivo.".format(TOKEN), 403)
    status, corpo = planner.rota_figma({"url": URL, "token_figma": TOKEN}, "u", "ip")
    assert (status, corpo) == (403, {"erro": "Token *** sem acesso ao arquivo."})
    assert TOKEN not in capsys.readouterr().out
    assert _ultimo(log, "planner_figma")["erro"] == "Token *** sem acesso ao arquivo."


def test_erro_do_figma_sem_mensagem_e_status_estranho(figma):
    figma.resultado = ErroFigmaFalso("", "abc")
    assert planner.rota_figma({"url": URL}, "u", "ip") == (
        502, {"erro": "Falha ao buscar no Figma."})


def test_falha_de_rede_no_figma_nao_vaza_o_token(figma, capsys):
    figma.resultado = OSError("proxy recusou {}".format(TOKEN))
    status, corpo = planner.rota_figma({"url": URL, "token_figma": TOKEN}, "u", "ip")
    assert status == 502
    saida = capsys.readouterr().out
    assert "proxy recusou ***" in saida and TOKEN not in saida


def test_figma_indisponivel_no_servidor(monkeypatch, log):
    monkeypatch.setattr(planner, "figma", None)
    status, corpo = planner.rota_figma({"url": URL}, "u", "ip")
    assert status == 503
    assert "indisponível" in corpo["erro"]


@pytest.mark.parametrize("dados, mensagem", [
    ("texto", "Requisição inválida: envie a URL do Figma em JSON."),
    ({"url": URL, "token_figma": 123}, "Token do Figma inválido."),
    ({"url": URL, "token_figma": "curto"}, "Token do Figma inválido: confira se copiou"),
    ({"url": ""}, "Cole a URL do arquivo ou do frame do Figma."),
    ({"url": URL, "ids": {"a": 1}}, "Lista de frames inválida."),
])
def test_figma_com_pedido_invalido(figma, log, dados, mensagem):
    status, corpo = planner.rota_figma(dados, "u", "ip")
    assert status == 400
    assert corpo["erro"].startswith(mensagem)
    assert figma.chamadas == []
    assert _ultimo(log, "planner_figma")["status"] == "erro"


@pytest.mark.parametrize("valor, esperado", [
    ("https://www.figma.com/design/abc", "https://www.figma.com/design/abc"),
    ("figma.com/design/abc", "https://figma.com/design/abc"),
    ("www.figma.com", "https://www.figma.com"),
    ("<https://embed.figma.com/x>", "https://embed.figma.com/x"),
    ("  'https://figma.com/file/x?node-id=1-2'  ", "https://figma.com/file/x?node-id=1-2"),
])
def test_url_do_figma_aceita(valor, esperado):
    assert planner.validar_url_figma(valor) == esperado


@pytest.mark.parametrize("valor, mensagem", [
    (None, "Cole a URL"),
    ("https://www.figma.com/" + "a" * 4100, "longa demais"),
    ("https://www.figma.com/design/abc\nhttps://x", "numa linha"),
    ("https://www.figma.com/design/abc\tx", "numa linha"),
    ("http://www.figma.com/design/abc", "URL do Figma inválida: use o link"),
    ("ftp://figma.com/x", "URL do Figma inválida: use o link"),
    ("https://figma.com.evil.com/x", "URL do Figma inválida: use o link"),
    ("https://evilfigma.com/x", "URL do Figma inválida: use o link"),
    ("https://[::1/x", "URL do Figma inválida: use o link"),
    ("exemplo.com/figma.com", "URL do Figma inválida: use o link"),
])
def test_url_do_figma_recusada(valor, mensagem):
    with pytest.raises(planner.ErroPlanner, match=mensagem):
        planner.validar_url_figma(valor)


def test_ids_dos_frames(monkeypatch):
    assert planner.validar_ids_figma(None) == []
    assert planner.validar_ids_figma("") == []
    assert planner.validar_ids_figma("12-34, 56:78 12-34") == ["12:34", "56:78"]
    assert planner.validar_ids_figma(["1:2", " ", "1-2"]) == ["1:2"]
    with pytest.raises(planner.ErroPlanner, match="Identificador de frame inválido: abc."):
        planner.validar_ids_figma(["abc"])
    with pytest.raises(planner.ErroPlanner, match=r"inválido: \(vazio\)"):
        planner.validar_ids_figma([None])
    monkeypatch.setattr(planner, "FIGMA_MAX_FRAMES", 2)
    with pytest.raises(planner.ErroPlanner, match=r"no máximo 2 frames por busca \(foram 3\)"):
        planner.validar_ids_figma(["1:1", "1:2", "1:3"])


def test_token_do_figma():
    assert planner.validar_token_figma(None) == ""
    assert planner.validar_token_figma("   ") == ""
    assert planner.validar_token_figma(" " + TOKEN + "\n") == TOKEN
    with pytest.raises(planner.ErroPlanner, match="Token do Figma inválido"):
        planner.validar_token_figma("token com espaço")


def test_token_nunca_aparece_no_texto():
    assert planner._sem_token("erro {}".format(TOKEN), TOKEN) == "erro ***"
    assert planner._sem_token("erro e mais erro", "e") == "erro e mais erro"
    assert planner._sem_token(None, TOKEN) == ""


@pytest.mark.parametrize("valor, esperado", [
    (429, 429), ("503", 503), ("abc", 502), (200, 502), (None, 502),
])
def test_status_http_do_erro_do_figma(valor, esperado):
    assert planner._status_http(valor) == esperado


# ---- Planner sobe mesmo sem o módulo do Figma -----------------------------------------

def test_planner_sobe_sem_o_modulo_do_figma(monkeypatch, log, capsys):
    monkeypatch.delattr(tagauditor, "figma", raising=False)
    monkeypatch.setitem(sys.modules, "tagauditor.figma", None)
    spec = importlib.util.spec_from_file_location("tagauditor._planner_sem_figma",
                                                  planner.__file__)
    copia = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(copia)
    assert copia.figma is None
    assert "Busca no Figma indisponível" in capsys.readouterr().out
    assert copia.figma_configurado() is False
    status, corpo = copia.rota_figma({"url": URL}, "u", "ip")
    assert status == 503
