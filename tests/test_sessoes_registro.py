"""Registro do último login por conta (registro.txt) — gravado em tmp_path."""
from datetime import datetime

import pytest

from tagauditor import sessoes


@pytest.fixture
def registro(tmp_path, monkeypatch):
    caminho = tmp_path / "registro.txt"
    monkeypatch.setattr(sessoes, "_CAMINHO_REGISTRO", str(caminho))
    relogio = {"agora": datetime(2026, 7, 22, 14, 3, 11)}

    class _Agora(datetime):
        @classmethod
        def now(cls, tz=None):
            return relogio["agora"]
    monkeypatch.setattr(sessoes, "datetime", _Agora)
    return caminho, relogio


def test_primeiro_login_cria_o_arquivo(registro):
    caminho, _ = registro
    assert not caminho.exists()
    sessoes.atualizar_registro_login("pessoa@exemplo.com")
    assert caminho.read_text(encoding="utf-8") == "pessoa@exemplo.com | 22/07/2026 14:03:11\n"


def test_novo_login_atualiza_so_a_propria_conta(registro):
    caminho, relogio = registro
    sessoes.atualizar_registro_login("a@x.com")
    sessoes.atualizar_registro_login("b@x.com")
    relogio["agora"] = datetime(2026, 7, 23, 9, 0, 0)
    sessoes.atualizar_registro_login("a@x.com")
    assert caminho.read_text(encoding="utf-8").splitlines() == [
        "a@x.com | 23/07/2026 09:00:00",
        "b@x.com | 22/07/2026 14:03:11",
    ]


def test_linhas_fora_do_formato_sao_descartadas(registro):
    caminho, _ = registro
    caminho.write_text("lixo sem separador\n\nx | y | z\nc@x.com | 01/01/2026 08:00:00\n",
                       encoding="utf-8")
    sessoes.atualizar_registro_login("d@x.com")
    assert caminho.read_text(encoding="utf-8").splitlines() == [
        "c@x.com | 01/01/2026 08:00:00",
        "d@x.com | 22/07/2026 14:03:11",
    ]
