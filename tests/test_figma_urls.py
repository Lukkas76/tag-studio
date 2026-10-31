"""Figma: link colado -> chave + ids, ids escolhidos na lista e escolha do token."""
import pytest

from tagauditor import figma

CHAVE = "AbCdEf1234567890xyz"


@pytest.fixture(autouse=True)
def sem_token_no_servidor(monkeypatch):
    # Independe do tagauditor/segredos.py: no pipeline ele não existe.
    monkeypatch.setattr(figma, "FIGMA_TOKEN", "")
    monkeypatch.setattr(figma, "FIGMA_MAX_FRAMES", 6)


# ---- Textos ------------------------------------------------------------------

def test_limpar_junta_linhas_e_corta_com_reticencias():
    assert figma._limpar("  um\n dois\t três  ", 50) == "um dois três"
    assert figma._limpar("abcdefghij", 5) == "abcd…"
    assert figma._limpar(None, 10) == ""
    assert figma._limpar("abc", 1) == "a…"


def test_sem_token_mascara_o_token_no_texto():
    assert figma._sem_token("erro figd_segredo123 aqui", "figd_segredo123") == "erro *** aqui"
    # Token curto demais (ou vazio) não é trocado: evitaria mascarar letras soltas.
    assert figma._sem_token("abc", "ab") == "abc"
    assert figma._sem_token(None, None) == ""


def test_plural():
    assert figma._plural(1, "frame", "frames") == "1 frame"
    assert figma._plural(3, "frame", "frames") == "3 frames"


def test_configurado_depende_do_token_do_servidor(monkeypatch):
    assert figma.configurado() is False
    monkeypatch.setattr(figma, "FIGMA_TOKEN", "   ")
    assert figma.configurado() is False
    monkeypatch.setattr(figma, "FIGMA_TOKEN", None)
    assert figma.configurado() is False
    monkeypatch.setattr(figma, "FIGMA_TOKEN", "figd_qualquer")
    assert figma.configurado() is True


def test_erro_figma_guarda_status_http_e_detalhe():
    e = figma.ErroFigma("msg", 429, 429, "rate")
    assert str(e) == "msg" and e.status == 429 and e.http == 429 and e.detalhe == "rate"
    padrao = figma.ErroFigma("x")
    assert padrao.status == 400 and padrao.http is None and padrao.detalhe is None


# ---- node-id -----------------------------------------------------------------

@pytest.mark.parametrize("bruto, esperado", [
    ("12-34", "12:34"),
    ("12:34", "12:34"),
    ("12%3A34", "12:34"),
    ("12%253A34", "12:34"),          # codificado duas vezes
    (" I1:2;3:4 ", "I1:2;3:4"),      # camada dentro de instância
    ("I1-2;I3-4", "I1:2;I3:4"),
    ("abc", None),
    ("", None),
    (None, None),
    ("12", None),
])
def test_normalizar_id(bruto, esperado):
    assert figma._normalizar_id(bruto) == esperado


# ---- Link do Figma -----------------------------------------------------------

@pytest.mark.parametrize("url, chave, ids", [
    ("https://www.figma.com/design/{}/Nome?node-id=1-2".format(CHAVE), CHAVE, ["1:2"]),
    ("https://figma.com/file/{}/Nome".format(CHAVE), CHAVE, []),
    ("figma.com/design/{}/Nome?node-id=1-2".format(CHAVE), CHAVE, ["1:2"]),
    ("www.figma.com/proto/{}/Fluxo?starting-point-node-id=5%3A6".format(CHAVE), CHAVE, ["5:6"]),
    ("  '<https://www.figma.com/board/{}/Quadro>'  ".format(CHAVE), CHAVE, []),
    ("https://www.figma.com/design/{}/Nome?node-id=1-2&node-id=1:2&starting-point-node-id=3-4"
     .format(CHAVE), CHAVE, ["1:2", "3:4"]),
    ("https://www.figma.com/design/{}/Nome#node-id=7-8".format(CHAVE), CHAVE, ["7:8"]),
    ("https://www.figma.com/design/{}/branch/BrAnCh1234567/Nome?node-id=1-2".format(CHAVE),
     "BrAnCh1234567", ["1:2"]),
    ("https://embed.figma.com/design/{}/Nome?node-id=1-2&embed-host=share".format(CHAVE),
     CHAVE, ["1:2"]),
    ("https://www.figma.com/embed?embed_host=share&url=https%3A%2F%2Fwww.figma.com%2Fdesign%2F"
     "{}%2FNome%3Fnode-id%3D9-10".format(CHAVE), CHAVE, ["9:10"]),
    ("HTTPS://WWW.FIGMA.COM./Design/{}/x".format(CHAVE), CHAVE, []),
    ("https://www.figma.com/novotipo/{}/x".format(CHAVE), CHAVE, []),   # tipo futuro
])
def test_analisar_url_aceita_os_formatos_do_figma(url, chave, ids):
    assert figma.analisar_url(url) == (chave, ids)


def test_analisar_devolve_o_tipo_do_link():
    assert figma._analisar("https://www.figma.com/slides/{}/Deck".format(CHAVE))[2] == "slides"


@pytest.mark.parametrize("url", [
    "https://www.figma.com/design",                              # sem chave
    "https://www.figma.com/",
    "https://exemplo.com/design/{}/x".format(CHAVE),
    "https://figma.com.exemplo.com/design/{}/x".format(CHAVE),
    "https://naofigma.com/design/{}/x".format(CHAVE),
    "https://www.figma.com/files/{}/recent".format(CHAVE),        # navegação, não arquivo
    "https://www.figma.com/team/{}/Time".format(CHAVE),
    "https://www.figma.com/_x/{}/y".format(CHAVE),                # tipo fora do padrão
    "https://www.figma.com/design/curta/x",                      # chave curta demais
    "https://www.figma.com/design/123456789012345/x",            # só dígitos = time/projeto
    "https://www.figma.com/design/{}/branch/ruim!/x".format(CHAVE),
    "https://[figma.com/design/{}/x".format(CHAVE),              # URL malformada
    "https://www.figma.com/" + "a" * 4100,                        # longa demais
    # embed dentro de embed não é seguido duas vezes
    "https://www.figma.com/embed?url=https%3A%2F%2Fwww.figma.com%2Fembed%3Furl%3Dx",
])
def test_link_que_nao_e_arquivo_do_figma_e_recusado(url):
    with pytest.raises(figma.ErroFigma) as exc:
        figma.analisar_url(url)
    assert exc.value.status == 400
    assert "figma.com/design" in str(exc.value)


def test_link_vazio_pede_o_link():
    for vazio in ("", "   ", None, "''"):
        with pytest.raises(figma.ErroFigma, match="Cole o link do Figma") as exc:
            figma.analisar_url(vazio)
        assert exc.value.status == 400


def test_link_http_pede_https():
    with pytest.raises(figma.ErroFigma, match="https://") as exc:
        figma.analisar_url("http://www.figma.com/design/{}/x".format(CHAVE))
    assert exc.value.status == 400


def test_link_da_comunidade_explica_que_precisa_duplicar():
    with pytest.raises(figma.ErroFigma, match="Comunidade"):
        figma.analisar_url("https://www.figma.com/community/file/1234567890123/Kit")


def test_node_id_invalido_no_link_e_citado_na_mensagem():
    with pytest.raises(figma.ErroFigma, match='"abc"') as exc:
        figma.analisar_url("https://www.figma.com/design/{}/x?node-id=abc".format(CHAVE))
    assert exc.value.status == 400


# ---- Ids escolhidos na lista ---------------------------------------------------

def test_validar_ids_vazio_vira_lista_vazia():
    assert figma._validar_ids(None) == []
    assert figma._validar_ids("") == []
    assert figma._validar_ids([]) == []


def test_validar_ids_normaliza_e_tira_repetidos():
    assert figma._validar_ids("1-2, 3:4,,1:2") == ["1:2", "3:4"]
    assert figma._validar_ids(["1:2", " ", "1-2", "I5:6;7:8"]) == ["1:2", "I5:6;7:8"]
    assert figma._validar_ids(("9:9",)) == ["9:9"]


def test_validar_ids_de_tipo_errado_e_recusado():
    with pytest.raises(figma.ErroFigma, match="Lista de frames inválida") as exc:
        figma._validar_ids({"1:2": True})
    assert exc.value.status == 400


def test_validar_ids_cita_ate_tres_invalidos():
    with pytest.raises(figma.ErroFigma) as exc:
        figma._validar_ids(["1:2", "x", 5, "y", "z"])
    msg = str(exc.value)
    assert "x, 5, y" in msg and "z" not in msg.split("(")[1].split(")")[0]
    assert exc.value.status == 400


def test_validar_ids_respeita_o_maximo_de_frames(monkeypatch):
    with pytest.raises(figma.ErroFigma, match="no máximo 6 frames por busca \\(você marcou 7\\)"):
        figma._validar_ids(["{}:1".format(i) for i in range(7)])
    monkeypatch.setattr(figma, "FIGMA_MAX_FRAMES", 1)
    with pytest.raises(figma.ErroFigma, match="no máximo 1 frame por busca"):
        figma._validar_ids(["1:1", "2:1"])


# ---- Token ---------------------------------------------------------------------

def test_token_pessoal_tem_prioridade_sobre_o_do_servidor(monkeypatch):
    monkeypatch.setattr(figma, "FIGMA_TOKEN", "figd_servidor_123")
    assert figma._escolher_token("  figd_pessoal_456 ") == ("figd_pessoal_456", True)
    assert figma._escolher_token("") == ("figd_servidor_123", False)
    assert figma._escolher_token(None) == ("figd_servidor_123", False)


def test_sem_token_nenhum_a_busca_e_indisponivel():
    with pytest.raises(figma.ErroFigma, match="precisa de um token") as exc:
        figma._escolher_token("")
    assert exc.value.status == 503


def test_token_pessoal_invalido_nao_e_ecoado():
    with pytest.raises(figma.ErroFigma) as exc:
        figma._escolher_token("figd_com espaco_no_meio")
    assert exc.value.status == 400
    assert "figd_com" not in str(exc.value)


def test_token_do_servidor_invalido_e_problema_de_configuracao(monkeypatch):
    monkeypatch.setattr(figma, "FIGMA_TOKEN", "figd\x01controle_123")
    with pytest.raises(figma.ErroFigma, match="FIGMA_TOKEN configurado") as exc:
        figma._escolher_token(None)
    assert exc.value.status == 503
    monkeypatch.setattr(figma, "FIGMA_TOKEN", "curto")
    with pytest.raises(figma.ErroFigma) as exc:
        figma._escolher_token(None)
    assert exc.value.status == 503
