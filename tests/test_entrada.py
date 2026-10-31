"""tag_auditor_chat.py: rodar o arquivo sobe o servidor; importar, não."""
import importlib
import os
import runpy
import sys

from tagauditor import servidor

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ARQUIVO = os.path.join(_RAIZ, "tag_auditor_chat.py")


def test_rodar_o_arquivo_chama_o_main_do_servidor(monkeypatch):
    chamadas = []
    monkeypatch.setattr(servidor, "main", lambda: chamadas.append("main"))
    runpy.run_path(_ARQUIVO, run_name="__main__")
    assert chamadas == ["main"]


def test_importar_o_modulo_nao_sobe_o_servidor(monkeypatch):
    chamadas = []

    def falso():
        chamadas.append("main")
    monkeypatch.setattr(servidor, "main", falso)
    original = sys.modules.pop("tag_auditor_chat", None)
    try:
        modulo = importlib.import_module("tag_auditor_chat")
    finally:                     # não deixa o módulo com o main falso para outros testes
        sys.modules.pop("tag_auditor_chat", None)
        if original is not None:
            sys.modules["tag_auditor_chat"] = original
    assert chamadas == []
    assert modulo.main is falso
