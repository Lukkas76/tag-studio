"""Rotas administrativas (/api/admin/...) e resumo do mês do servidor, com
servidor real em porta efêmera. A trilha de atividade é trocada por stubs
e o backup .zip percorre um BASE_DIR de mentira em tmp_path."""
import html
import http.client
import io
import json
import re
import threading
import zipfile

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


def _json_do_servidor(corpo):
    """O que o front faz (lerJson no admin.js + JSON.parse)."""
    return json.loads(html.unescape(corpo.decode("utf-8")))


ADMIN = {"email": "admin@exemplo.com", "nome": "Admin"}
COMUM = {"email": "comum@exemplo.com", "nome": "Comum"}
CAB_ADMIN = {"Authorization": "Bearer tok-admin"}
CAB_COMUM = {"Authorization": "Bearer tok-comum"}


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch, tmp_path):
    """Logs em tmp_path, um admin e um usuário comum logados."""
    monkeypatch.setattr(atividade, "_PASTA", str(tmp_path / "logs"))
    monkeypatch.setattr(servidor, "EMAILS_ADMIN", ["admin@exemplo.com"])
    monkeypatch.setitem(servidor.SESSOES_ATIVAS, "tok-admin", ADMIN)
    monkeypatch.setitem(servidor.SESSOES_ATIVAS, "tok-comum", COMUM)


@pytest.fixture
def eventos(monkeypatch):
    registrados = []

    def registrar(acao, usuario=None, ip=None, **campos):
        registrados.append(dict(campos, acao=acao, usuario=usuario, ip=ip))

    monkeypatch.setattr(servidor.atividade, "registrar", registrar)
    return registrados


@pytest.fixture
def leituras(monkeypatch):
    """atividade.ler/listar_meses falsos: guarda os argumentos de cada leitura."""
    pedidos = []
    linhas = [
        {"quando": "2026-10-30T10:00:00", "acao": "login", "email": "a@b.com",
         "nome": "A", "ip": "10.0.0.1", "status": "ok", "tokens": 12},
        {"quando": "2026-10-29T09:00:00", "acao": "feedback", "email": "c@d.com",
         "comentario": "+1 <b>bom</b>", "voto": "like"},
    ]

    def ler(**k):
        pedidos.append(k)
        return [dict(ev) for ev in linhas]

    monkeypatch.setattr(servidor.atividade, "ler", ler)
    monkeypatch.setattr(servidor.atividade, "listar_meses", lambda: ["2026-10", "2026-09"])
    return pedidos


ROTAS_ADMIN = ["/api/admin/atividade", "/api/admin/exportar", "/api/admin/backup"]


# ---- Acesso -----------------------------------------------------------------

@pytest.mark.parametrize("rota", ROTAS_ADMIN)
def test_rota_admin_sem_sessao_responde_401(porta, eventos, rota):
    status, _, corpo = _pedir(porta, "GET", rota)
    assert status == 401
    assert "Sessão inválida" in _json_do_servidor(corpo)["erro"]
    assert eventos == []


@pytest.mark.parametrize("rota, rota_log", [
    ("/api/admin/atividade", None),
    ("/api/admin/exportar", "exportar"),
    ("/api/admin/backup", "backup"),
])
def test_rota_admin_de_quem_nao_e_admin_responde_403(porta, eventos, rota, rota_log):
    status, _, corpo = _pedir(porta, "GET", rota, headers=CAB_COMUM)
    assert status == 403
    assert _json_do_servidor(corpo)["erro"] == (
        "Acesso restrito aos administradores do {}.".format(servidor.NOME_PLATAFORMA))
    assert len(eventos) == 1
    assert eventos[0]["acao"] == "admin_negado" and eventos[0]["usuario"] == COMUM
    assert eventos[0].get("rota") == rota_log


# ---- /api/admin/atividade ---------------------------------------------------

def test_atividade_devolve_eventos_e_parametros(porta, eventos, leituras):
    status, cab, corpo = _pedir(porta, "GET", "/api/admin/atividade?mes=2026-09&limite=50",
                                headers=CAB_ADMIN)
    assert status == 200
    dados = _json_do_servidor(corpo)
    assert dados["meses"] == ["2026-10", "2026-09"]
    assert len(dados["eventos"]) == 2
    assert dados["eventos"][1]["comentario"] == "+1 <b>bom</b>"
    assert dados["nome_plataforma"] == servidor.NOME_PLATAFORMA
    for chave in ("baseline_manual_min", "baseline_ferramenta_min",
                  "baseline_planner_manual_min", "baseline_planner_ferramenta_min",
                  "moeda", "cotacao_dolar", "preco_entrada_usd_milhao",
                  "preco_saida_usd_milhao"):
        assert chave in dados
    assert leituras == [{"mes": "2026-09", "limite": 50}]
    assert eventos[0]["acao"] == "admin_logs"
    assert eventos[0]["mes"] == "2026-09" and eventos[0]["eventos"] == 2


@pytest.mark.parametrize("query, mes, limite", [
    ("", None, 2000),
    ("?mes=2026-1&limite=abc", None, 2000),
    ("?mes=..%2F..%2Fsegredos&limite=0", None, 1),
    ("?limite=999999", None, 5000),
    ("?mes=2025-12", "2025-12", 2000),
])
def test_atividade_saneia_mes_e_limite(porta, eventos, leituras, query, mes, limite):
    status, _, _ = _pedir(porta, "GET", "/api/admin/atividade" + query, headers=CAB_ADMIN)
    assert status == 200
    assert leituras == [{"mes": mes, "limite": limite}]
    assert eventos[0]["mes"] == (mes or "atual")


def test_atividade_com_falha_na_leitura_responde_500(porta, eventos, monkeypatch):
    def ler(**k):
        raise OSError("sem acesso")

    monkeypatch.setattr(servidor.atividade, "ler", ler)
    status, _, corpo = _pedir(porta, "GET", "/api/admin/atividade", headers=CAB_ADMIN)
    assert status == 500
    assert _json_do_servidor(corpo) == {"erro": "Falha ao ler os logs: sem acesso"}


# ---- /api/admin/exportar ----------------------------------------------------

def test_exportar_monta_csv_com_colunas_fixas_e_extras(porta, eventos, leituras):
    status, _, corpo = _pedir(porta, "GET", "/api/admin/exportar?mes=2026-10", headers=CAB_ADMIN)
    assert status == 200
    dados = _json_do_servidor(corpo)
    assert dados["arquivo"] == "tag-auditor-logs_2026-10.csv"
    linhas = dados["csv"].splitlines()
    assert linhas[0] == "quando;acao;email;nome;ip;status;comentario;tokens;voto"
    assert linhas[1] == "2026-10-30T10:00:00;login;a@b.com;A;10.0.0.1;ok;;12;"
    assert linhas[2] == "2026-10-29T09:00:00;feedback;c@d.com;;;;'+1 <b>bom</b>;;like"
    assert leituras == [{"mes": "2026-10", "limite": 100000}]
    assert eventos[0]["acao"] == "admin_export"
    assert eventos[0]["mes"] == "2026-10" and eventos[0]["eventos"] == 2


def test_exportar_sem_mes_usa_o_mes_atual(porta, eventos, leituras):
    status, _, corpo = _pedir(porta, "GET", "/api/admin/exportar?mes=lixo", headers=CAB_ADMIN)
    assert status == 200
    nome = _json_do_servidor(corpo)["arquivo"]
    assert re.fullmatch(r"tag-auditor-logs_\d{4}-\d{2}\.csv", nome)
    assert leituras[0]["mes"] is None
    assert eventos[0]["mes"] == "atual"
    status, _, corpo = _pedir(porta, "GET", "/api/admin/exportar", headers=CAB_ADMIN)
    assert status == 200
    assert leituras[1]["mes"] is None


def test_exportar_sem_eventos_tem_so_o_cabecalho(porta, eventos, monkeypatch):
    monkeypatch.setattr(servidor.atividade, "ler", lambda **k: [])
    status, _, corpo = _pedir(porta, "GET", "/api/admin/exportar?mes=2026-01", headers=CAB_ADMIN)
    assert status == 200
    assert _json_do_servidor(corpo)["csv"].splitlines() == ["quando;acao;email;nome;ip;status"]


def test_exportar_com_falha_responde_500(porta, eventos, monkeypatch):
    def ler(**k):
        raise RuntimeError("arquivo corrompido")

    monkeypatch.setattr(servidor.atividade, "ler", ler)
    status, _, corpo = _pedir(porta, "GET", "/api/admin/exportar", headers=CAB_ADMIN)
    assert status == 500
    assert _json_do_servidor(corpo) == {"erro": "Falha ao exportar: arquivo corrompido"}


@pytest.mark.parametrize("valor, esperado", [
    ("=SOMA(A1)", "'=SOMA(A1)"), ("+55 11", "'+55 11"), ("-1", "'-1"),
    ("@cmd", "'@cmd"), ("\tx", "'\tx"), ("\rx", "'\rx"),
    ("texto", "texto"), ("", ""), (-3, -3), (1.5, 1.5), (None, None),
])
def test_celula_csv_neutraliza_formulas(valor, esperado):
    assert servidor._celula_csv(valor) == esperado


# ---- /api/admin/backup ------------------------------------------------------

def _criar_projeto(raiz):
    arquivos = {
        "main.py": "print('oi')",
        "registro.txt": "a@b.com | hoje",
        "~$planilha.xlsx": "lixo do office",
        "velho.pyc": "bytecode",
        "antigo.zip": "zip",
        "static/index.html": "<html></html>",
        "tagauditor/servidor.py": "# codigo",
        "tagauditor/__pycache__/servidor.cpython-312.pyc": "bytecode",
        "logs/atividade-2026-10.jsonl": "{}",
        ".git/config": "[core]",
        ".venv/pyvenv.cfg": "home = x",
        "node_modules/pacote/index.js": "x",
    }
    for relativo, conteudo in arquivos.items():
        caminho = raiz.joinpath(*relativo.split("/"))
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(conteudo, encoding="utf-8")


def test_backup_baixa_o_projeto_sem_logs_nem_temporarios(porta, eventos, monkeypatch, tmp_path):
    projeto = tmp_path / "projeto"
    _criar_projeto(projeto)
    monkeypatch.setattr(servidor, "BASE_DIR", str(projeto))
    status, cab, corpo = _pedir(porta, "GET", "/api/admin/backup", headers=CAB_ADMIN)
    assert status == 200
    assert cab["Content-Type"] == "application/zip"
    assert int(cab["Content-Length"]) == len(corpo)
    disposicao = re.fullmatch(r'attachment; filename="(tag-auditor-projeto_\d{4}-\d{2}-\d{2}\.zip)"',
                              cab["Content-Disposition"])
    assert disposicao
    with zipfile.ZipFile(io.BytesIO(corpo)) as z:
        assert sorted(z.namelist()) == ["main.py", "static/index.html", "tagauditor/servidor.py"]
        assert z.read("main.py") == b"print('oi')"
    ev = eventos[0]
    assert ev["acao"] == "admin_backup" and ev["usuario"] == ADMIN
    assert ev["arquivo"] == disposicao.group(1) and ev["bytes"] == len(corpo)


def test_backup_com_falha_responde_500(porta, monkeypatch, tmp_path):
    projeto = tmp_path / "projeto"
    _criar_projeto(projeto)
    monkeypatch.setattr(servidor, "BASE_DIR", str(projeto))

    def registrar(acao, *a, **k):
        raise OSError("disco cheio")

    monkeypatch.setattr(servidor.atividade, "registrar", registrar)
    status, cab, corpo = _pedir(porta, "GET", "/api/admin/backup", headers=CAB_ADMIN)
    assert status == 500
    assert cab["Content-Type"] == "text/plain; charset=utf-8"
    assert _json_do_servidor(corpo) == {"erro": "Falha ao gerar o zip: disco cheio"}


# ---- Resumo do mês (_resumo_do_mes) -----------------------------------------

EVENTOS_DO_MES = [
    {"acao": "login", "email": "eu@x.com", "quando": "2026-10-30T09:00:00", "status": "ok"},
    {"acao": "auditoria", "email": " Eu@X.com ", "status": "ok", "quando": "2026-10-02T10:00:00"},
    {"acao": "auditoria", "email": "eu@x.com", "status": "erro", "quando": "2026-10-05T10:00:00"},
    {"acao": "planner_analise", "email": "eu@x.com", "status": "ok", "modo": "analise",
     "quando": "2026-10-03T10:00:00"},
    {"acao": "planner_analise", "email": "eu@x.com", "status": "ok", "modo": "pergunta",
     "quando": "2026-10-04T10:00:00"},
    {"acao": "auditoria", "email": "outro@x.com", "status": "ok", "quando": "2026-10-06T10:00:00"},
    {"acao": "planner_analise", "email": "", "status": "ok", "modo": "analise"},
    {"acao": "auditoria", "email": "eu@x.com", "status": "ok"},
    {"acao": "auditoria", "email": None, "status": "ok", "quando": None},
]


def test_resumo_do_mes_conta_usuario_e_plataforma(monkeypatch):
    pedidos = []
    monkeypatch.setattr(servidor.atividade, "ler",
                        lambda **k: pedidos.append(k) or list(EVENTOS_DO_MES))
    resumo = servidor._resumo_do_mes("EU@x.com")
    assert resumo["mes"] == pedidos[0]["mes"]
    assert re.fullmatch(r"\d{4}-\d{2}", resumo["mes"])
    assert pedidos[0]["limite"] == servidor._LIMITE_LEITURA_RESUMO
    assert resumo["usuario"] == {"auditorias": 2, "analises": 1,
                                 "ultima_atividade": "2026-10-05T10:00:00"}
    assert resumo["plataforma"] == {"auditorias": 4, "analises": 2, "usuarios": 2}


def test_resumo_do_mes_sem_email_nao_atribui_nada_ao_usuario(monkeypatch):
    monkeypatch.setattr(servidor.atividade, "ler", lambda **k: list(EVENTOS_DO_MES))
    resumo = servidor._resumo_do_mes(None)
    assert resumo["usuario"] == {"auditorias": 0, "analises": 0, "ultima_atividade": None}
    assert resumo["plataforma"]["auditorias"] == 4


def test_resumo_do_mes_vazio(monkeypatch):
    monkeypatch.setattr(servidor.atividade, "ler", lambda **k: [])
    resumo = servidor._resumo_do_mes("eu@x.com")
    assert re.fullmatch(r"\d{4}-\d{2}", resumo["mes"])
    assert resumo["usuario"] == {"auditorias": 0, "analises": 0, "ultima_atividade": None}
    assert resumo["plataforma"] == {"auditorias": 0, "analises": 0, "usuarios": 0}
