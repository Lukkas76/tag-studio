"""console.escrever() nunca derruba a requisição, mesmo com console ruim."""
import sys

from tagauditor import console


class _Saida:
    """stdout que só aceita o que cabe na codificação dele (ex.: cp1252)."""

    def __init__(self, encoding):
        self.encoding = encoding
        self.linhas = []

    def write(self, texto):
        texto.encode(self.encoding or "ascii")
        self.linhas.append(texto)


def test_caractere_que_o_console_nao_mostra_vira_interrogacao(monkeypatch):
    saida = _Saida("cp1252")
    monkeypatch.setattr(sys, "stdout", saida)
    console.escrever("[teste] {} {}", "ação", "✅")
    assert saida.linhas == ["[teste] ação ?\n"]


def test_console_sem_codificacao_cai_para_ascii(monkeypatch):
    saida = _Saida(None)
    monkeypatch.setattr(sys, "stdout", saida)
    console.escrever("[teste] ação ✅")
    assert saida.linhas == ["[teste] a??o ?\n"]


def test_falha_ate_na_segunda_tentativa_e_engolida(monkeypatch):
    class _Quebrado:
        encoding = "cp1252"

        def write(self, texto):
            raise UnicodeEncodeError("cp1252", texto, 0, 1, "sempre falha")
    monkeypatch.setattr(sys, "stdout", _Quebrado())
    console.escrever("[teste] {}", "✅")              # não levanta


def test_console_fechado_nao_derruba(monkeypatch):
    class _Fechado:
        def write(self, texto):
            raise OSError("console fechado")
    monkeypatch.setattr(sys, "stdout", _Fechado())
    console.escrever("[teste] {}", 1)                  # não levanta


def test_valor_que_nao_e_texto_vira_texto(capsys):
    console.escrever("[teste] {} {}", None, ["a\nb"])
    assert capsys.readouterr().out == "[teste] None ['a\\nb']\n"
