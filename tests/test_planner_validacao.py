"""Tag Planner: utilitários, config do front e validação do pedido (contexto,
telas, imagens, histórico e pergunta de acompanhamento)."""
import base64
import types

import pytest

from tagauditor import planner
from tagauditor.planner import ErroPlanner


# ---- Imagens mínimas (só o cabeçalho que o planner lê) ----------------------

def _png(largura=100, altura=200):
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + largura.to_bytes(4, "big")
            + altura.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00" + b"\x00" * 4)


def _gif(largura=30, altura=40):
    return b"GIF89a" + largura.to_bytes(2, "little") + altura.to_bytes(2, "little") + b"\x00" * 4


def _jpeg(largura=320, altura=240):
    """SOI + preenchimento + APP0 + lixo + RST + SOF0: passa por todos os
    desvios do leitor de marcadores antes de achar o tamanho."""
    app0 = b"\xff\xe0\x00\x10" + b"JFIF\x00" + b"\x00" * 9
    sof = (b"\xff\xc0\x00\x11\x08" + altura.to_bytes(2, "big") + largura.to_bytes(2, "big")
           + b"\x03" + b"\x00" * 9)
    return b"\xff\xd8" + b"\xff" + app0 + b"\x00" + b"\xff\xd0" + sof


def _webp(bloco, corpo):
    dados = b"RIFF" + b"\x00" * 4 + b"WEBP" + bloco + b"\x00" * 4 + corpo
    return dados + b"\x00" * max(0, 30 - len(dados))


def _webp_vp8x(largura, altura):
    return _webp(b"VP8X", b"\x00" * 4 + (largura - 1).to_bytes(3, "little")
                 + (altura - 1).to_bytes(3, "little"))


def _webp_vp8(largura, altura):
    return _webp(b"VP8 ", b"\x00" * 3 + b"\x9d\x01\x2a" + largura.to_bytes(2, "little")
                 + altura.to_bytes(2, "little"))


def _webp_vp8l(largura, altura):
    bits = (largura - 1) | ((altura - 1) << 14)
    return _webp(b"VP8L", b"\x2f" + bits.to_bytes(4, "little"))


def _b64(dados):
    return base64.b64encode(dados).decode("ascii")


def _parte(dados=None, **extra):
    parte = {"base64": _b64(dados if dados is not None else _png()), "mime": "image/png"}
    parte.update(extra)
    return parte


def _tela(nome="Home", partes=None, **extra):
    tela = {"nome": nome, "partes": partes if partes is not None else [_parte()]}
    tela.update(extra)
    return tela


@pytest.fixture(autouse=True)
def limites_fixos(monkeypatch):
    """Limites do config.py fixos: o teste não depende de quem mexer no config."""
    monkeypatch.setattr(planner, "PLANNER_MAX_TELAS", 6)
    monkeypatch.setattr(planner, "PLANNER_MAX_PARTES", 10)
    monkeypatch.setattr(planner, "PLANNER_MAX_MB_IMAGENS", 20)


# ---- Utilitários de texto e número ------------------------------------------

def test_surrogate_solto_vira_caractere_de_substituicao():
    assert planner.sem_surrogates("a" + chr(0xD800) + "b") == "a�b"
    assert planner.sem_surrogates(None) is None
    assert planner.sem_surrogates(12) == 12


@pytest.mark.parametrize("valor, esperado", [
    (None, ""), (True, ""), (5, "5"), (2.5, "2.5"), (["x"], ""), ({"a": 1}, ""),
    ("  oi  ", "oi"), ("a\r\nb\rc", "a\nb\nc"), ("a\x00b\x07c", "abc"),
])
def test_texto_limpa_e_converte(valor, esperado):
    assert planner._texto(valor) == esperado


def test_texto_linha_unica_e_limite():
    assert planner._texto("  a \n\t b  ", linha_unica=True) == "a b"
    assert planner._texto("abc   def", limite=5) == "abc"
    assert planner._texto("x" + chr(0xDC00), linha_unica=True) == "x�"


@pytest.mark.parametrize("valor, esperado", [
    (None, None), (True, None), (False, None), (7, 7), (7.6, 8), ("12,6", 13), (" 3 ", 3),
    ("", None), ("   ", None), ("abc", None), (float("inf"), None), ([1], None),
    (0, None), (100001, None),
])
def test_inteiro_aceita_numero_e_texto_dentro_da_faixa(valor, esperado):
    assert planner._inteiro(valor) == esperado


def test_inteiro_com_faixa_propria():
    assert planner._inteiro(0, 0, 10) == 0
    assert planner._inteiro(11, 0, 10) is None


@pytest.mark.parametrize("valor, esperado", [
    (None, None), (True, None), (3, 3.0), (2.5, 2.5), ("12,5", 12.5), ("45%", 45.0),
    ("51px", 51.0), (" 7 PX ", 7.0), ("abc", None), ([1], None), (float("nan"), None),
    ("1e999", None), (10 ** 400, None),
])
def test_numero_aceita_variacoes_de_texto(valor, esperado):
    assert planner._numero(valor) == esperado


def test_formatos_brasileiros():
    assert planner._fmt_int(1234567) == "1.234.567"
    assert planner._fmt_num(20) == "20"
    assert planner._fmt_num(20.5) == "20,5"
    assert planner._fmt_mb(1572864) == "1,5"


def test_detalhe_do_erro_usa_o_nome_da_classe_sem_mensagem_e_corta_em_400():
    assert planner._detalhe_erro(ValueError()) == "ValueError"
    assert planner._detalhe_erro(RuntimeError(" falhou ")) == "falhou"
    assert len(planner._detalhe_erro(RuntimeError("x" * 1000))) == 400


def test_erro_planner_guarda_o_status():
    assert ErroPlanner("x").status == 400
    assert ErroPlanner("y", 502).status == 502
    assert str(ErroPlanner("mensagem")) == "mensagem"


# ---- Config do navegador ----------------------------------------------------

def test_figma_configurado_sem_modulo_do_figma(monkeypatch):
    monkeypatch.setattr(planner, "figma", None)
    assert planner.figma_configurado() is False


def test_figma_configurado_nunca_levanta(monkeypatch):
    def quebra():
        raise RuntimeError("config quebrada")
    monkeypatch.setattr(planner, "figma", types.SimpleNamespace(configurado=quebra))
    assert planner.figma_configurado() is False


def test_figma_configurado_com_token(monkeypatch):
    monkeypatch.setattr(planner, "figma", types.SimpleNamespace(configurado=lambda: "sim"))
    assert planner.figma_configurado() is True


def test_config_do_front_traz_os_limites_que_o_servidor_confere(monkeypatch):
    monkeypatch.setattr(planner, "figma", types.SimpleNamespace(configurado=lambda: False))
    monkeypatch.setattr(planner, "PLANNER_ESCLARECER", True)
    monkeypatch.setattr(planner, "PLANNER_ESCLARECER_MAX_PERGUNTAS", None)
    config = planner.config_frontend()
    assert config["figmaConfigurado"] is False
    assert config["maxTelas"] == 6
    assert config["maxPartes"] == 10
    assert config["maxPartesPorTela"] == planner.MAX_PARTES_POR_TELA
    assert config["limiarDivisao"] == planner.LIMIAR_DIVISAO
    assert config["esclarecer"] is True
    assert config["maxPerguntas"] == 0


# ---- Contexto do formulário -------------------------------------------------

@pytest.mark.parametrize("contexto", [None, [], {}, {"pergunta": "   "}])
def test_contexto_sem_pergunta_de_negocio_e_recusado(contexto):
    with pytest.raises(ErroPlanner, match="pergunta de negócio") as erro:
        planner.validar_contexto(contexto)
    assert erro.value.status == 400


def test_pergunta_de_negocio_longa_demais(monkeypatch):
    with pytest.raises(ErroPlanner, match="4.001 caracteres; o máximo é 4.000"):
        planner.validar_contexto({"pergunta": "x" * 4001})


def test_contexto_limpo_e_normalizado():
    ctx = planner.validar_contexto({
        "pergunta": "  Quantos contratam?  ",
        "produto": "Cartão\n  de crédito",
        "jornada": "x" * 400,
        "canal": "Web View",
        "plataforma": "IOS",
        "tela_anterior": "Home",
        "mapa_metricas": "linha 1\nlinha 2",
        "observacoes": 42,
        "esclarecimentos": "não é lista",
    })
    assert ctx["pergunta"] == "Quantos contratam?"
    assert ctx["produto"] == "Cartão de crédito"
    assert len(ctx["jornada"]) == planner.MAX_CAMPO_CURTO
    assert ctx["canal"] == "WebView"
    assert ctx["plataforma"] == "iOS"
    assert ctx["tela_anterior"] == "Home"
    assert ctx["tela_seguinte"] == ""
    assert ctx["mapa_metricas"] == "linha 1\nlinha 2"
    assert ctx["observacoes"] == "42"
    assert ctx["esclarecimentos"] == []


@pytest.mark.parametrize("valor, esperado", [
    ("app", "App nativo"), ("Aplicativo Nativo", "App nativo"), ("Não informado", ""),
    ("N/A", ""), ("", ""), ("Smart TV", "Smart TV"), (None, ""),
])
def test_canal_aceita_variacoes(valor, esperado):
    assert planner._normalizar_opcao(valor, planner._CANAIS) == esperado


def test_esclarecimentos_ignoram_itens_sem_pergunta_e_respeitam_o_maximo():
    lista = ["texto solto", {"pergunta": ""}, {"pergunta": "Qual o KPI?", "resposta": " Vendas ",
                                                "premissa": "conversão"}]
    lista += [{"pergunta": "P{}".format(n)} for n in range(10)]
    saida = planner._validar_esclarecimentos(lista)
    assert saida[0] == {"pergunta": "Qual o KPI?", "resposta": "Vendas", "premissa": "conversão"}
    # só os MAX_ESCLARECIMENTOS primeiros itens da lista são lidos
    assert len(saida) == planner.MAX_ESCLARECIMENTOS - 2
    assert saida[-1] == {"pergunta": "P1", "resposta": "", "premissa": ""}


# ---- Figma dentro da tela ---------------------------------------------------

@pytest.mark.parametrize("valor, esperado", [
    ("12:34", "12:34"), ("12-34", "12:34"), ("12%3A34", "12:34"), ("12%253A34", "12:34"),
    ("I12:34;56:78", "I12:34;56:78"), (" 1:2 ", "1:2"), ("abc", None), (12, None),
    (True, None), (None, None), (["1:2"], None),
])
def test_node_id_do_figma(valor, esperado):
    assert planner._normalizar_node_id(valor) == esperado


def test_inventario_do_figma_so_com_campos_do_spec(monkeypatch):
    monkeypatch.setattr(planner, "MAX_ITENS_INVENTARIO", 2)
    itens = planner._sanitizar_inventario([
        "texto solto",
        {"ref": "sem código válido!"},
        {"ref": "F1", "id": "1-2", "tipo": "instance", "nome": "Botão", "texto": "Solicitar",
         "interativo": True, "bbox": {"x": "0.1", "y": 0.2, "largura": 0.33333, "altura": 0.1},
         "extra": "fora do SPEC"},
        {"ref": "F2", "interativo": "sim", "bbox": {"x": 0.1}},
        {"ref": "F3"},
    ])
    assert itens == [
        {"ref": "F1", "id": "1:2", "tipo": "INSTANCE", "nome": "Botão", "texto": "Solicitar",
         "interativo": True, "bbox": {"x": 0.1, "y": 0.2, "largura": 0.3333, "altura": 0.1}},
        {"ref": "F2", "id": "", "tipo": "", "nome": "", "texto": "", "interativo": False,
         "bbox": None},
    ]
    assert planner._sanitizar_inventario("não é lista") == []
    assert planner._bbox_inventario("não é dict") is None


def test_bloco_figma_da_tela():
    assert planner._validar_figma_tela("x") is None
    bloco = planner._validar_figma_tela({"file_key": "chave/invalida", "node_id": "1-2",
                                         "inventario": [{"ref": "F1"}]})
    assert bloco["file_key"] == ""
    assert bloco["node_id"] == "1:2"
    assert [i["ref"] for i in bloco["inventario"]] == ["F1"]
    assert planner._validar_figma_tela({"file_key": "AbC123"})["file_key"] == "AbC123"


# ---- Recorte e partes -------------------------------------------------------

def test_recorte_ausente_assume_faixas_iguais():
    assert planner._validar_recorte(None, 1, 1, "x") == {"y": 0.0, "altura": 1.0}
    assert planner._validar_recorte(None, 2, 3, "x") == {"y": 0.333333, "altura": 0.333333}


def test_recorte_valido_absorve_arredondamento():
    assert planner._validar_recorte({"y": 0.5, "altura": 0.5005}, 2, 2, "x") == {
        "y": 0.5, "altura": 0.5}
    assert planner._validar_recorte({"y": "0", "altura": "1"}, 1, 1, "x") == {
        "y": 0.0, "altura": 1.0}


@pytest.mark.parametrize("recorte", [
    "meio", {"y": None, "altura": 0.5}, {"y": -0.1, "altura": 0.5}, {"y": 0.2, "altura": 0},
    {"y": 0.6, "altura": 0.6}, {"y": 1.0, "altura": 0.001},
])
def test_recorte_invalido_e_recusado(recorte):
    with pytest.raises(ErroPlanner, match="Tela 1, parte 1: recorte inválido"):
        planner._validar_recorte(recorte, 1, 2, "Tela 1, parte 1")


def test_parte_com_formato_invalido():
    with pytest.raises(ErroPlanner, match="parte 1: formato inválido"):
        planner._validar_parte("x", 1, 1, "Tela 1")


@pytest.mark.parametrize("b64", [None, "", "   ", 123])
def test_parte_sem_imagem(b64):
    with pytest.raises(ErroPlanner, match="a imagem veio vazia"):
        planner._validar_parte({"base64": b64}, 1, 1, "Tela 1")


def test_parte_aceita_data_uri_e_aproveita_o_tipo():
    parte = planner._validar_parte({"base64": " data:image/jpg;base64, QUJD "}, 1, 1, "Tela 1")
    assert parte["mime"] == "image/jpeg"
    assert parte["base64"] == "QUJD"
    # tipo informado no campo vale mais que o da data URI
    parte = planner._validar_parte({"base64": "DATA:image/gif;base64,QUJD", "mime": "image/png"},
                                   1, 1, "Tela 1")
    assert parte["mime"] == "image/png"


def test_parte_completa():
    parte = planner._validar_parte({"base64": "QUJD", "mime": "image/x-png", "largura": "100",
                                    "altura": 200.2, "faixa": 30,
                                    "recorte": {"y": 0, "altura": 0.5}}, 1, 2, "Tela 1")
    assert parte == {"parte": 1, "mime": "image/png", "base64": "QUJD", "largura": 100,
                     "altura": 200, "faixa": 30, "recorte": {"y": 0.0, "altura": 0.5}}


def test_parte_com_tipo_nao_suportado():
    with pytest.raises(ErroPlanner, match=r"formato de imagem não suportado \(image/bmp\)"):
        planner._validar_parte({"base64": "QUJD", "mime": "image/bmp"}, 1, 1, "Tela 1")


@pytest.mark.parametrize("campos", [{"largura": "abc"}, {"altura": 0}, {"largura": True}])
def test_parte_com_dimensoes_invalidas(campos):
    with pytest.raises(ErroPlanner, match="largura/altura inválidas"):
        planner._validar_parte(dict(base64="QUJD", **campos), 1, 1, "Tela 1")


# ---- Tela -------------------------------------------------------------------

def test_tela_com_formato_invalido():
    with pytest.raises(ErroPlanner, match="Tela 2: formato inválido"):
        planner._validar_tela("x", 2)


def test_tela_sem_nome_e_origem_desconhecida():
    tela = planner._validar_tela({"origem": "scanner", "partes": [_parte()]}, 3)
    assert tela["nome"] == "Tela 3"
    assert tela["origem"] == "upload"
    assert tela["sem_imagem"] is False
    assert tela["figma"] is None
    assert planner._validar_tela({"origem": "FIGMA", "partes": [_parte()]}, 1)["origem"] == "figma"


def test_tela_com_dimensoes_invalidas():
    with pytest.raises(ErroPlanner, match='Tela 1 \\("Home"\\): largura/altura inválidas'):
        planner._validar_tela(_tela(largura="larga"), 1)


def test_tela_com_partes_que_nao_sao_lista():
    with pytest.raises(ErroPlanner, match="formato das partes inválido"):
        planner._validar_tela(_tela(partes="x"), 1)


def test_tela_sem_imagem_e_sem_camadas_e_recusada():
    with pytest.raises(ErroPlanner, match="nenhuma imagem recebida"):
        planner._validar_tela({"nome": "Home", "partes": None}, 1)
    with pytest.raises(ErroPlanner, match="nenhuma imagem recebida"):
        planner._validar_tela({"nome": "Home", "partes": [], "figma": {"inventario": []}}, 1)


def test_tela_do_figma_so_com_camadas_e_aceita():
    tela = planner._validar_tela({"nome": "Home", "origem": "figma", "partes": [],
                                  "figma": {"node_id": "1:2", "inventario": [{"ref": "F1"}]}}, 1)
    assert tela["sem_imagem"] is True
    assert tela["partes"] == []


def test_tela_com_partes_demais():
    partes = [_parte() for _ in range(planner.MAX_PARTES_POR_TELA + 1)]
    with pytest.raises(ErroPlanner, match="no máximo 6 partes por tela"):
        planner._validar_tela(_tela(partes=partes), 1)


# ---- Conferência das imagens ------------------------------------------------

@pytest.mark.parametrize("dados, tipo", [
    (_png(), "image/png"), (_jpeg(), "image/jpeg"), (_gif(), "image/gif"),
    (b"GIF87a" + b"\x00" * 4, "image/gif"), (_webp_vp8x(10, 20), "image/webp"),
    (b"RIFF\x00\x00\x00\x00WEB", None), (b"<html>", None), (b"", None),
])
def test_tipo_real_pela_assinatura(dados, tipo):
    assert planner._tipo_por_assinatura(dados) == tipo


@pytest.mark.parametrize("dados, tipo, dims", [
    (_png(100, 200), "image/png", (100, 200)),
    (_png(0, 200), "image/png", None),
    (_png(100001, 5), "image/png", None),
    (b"\x89PNG\r\n\x1a\n", "image/png", None),
    (_gif(30, 40), "image/gif", (30, 40)),
    (_jpeg(320, 240), "image/jpeg", (320, 240)),
    (_webp_vp8x(1000, 2000), "image/webp", (1000, 2000)),
    (_webp_vp8(640, 480), "image/webp", (640, 480)),
    (_webp_vp8l(17, 33), "image/webp", (17, 33)),
    (_webp(b"ALPH", b""), "image/webp", None),
    (b"RIFF\x00\x00\x00\x00WEBPVP8X", "image/webp", None),
    (_png(), "image/bmp", None),
])
def test_dimensoes_lidas_do_cabecalho(dados, tipo, dims):
    assert planner._dimensoes_imagem(dados, tipo) == dims


@pytest.mark.parametrize("dados", [
    b"\xff\xd8\xff\xd9\x00\x00",                      # fim da imagem sem SOF
    b"\xff\xd8\xff\xda\x00\x00",                      # dados de imagem sem SOF
    b"\xff\xd8\xff\xc0\x00\x11\x08\x00",              # SOF cortado
    b"\xff\xd8\x00\x00\x00\x00\x00",                  # só lixo depois do SOI
])
def test_jpeg_sem_tamanho_legivel(dados):
    assert planner._dimensoes_jpeg(dados) is None


def test_jpeg_com_marcadores_sem_tamanho_antes_do_sof():
    sof = b"\xff\xc2\x00\x11\x08" + (50).to_bytes(2, "big") + (60).to_bytes(2, "big") + b"\x00"
    assert planner._dimensoes_jpeg(b"\xff\xd8\xff\x01\xff\xd8\xff\xd7" + sof) == (60, 50)


def test_cabecalho_que_quebra_a_leitura_nao_derruba(monkeypatch):
    def quebra(_dados):
        raise IndexError("cabeçalho estranho")
    monkeypatch.setattr(planner, "_dimensoes_jpeg", quebra)
    assert planner._dimensoes_imagem(_jpeg(), "image/jpeg") is None


def _tela_limpa(parte):
    return {"indice": 1, "nome": "Home", "partes": [parte]}


def _parte_limpa(dados, largura=None, altura=None):
    return {"parte": 1, "mime": "image/gif", "base64": _b64(dados), "largura": largura,
            "altura": altura, "faixa": 0}


def test_imagem_conferida_corrige_tipo_e_tamanho(capsys):
    parte = _parte_limpa(_png(100, 200), largura=50, altura=50)
    assert planner._conferir_imagem(_tela_limpa(parte), parte) == len(_png())
    assert (parte["mime"], parte["largura"], parte["altura"]) == ("image/png", 100, 200)
    assert "o navegador informou 50x50 px, mas a imagem tem 100x200 px" in capsys.readouterr().out


def test_imagem_sem_tamanho_no_cabecalho_usa_o_informado():
    parte = _parte_limpa(b"\x89PNG\r\n\x1a\n", largura=10, altura=20)
    planner._conferir_imagem(_tela_limpa(parte), parte)
    assert (parte["largura"], parte["altura"]) == (10, 20)


def test_imagem_sem_tamanho_nenhum_e_recusada():
    parte = _parte_limpa(b"\x89PNG\r\n\x1a\n", largura=10)
    with pytest.raises(ErroPlanner, match='Tela 1 \\("Home"\\), parte 1: largura/altura da imagem '
                                          'não informadas'):
        planner._conferir_imagem(_tela_limpa(parte), parte)


def test_imagem_em_base64_invalido():
    parte = _parte_limpa(b"")
    parte["base64"] = "não é base64!"
    with pytest.raises(ErroPlanner, match="não veio em base64 válido"):
        planner._conferir_imagem(_tela_limpa(parte), parte)


def test_conteudo_que_nao_e_imagem():
    parte = _parte_limpa(b"<script>alert(1)</script>")
    with pytest.raises(ErroPlanner, match="não é uma imagem PNG, JPEG, WebP ou GIF"):
        planner._conferir_imagem(_tela_limpa(parte), parte)


# ---- validar_telas / validar_analise ----------------------------------------

@pytest.mark.parametrize("lista", [None, [], "telas", {"a": 1}])
def test_pedido_sem_telas(lista):
    with pytest.raises(ErroPlanner, match="Envie ao menos uma tela"):
        planner.validar_telas(lista)


def test_telas_demais(monkeypatch):
    monkeypatch.setattr(planner, "PLANNER_MAX_TELAS", 2)
    with pytest.raises(ErroPlanner, match=r"no máximo 2 telas por análise \(foram 3\)"):
        planner.validar_telas([_tela() for _ in range(3)])


def test_partes_demais_na_analise(monkeypatch):
    monkeypatch.setattr(planner, "PLANNER_MAX_PARTES", 3)
    with pytest.raises(ErroPlanner, match="As telas somam 4 partes de imagem; o máximo por análise "
                                          "é 3"):
        planner.validar_telas([_tela(partes=[_parte(), _parte()]) for _ in range(2)])


def test_imagens_pesadas_demais(monkeypatch):
    monkeypatch.setattr(planner, "PLANNER_MAX_MB_IMAGENS", 0.00005)
    with pytest.raises(ErroPlanner, match="As imagens somam 0,0 MB; o limite por análise é 0,0 MB"):
        planner.validar_telas([_tela(partes=[_parte(), _parte()])])


def test_telas_validas_com_tamanho_real_e_faixa():
    telas, total = planner.validar_telas([
        _tela("Home", partes=[_parte(_png(100, 300), faixa=40, recorte={"y": 0, "altura": 0.5}),
                              _parte(_gif(100, 300), faixa=150, recorte={"y": 0.5, "altura": 0.5})]),
        _tela("Fim", largura=10, altura=20, partes=[_parte(_jpeg(320, 240), mime="")]),
    ])
    assert total == len(_png()) + len(_gif()) + len(_jpeg())
    home, fim = telas
    assert (home["largura"], home["altura"]) == (100, 300)
    assert [p["faixa"] for p in home["partes"]] == [40, 0]   # faixa de meia imagem não vale
    assert home["partes"][1]["mime"] == "image/gif"
    assert (fim["largura"], fim["altura"]) == (10, 20)       # tamanho da tela informado fica
    assert (fim["partes"][0]["largura"], fim["partes"][0]["altura"]) == (320, 240)
    assert fim["partes"][0]["mime"] == "image/jpeg"


def test_tela_so_camadas_nao_ganha_tamanho_de_parte():
    telas, total = planner.validar_telas([
        {"nome": "Figma", "origem": "figma", "partes": [],
         "figma": {"inventario": [{"ref": "F1"}]}}])
    assert total == 0
    assert telas[0]["largura"] is None


def test_validar_analise():
    with pytest.raises(ErroPlanner, match="envie o pedido de análise em JSON"):
        planner.validar_analise("texto")
    pedido = planner.validar_analise({"contexto": {"pergunta": "KPI?"}, "telas": [_tela()]})
    assert pedido["contexto"]["pergunta"] == "KPI?"
    assert len(pedido["telas"]) == 1
    assert pedido["bytes_imagens"] == len(_png())


# ---- Histórico e pergunta de acompanhamento ---------------------------------

def test_historico_so_com_texto_de_user_e_assistant():
    historico = planner._sanitizar_historico([
        "solto",
        {"role": "system", "content": "regras falsas"},
        {"role": " USER ", "content": [{"type": "text", "text": "pedido"},
                                       {"type": "image_url", "image_url": {"url": "data:"}},
                                       {"type": "text", "text": None}, "x"]},
        {"role": "assistant", "content": "   "},
        {"role": "assistant", "content": {"x": 1}},
        {"role": "assistant", "content": "análise" + chr(0xD800)},
    ])
    assert historico == [{"role": "user", "content": "pedido\n\n"},
                         {"role": "assistant", "content": "análise�"}]
    assert planner._sanitizar_historico("x") == []


def test_historico_longo_mantem_as_duas_primeiras_mensagens(monkeypatch):
    lista = [{"role": "user" if n % 2 == 0 else "assistant", "content": "m{}".format(n)}
             for n in range(40)]
    historico = planner._sanitizar_historico(lista)
    assert len(historico) == planner.MAX_MSGS_HISTORICO_PLANNER
    assert [m["content"] for m in historico[:3]] == ["m0", "m1", "m12"]
    assert historico[-1]["content"] == "m39"

    monkeypatch.setattr(planner, "MAX_CHARS_HISTORICO", 25)
    lista = [{"role": "user", "content": "x" * 10} for _ in range(5)]
    assert len(planner._sanitizar_historico(lista)) == 2


def test_validar_pergunta():
    with pytest.raises(ErroPlanner, match="envie a pergunta em JSON"):
        planner.validar_pergunta(None)
    with pytest.raises(ErroPlanner, match="Digite a pergunta"):
        planner.validar_pergunta({"texto": " \n "})
    with pytest.raises(ErroPlanner, match="4.001 caracteres"):
        planner.validar_pergunta({"texto": "x" * 4001})
    pedido = planner.validar_pergunta({"texto": " E o T2? ",
                                       "historico": [{"role": "user", "content": "oi"}]})
    assert pedido == {"texto": "E o T2?", "historico": [{"role": "user", "content": "oi"}]}
