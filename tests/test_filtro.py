"""Filtro de falsos positivos da resposta da IA (tabelas markdown)."""
import pytest

from tagauditor import filtro


@pytest.fixture(autouse=True)
def filtro_ligado(monkeypatch):
    monkeypatch.setattr(filtro, "FILTRAR_FALSOS_POSITIVOS", True)


def test_normalizar():
    assert filtro.normalizar("  Ação   `código` **forte** ✅ ") == "acao codigo forte"
    assert filtro.normalizar(None) == ""
    assert filtro.normalizar(42) == "42"


# ---- linha a linha ----------------------------------------------------------------

_IDX = {"localizacao": 0, "parametro": 1, "valor": 2, "problema": 3, "correcao": 4}


def _falsa(localizacao="page", parametro="action", valor="x", problema="", correcao=""):
    return filtro._fp_linha_falsa([localizacao, parametro, valor, problema, correcao], _IDX)


@pytest.mark.parametrize("problema", ["", "OK", "  ok ", "Sem ajustes"])
def test_linha_sem_apontamento_fica(problema):
    assert _falsa(problema=problema, correcao="Adicionar algo") is False


@pytest.mark.parametrize("problema, correcao", [
    ("O uso de _ sugere padrão hierárquico", "Trocar"),
    ("Parece fora do padrão", "Trocar"),
    ("Valor fora do padrão", "Verificar se o valor está certo"),
    ("Se for de fato um botão, renomear", "Renomear"),
    ("Prefixo desnecessário", "Remover"),
    ("name e path não estão coerentes entre si", "Alinhar"),
    ("Pode ser um erro de digitação", "Corrigir"),
])
def test_especulacao_e_falso_positivo(problema, correcao):
    assert _falsa(problema=problema, correcao=correcao) is True


@pytest.mark.parametrize("problema", [
    "Parâmetro não foi declarado",
    "Campo label ausente no evento",
    "Ausência do bloco user",
    "Valor não consta no plano",
])
def test_campo_ausente_no_problema_e_falso_positivo(problema):
    assert _falsa(problema=problema, correcao="Corrigir") is True


def test_ausente_so_na_correcao_nao_derruba_a_linha():
    assert _falsa(problema="Acento no valor", correcao="Bloco ausente") is False


def test_placeholder_so_vale_se_estiver_no_parametro_errado():
    assert _falsa(valor="{{user_id}}", problema="Valor fora do padrão") is True
    assert _falsa(valor="${tela}", problema="Usar minúsculas") is True
    assert _falsa(valor="{{user_id}}", problema="Placeholder no parâmetro errado",
                  correcao="Mover") is False


def test_underline_em_location_e_permitido():
    assert _falsa(parametro="location", problema="Usa underline como separador") is True
    assert _falsa(parametro="action", problema="Usa underline como separador") is False


@pytest.mark.parametrize("parametro, problema, esperado", [
    ("path", "Uso de barra no valor", True),
    ("page_title", "Contém slash", True),
    ("url_webview", "path usa / fora do padrão", True),
    ("location", "Nível de diretório extra", True),
    ("path", "Acento em cambio/algo", False),
    ("action", "Uso de barra no valor", False),
])
def test_barra_e_separador_valido_em_caminhos(parametro, problema, esperado):
    assert _falsa(parametro=parametro, problema=problema, correcao="Trocar") is esperado


@pytest.mark.parametrize("valor", ["", "-", "(vazio)", "N/A", "Não informado"])
def test_valor_vazio_com_pedido_para_adicionar(valor):
    assert _falsa(valor=valor, problema="Parâmetro obrigatório", correcao="Adicionar o label") is True


def test_valor_vazio_com_outra_correcao_fica():
    assert _falsa(valor="-", problema="Parâmetro obrigatório", correcao="Revisar com o time") is False


def test_apontamento_legitimo_fica():
    assert _falsa(valor="Clique_Botao", problema="Usar hífen em vez de underline",
                  correcao="clique-botao") is False


def test_linha_mais_curta_que_o_cabecalho():
    assert filtro._fp_linha_falsa(["so uma"], _IDX) is False


# ---- cabeçalho e separador -------------------------------------------------------

def test_mapear_colunas():
    assert filtro._fp_mapear_colunas(
        ["Evento", "Localização", "Parâmetro", "Valor atual", "Problema", "Correção"]
    ) == {"localizacao": 1, "parametro": 2, "valor": 3, "problema": 4, "correcao": 5}
    assert filtro._fp_mapear_colunas(["Chave", "Observação", "Recomendação", "Problema"]) == \
        {"parametro": 0, "problema": 1, "correcao": 2}
    assert filtro._fp_mapear_colunas(["Ajuste"]) == {"correcao": 0}


@pytest.mark.parametrize("linha, esperado", [
    ("|---|---|", True), ("| :--- | ---: |", True), ("---", False),
    ("| a | b |", False), ("", False), (None, False),
])
def test_separador(linha, esperado):
    assert filtro._fp_eh_separador(linha) is esperado


def test_celulas():
    assert filtro._fp_celulas("  | a | b |c|  ") == ["a", "b", "c"]
    assert filtro._fp_celulas("a | b") == ["a", "b"]


# ---- tabelas inteiras -----------------------------------------------------------

_TEXTO = "\n".join([
    "Intro com | barra solta",
    "",
    "| Evento | Parâmetro | Valor | Problema | Correção |",
    "|---|---|---|---|---|",
    "| a | label | x | Sugere erro | y |",
    "| b | action | Clique_X | Underline em vez de hífen | clique-x |",
    "",
    "| Coluna | Outra |",
    "|:---|---:|",
    "| sugere | qualquer |",
    "",
    "| Evento | Problema | Correção |",
    "|---|---|---|",
    "| c | Parece errado | z |",
    "Fim",
])


def test_percorrer_tabelas_remove_so_o_reprovado():
    texto, removidas = filtro.percorrer_tabelas(_TEXTO, filtro._fp_linha_falsa)
    assert removidas == 2
    assert texto.split("\n") == [
        "Intro com | barra solta",
        "",
        "| Evento | Parâmetro | Valor | Problema | Correção |",
        "|---|---|---|---|---|",
        "| b | action | Clique_X | Underline em vez de hífen | clique-x |",
        "",
        "| Coluna | Outra |",
        "|:---|---:|",
        "| sugere | qualquer |",
        "",
        "Sem ajustes.",
        "Fim",
    ]


def test_filtrar_falsos_positivos_avisa_no_console(capsys):
    texto = filtro.filtrar_falsos_positivos(_TEXTO)
    assert "Sugere erro" not in texto and "Sem ajustes." in texto
    assert "[filtro] 2 apontamento(s) de falso positivo removido(s)" in capsys.readouterr().out


def test_filtrar_sem_falso_positivo_nao_avisa(capsys):
    texto = "| Problema |\n|---|\n| Usar minúsculas |"
    assert filtro.filtrar_falsos_positivos(texto) == texto
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("texto", [None, 42, "", "Sem tabela nenhuma"])
def test_filtrar_entrada_sem_tabela(texto):
    assert filtro.filtrar_falsos_positivos(texto) == texto


def test_filtro_desligado(monkeypatch):
    monkeypatch.setattr(filtro, "FILTRAR_FALSOS_POSITIVOS", False)
    assert filtro.filtrar_falsos_positivos(_TEXTO) == _TEXTO
