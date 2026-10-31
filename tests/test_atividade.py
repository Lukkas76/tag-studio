"""Trilha de atividade em JSONL (um arquivo por mês) e backup dos logs.
Tudo em tmp_path: _PASTA é trocada pela pasta temporária do teste."""
import json
import os
from datetime import datetime

import pytest

from tagauditor import atividade


class _Agora(datetime):
    """Relógio fixo em 22/07/2026 14:03:11."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 7, 22, 14, 3, 11)


@pytest.fixture
def pasta(tmp_path, monkeypatch):
    destino = tmp_path / "logs"
    monkeypatch.setattr(atividade, "_PASTA", str(destino))
    monkeypatch.setattr(atividade, "datetime", _Agora)
    return destino


def _gravar(pasta, nome, linhas):
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / nome).write_text("\n".join(linhas) + "\n", encoding="utf-8")


def test_registrar_grava_uma_linha_json_no_arquivo_do_mes(pasta):
    usuario = {"email": "fulano@gmail.com", "nome": "Fulano Ação", "foto": "x"}
    atividade.registrar("auditoria", usuario, "10.2.3.4", status="ok", caracteres=18400)
    atividade.registrar("login_falhou")

    arquivo = pasta / "atividade-2026-07.jsonl"
    linhas = arquivo.read_text(encoding="utf-8").splitlines()
    assert "Fulano Ação" in linhas[0]                  # ensure_ascii=False
    assert json.loads(linhas[0]) == {
        "quando": "2026-07-22T14:03:11", "acao": "auditoria", "email": "fulano@gmail.com",
        "nome": "Fulano Ação", "ip": "10.2.3.4", "status": "ok", "caracteres": 18400,
    }
    assert json.loads(linhas[1]) == {
        "quando": "2026-07-22T14:03:11", "acao": "login_falhou", "email": "", "nome": "", "ip": "",
    }


def test_registrar_com_disco_falhando_so_avisa(tmp_path, monkeypatch, capsys):
    ocupado = tmp_path / "isto-e-um-arquivo"
    ocupado.write_text("x", encoding="utf-8")
    monkeypatch.setattr(atividade, "_PASTA", str(ocupado))
    atividade.registrar("login")                       # não levanta
    assert "[atividade] Falha ao gravar o log" in capsys.readouterr().out


def test_ler_mes_atual_mais_recente_primeiro(pasta):
    for acao in ("login", "auditoria", "logout"):
        atividade.registrar(acao, {"email": "a@x.com"})
    assert [e["acao"] for e in atividade.ler()] == ["logout", "auditoria", "login"]


def test_ler_ignora_linhas_ruins_e_filtra_por_email(pasta):
    _gravar(pasta, "atividade-2026-06.jsonl", [
        json.dumps({"acao": "login", "email": "a@x.com"}),
        "",
        "{json quebrado",
        json.dumps({"acao": "login", "email": "b@x.com"}),
        json.dumps({"acao": "logout", "email": "a@x.com"}),
    ])
    assert [e["acao"] for e in atividade.ler("2026-06")] == ["logout", "login", "login"]
    assert [e["email"] for e in atividade.ler("2026-06", email="a@x.com")] == ["a@x.com"] * 2
    assert atividade.ler("2026-06", email="ninguem@x.com") == []


@pytest.mark.parametrize("limite, quantos", [(2, 2), (0, 1), ("1", 1), (100, 3)])
def test_ler_respeita_o_limite(pasta, limite, quantos):
    _gravar(pasta, "atividade-2026-05.jsonl",
            [json.dumps({"acao": "a{}".format(i)}) for i in range(3)])
    eventos = atividade.ler("2026-05", limite=limite)
    assert len(eventos) == quantos
    assert eventos[0]["acao"] == "a2"


def test_ler_mes_sem_arquivo(pasta):
    assert atividade.ler("1999-01") == []
    assert atividade.ler() == []


def test_listar_meses_do_mais_novo_para_o_mais_antigo(pasta):
    for nome in ("atividade-2026-05.jsonl", "atividade-2026-07.jsonl",
                 "atividade-2025-12.jsonl", "outro-2026-08.jsonl", "atividade-2026-09.txt"):
        _gravar(pasta, nome, ["{}"])
    assert atividade.listar_meses() == ["2026-07", "2026-05", "2025-12"]


def test_sem_pasta_de_logs_nao_ha_meses_nem_arquivos(pasta):
    assert not pasta.exists()
    assert atividade.listar_meses() == []
    assert atividade._arquivos_de_log() == []


def test_backup_copia_so_os_arquivos_de_trilha(pasta, tmp_path):
    _gravar(pasta, "atividade-2026-06.jsonl", ['{"acao": "a"}'])
    _gravar(pasta, "atividade-2026-07.jsonl", ['{"acao": "b"}'])
    _gravar(pasta, "anotacoes.txt", ["nada"])
    destino = tmp_path / "backup" / "ta-logs"

    assert atividade.backup(str(destino)) == 2
    assert sorted(os.listdir(destino)) == ["atividade-2026-06.jsonl", "atividade-2026-07.jsonl"]
    assert (destino / "atividade-2026-07.jsonl").read_text(encoding="utf-8") == '{"acao": "b"}\n'


def test_backup_desligado(pasta):
    assert atividade.backup("") == 0
    assert atividade.backup(None) == 0


def test_backup_sem_logs_cria_o_destino_e_copia_nada(pasta, tmp_path):
    destino = tmp_path / "vazio"
    assert atividade.backup(str(destino)) == 0
    assert destino.is_dir()


def test_backup_com_falha_so_avisa(pasta, tmp_path, capsys):
    _gravar(pasta, "atividade-2026-07.jsonl", ["{}"])
    ocupado = tmp_path / "destino-e-arquivo"
    ocupado.write_text("x", encoding="utf-8")
    assert atividade.backup(str(ocupado)) == 0
    assert "[backup] Falha ao copiar os logs para" in capsys.readouterr().out


def test_arquivo_do_mes_usa_a_pasta_configurada(pasta):
    assert atividade._arquivo_do_mes() == os.path.join(str(pasta), "atividade-2026-07.jsonl")
