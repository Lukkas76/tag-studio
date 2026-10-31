"""Saída de console do servidor — é o log do processo (a janela preta no
Windows, o stdout do container no AKS).

Toda linha do servidor passa por escrever(): o texto vem de um modelo FIXO do
código ("... {} ...") e o que vem de fora (resposta da Bridge, nome de anexo do
Confluence, rota pedida pelo navegador) entra só como valor, já sem quebra de
linha nem caractere de controle — ninguém consegue forjar uma linha falsa no
log. Dado pessoal (e-mail, nome) não vai para o console: quem fez o quê fica na
trilha de atividade (atividade.py).
"""
import re
import sys

_RE_QUEBRA = re.compile(r"\r\n|\r|\n")
_RE_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")


def _limpar(valor):
    """Número continua número (para {:.1f}, {:d}); o resto vira texto de uma
    linha só (quebras viram " | ", demais controles viram espaço)."""
    if isinstance(valor, (int, float)):
        return valor
    texto = valor if isinstance(valor, str) else str(valor)
    return _RE_CONTROLE.sub(" ", _RE_QUEBRA.sub(" | ", texto))


def escrever(modelo="", *valores):
    """Escreve uma linha no console. Sem valores, `modelo` sai como está.

    Nunca derruba a requisição: com o console/arquivo em cp1252, só o
    caractere que ele não sabe mostrar vira "?" (os acentos continuam)."""
    texto = modelo.format(*[_limpar(v) for v in valores]) if valores else modelo
    try:
        sys.stdout.write(texto + "\n")
    except UnicodeEncodeError:
        codificacao = getattr(sys.stdout, "encoding", None) or "ascii"
        try:
            sys.stdout.write(texto.encode(codificacao, "replace").decode(codificacao) + "\n")
        except Exception:
            pass
    except Exception:
        pass
