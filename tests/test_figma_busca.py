"""Figma: busca de ponta a ponta contra uma API do Figma simulada (lista de
frames, frames com PNG + camadas, cache e memória de páginas)."""
import base64
import io
import json
import struct
import threading
import types
import urllib.error
import urllib.parse
import zlib
from collections import OrderedDict

import pytest

from tagauditor import figma

CHAVE = "AbCdEf1234567890xyz"
URL = "https://www.figma.com/design/{}/Arquivo".format(CHAVE)
TOKEN = "figd_TOKEN_de_teste_1234"
TK = "tk-teste"


def _png(largura, altura):
    """PNG RGB válido, gerado em memória."""
    def bloco(tipo, dados):
        crc = zlib.crc32(tipo + dados) & 0xFFFFFFFF
        return struct.pack(">I", len(dados)) + tipo + dados + struct.pack(">I", crc)
    ihdr = struct.pack(">IIBBBBB", largura, altura, 8, 2, 0, 0, 0)
    linhas = b"".join(b"\x00" + b"\xff\xff\xff" * largura for _ in range(altura))
    return (b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", ihdr)
            + bloco(b"IDAT", zlib.compress(linhas)) + bloco(b"IEND", b""))


PNG = _png(200, 400)


class Resposta(io.BytesIO):
    def __init__(self, corpo=b"", headers=None):
        super().__init__(corpo)
        self.headers = headers or {}


def _no(id_no, nome, tipo="FRAME", x=0, y=0, w=100, h=200, filhos=None, **extra):
    no = {"id": id_no, "name": nome, "type": tipo,
          "absoluteBoundingBox": {"x": x, "y": y, "width": w, "height": h}}
    if filhos is not None:
        no["children"] = filhos
    no.update(extra)
    return no


def _texto(id_no, caracteres, x, y, w=80, h=20):
    return _no(id_no, id_no, "TEXT", x, y, w, h, characters=caracteres)


def _tela(id_no="1:2", nome="Home", x=0, y=0):
    """Frame 100x200 com um título e um botão (instância de componente)."""
    return _no(id_no, nome, "FRAME", x, y, 100, 200, filhos=[
        _texto(id_no + "0", "Olá", x + 10, y + 10),
        _no(id_no + "1", "Entrar", "INSTANCE", x + 10, y + 100, 80, 40, componentId="c1",
            filhos=[_texto(id_no + "2", "Entrar agora", x + 15, y + 110)]),
    ])


def _url_png(id_no):
    return "https://figma-alpha-api.s3.us-west-2.amazonaws.com/images/" + id_no.replace(":", "-")


class FigmaFalso:
    """API do Figma simulada no único ponto de rede do módulo (_abrir)."""

    def __init__(self):
        self.nome = "Arquivo de teste"
        self.arquivo = None                  # corpo de /v1/files/:chave
        self.nos = {}                        # id -> documento
        self.componentes = {"c1": {"name": "Primary", "componentSetId": "s1"}}
        self.conjuntos = {"s1": {"name": "Button"}}
        self.imagens = {}                    # id -> url (padrão: _url_png)
        self.pngs = {}                       # url -> bytes ou exceção
        self.falhas = {}                     # etapa -> [exceção ou None, ...]
        self.corpos = {}                     # etapa -> corpo JSON forçado
        self.pedidos = []
        self._trava = threading.Lock()

    def etapas(self):
        return [p[0] for p in self.pedidos]

    def __call__(self, req, timeout, seguro=False):
        partes = urllib.parse.urlsplit(req.full_url)
        consulta = dict(urllib.parse.parse_qsl(partes.query))
        if partes.hostname != "api.figma.com":
            assert seguro and "X-figma-token" not in req.unredirected_hdrs
            with self._trava:
                self.pedidos.append(("png", req.full_url))
            item = self.pngs.get(req.full_url, PNG)
            if isinstance(item, BaseException):
                raise item
            return item if isinstance(item, Resposta) else Resposta(item)
        assert req.unredirected_hdrs.get("X-figma-token")
        if partes.path.endswith("/nodes"):
            etapa = "nos"
        elif partes.path.startswith("/v1/images/"):
            etapa = "imagens"
        else:
            etapa = "arquivo"
        with self._trava:
            self.pedidos.append((etapa, consulta))
        fila = self.falhas.get(etapa)
        if fila:
            exc = fila.pop(0)
            if exc is not None:
                raise exc
        if etapa in self.corpos:
            corpo = self.corpos[etapa]
        elif etapa == "arquivo":
            corpo = self.arquivo
        elif etapa == "nos":
            corpo = {"name": self.nome, "nodes": {}}
            for i in consulta["ids"].split(","):
                doc = self.nos.get(i)
                corpo["nodes"][i] = None if doc is None else {
                    "document": doc, "components": self.componentes,
                    "componentSets": self.conjuntos}
        else:
            corpo = {"err": None, "images": {
                i: self.imagens.get(i, _url_png(i)) for i in consulta["ids"].split(",")}}
        return Resposta(json.dumps(corpo).encode("utf-8"))


def _too_large():
    corpo = json.dumps({"status": 400, "err": "Request too large"}).encode("utf-8")
    return urllib.error.HTTPError("https://api.figma.com/x", 400, "Bad Request",
                                  {"Content-Type": "application/json"}, io.BytesIO(corpo))


def _http(codigo, err):
    corpo = json.dumps({"status": codigo, "err": err}).encode("utf-8")
    return urllib.error.HTTPError("https://api.figma.com/x", codigo, "erro",
                                  {"Content-Type": "application/json"}, io.BytesIO(corpo))


@pytest.fixture(autouse=True)
def isolar(monkeypatch):
    monkeypatch.setattr(figma, "_cache", OrderedDict())
    monkeypatch.setattr(figma, "_memo_paginas", OrderedDict())
    monkeypatch.setattr(figma, "_rota_estado",
                        {"testado_em": 0.0, "proxy": None, "ok": False, "anunciada": None})
    monkeypatch.setattr(figma, "FIGMA_TOKEN", "")
    monkeypatch.setattr(figma, "PROXY_FIGMA", "")
    monkeypatch.setattr(figma, "FIGMA_API", "https://api.figma.com")
    monkeypatch.setattr(figma, "FIGMA_TIMEOUT", 60)
    monkeypatch.setattr(figma, "FIGMA_CACHE_MIN", 30)
    monkeypatch.setattr(figma, "FIGMA_MAX_FRAMES", 6)
    monkeypatch.setattr(figma, "FIGMA_MAX_OPCOES", 80)
    monkeypatch.setattr(figma, "FIGMA_MAX_ELEMENTOS", 150)
    monkeypatch.setattr(figma, "FIGMA_ESCALA_MAX", 2)
    monkeypatch.setattr(figma, "FIGMA_LARGURA_ALVO", 1600)


@pytest.fixture
def api(monkeypatch):
    falso = FigmaFalso()
    monkeypatch.setattr(figma, "_abrir", falso)
    return falso


@pytest.fixture
def console(monkeypatch):
    linhas = []

    def escrever(modelo="", *valores):
        linhas.append(modelo.format(*valores) if valores else modelo)
    monkeypatch.setattr(figma.console, "escrever", escrever)
    return linhas


@pytest.fixture
def relogio(monkeypatch):
    agora = {"t": 1000.0}
    monkeypatch.setattr(figma, "time", types.SimpleNamespace(time=lambda: agora["t"]))
    return agora


def _buscar(node_ids=(), ids=None):
    url = URL
    if node_ids:
        url += "?" + "&".join("node-id=" + n.replace(":", "-") for n in node_ids)
    return figma.buscar(url, ids=ids, token_usuario=TOKEN)


# ---- Arquivo inteiro: lista de frames ---------------------------------------------

def _arquivo_com_paginas():
    return {"name": "App Bradesco", "document": {"id": "0:0", "type": "DOCUMENT", "children": [
        {"id": "0:1", "name": "Página 1", "type": "CANVAS", "children": [
            _no("1:3", "Login", x=400, y=0, w=375, h=812),
            _no("1:2", "Home", x=0, y=0, w=375, h=812),
            _no("1:4", "Fundo", "RECTANGLE", x=0, y=0, w=10, h=10),
            _no("1:9", "Rascunho", x=800, y=0, visible=False),
            "lixo",
            _no("1:5", "Fluxo", "SECTION", x=0, y=1000, w=900, h=900, filhos=[
                _no("1:7", "Passo 2", x=500, y=1100),
                _no("1:6", "Passo 1", x=100, y=1100),
            ]),
            _no("1:8", "Vazia", "SECTION", x=1000, y=1000, w=100, h=100,
                filhos=[_no("1:80", "Ret", "RECTANGLE")]),
        ]},
        {"id": "0:2", "name": "Página 2", "type": "CANVAS", "children": [
            {"id": "2:2", "name": "", "type": "COMPONENT"},
        ]},
        {"id": "0:3", "name": "Notas", "type": "TEXT"},
        "lixo",
    ]}}


def test_link_do_arquivo_lista_os_frames_das_paginas(api):
    api.arquivo = _arquivo_com_paginas()
    resposta = _buscar()
    assert resposta["arquivo"] == {"key": CHAVE, "nome": "App Bradesco"}
    opcoes = resposta["opcoes"]
    assert [(o["id"], o["nome"], o["pagina"], o["tipo"]) for o in opcoes] == [
        ("1:2", "Home", "Página 1", "FRAME"),
        ("1:3", "Login", "Página 1", "FRAME"),
        ("1:6", "Passo 1", "Página 1 / Fluxo", "FRAME"),
        ("1:7", "Passo 2", "Página 1 / Fluxo", "FRAME"),
        ("1:8", "Vazia", "Página 1", "SECTION"),
        ("2:2", "2:2", "Página 2", "COMPONENT"),
    ]
    assert (opcoes[0]["largura"], opcoes[0]["altura"]) == (375, 812)
    assert (opcoes[-1]["largura"], opcoes[-1]["altura"]) == (0, 0)
    assert "SECTION" in resposta["aviso"]
    assert api.pedidos == [("arquivo", {"depth": "2"})]

    # Repetir a busca não gasta cota (cache).
    assert _buscar() == resposta
    assert len(api.pedidos) == 1


def test_lista_longa_mostra_so_os_primeiros(api, monkeypatch):
    monkeypatch.setattr(figma, "FIGMA_MAX_OPCOES", 2)
    api.arquivo = {"name": "x", "document": {"children": [
        {"type": "CANVAS", "name": "P", "children": [
            _no("1:{}".format(i), "T{}".format(i), x=200 * i) for i in range(1, 5)]}]}}
    resposta = _buscar()
    assert [o["id"] for o in resposta["opcoes"]] == ["1:1", "1:2"]
    assert "tem 4 frames" in resposta["aviso"] and "SECTION" not in resposta["aviso"]


def test_arquivo_sem_frames_visiveis(api):
    api.arquivo = {"name": "x", "document": {"children": [
        {"type": "CANVAS", "name": "P", "children": [_no("1:1", "R", "RECTANGLE")]}]}}
    with pytest.raises(figma.ErroFigma, match="Não encontrei frames visíveis neste arquivo") as exc:
        _buscar()
    assert exc.value.status == 400


def test_arquivo_sem_documento_e_resposta_estranha(api):
    api.arquivo = {"name": "x"}
    with pytest.raises(figma.ErroFigma, match="resposta inesperada") as exc:
        _buscar()
    assert exc.value.status == 502


def test_arquivo_grande_demais_para_listar(api):
    api.falhas["arquivo"] = [_too_large()]
    with pytest.raises(figma.ErroFigma, match="grande demais para listar") as exc:
        _buscar()
    assert exc.value.status == 400 and exc.value.http == 400


def test_erro_na_lista_e_repassado(api):
    api.falhas["arquivo"] = [_http(403, "Invalid token")]
    with pytest.raises(figma.ErroFigma, match="o seu token") as exc:
        _buscar()
    assert exc.value.http == 403


def test_token_do_servidor_e_usado_sem_token_pessoal(api, monkeypatch):
    monkeypatch.setattr(figma, "FIGMA_TOKEN", "figd_servidor_teste_999")
    api.falhas["arquivo"] = [_http(403, "Forbidden")]
    with pytest.raises(figma.ErroFigma, match="o token do servidor"):
        figma.buscar(URL)


# ---- Frame do link: PNG + camadas -------------------------------------------------

def test_link_de_frame_devolve_png_e_camadas(api):
    api.nos["1:2"] = _tela()
    resposta = _buscar(["1:2"])
    assert resposta["arquivo"] == {"key": CHAVE, "nome": "Arquivo de teste"}
    assert "aviso" not in resposta
    (frame,) = resposta["frames"]
    assert {k: frame[k] for k in ("id", "nome", "pagina", "largura", "altura", "escala",
                                  "mime", "sem_imagem", "imagem_erro",
                                  "inventario_truncado")} == {
        "id": "1:2", "nome": "Home", "pagina": None, "largura": 200, "altura": 400,
        "escala": 2, "mime": "image/png", "sem_imagem": False, "imagem_erro": None,
        "inventario_truncado": False}
    assert base64.b64decode(frame["base64"]) == PNG
    assert [(i["id"], i["texto"], i["interativo"], i["nome"]) for i in frame["inventario"]] == [
        ("1:20", "Olá", False, "1:20"),
        ("1:21", "Entrar agora", True, "Entrar · Button"),
    ]
    assert api.etapas() == ["nos", "imagens", "png"]
    assert "depth" not in api.pedidos[0][1]
    assert api.pedidos[1][1] == {"ids": "1:2", "format": "png", "scale": "2",
                                 "use_absolute_bounds": "true"}

    # Segunda busca do mesmo frame: do cache, sem chamadas.
    assert _buscar(["1:2"]) == resposta
    assert len(api.pedidos) == 3


def test_frames_escolhidos_seguem_a_ordem_pedida_mesmo_do_cache(api):
    api.nos["1:2"] = _tela("1:2", "Home")
    api.nos["1:3"] = _tela("1:3", "Login", x=400)
    primeira = _buscar(ids=["1:3", "1:2"])
    assert [f["id"] for f in primeira["frames"]] == ["1:3", "1:2"]
    assert api.etapas().count("png") == 2
    segunda = _buscar(ids="1:2,1:3")
    assert [f["id"] for f in segunda["frames"]] == ["1:2", "1:3"]
    assert len(api.pedidos) == 4      # nós + imagens + 2 PNGs, só na 1ª


def test_ids_escolhidos_tem_prioridade_sobre_o_link(api):
    api.nos["1:3"] = _tela("1:3", "Login")
    resposta = _buscar(["1:2"], ids=["1:3"])
    assert [f["id"] for f in resposta["frames"]] == ["1:3"]


def test_link_com_mais_frames_que_o_limite_usa_os_primeiros(api, monkeypatch):
    monkeypatch.setattr(figma, "FIGMA_MAX_FRAMES", 1)
    api.nos["1:2"] = _tela()
    resposta = _buscar(["1:2", "1:3"])
    assert [f["id"] for f in resposta["frames"]] == ["1:2"]
    assert api.pedidos[0][1]["ids"] == "1:2"


def test_frame_que_sumiu_do_arquivo(api):
    with pytest.raises(figma.ErroFigma, match='Não encontrei o item 1:2 no arquivo "Arquivo de teste"'):
        _buscar(["1:2"])
    api.nome = ""
    with pytest.raises(figma.ErroFigma, match="Não encontrei os itens 1:2, 1:3 no arquivo \"{}\""
                       .format(CHAVE)):
        _buscar(ids=["1:2", "1:3"])


def test_resposta_de_nos_sem_nodes(api):
    api.corpos["nos"] = {"status": 200, "err": "Invalid ids."}
    with pytest.raises(figma.ErroFigma, match="recusou o pedido: Invalid ids\\.$") as exc:
        _buscar(["1:2"])
    assert exc.value.status == 400 and exc.value.detalhe == "Invalid ids."
    api.corpos["nos"] = {"name": "x"}
    with pytest.raises(figma.ErroFigma, match="resposta inesperada") as exc:
        _buscar(["1:2"])
    assert exc.value.status == 502


# ---- Páginas e seções ----------------------------------------------------------------

def _pagina():
    return {"id": "0:1", "name": "Página 1", "type": "CANVAS", "children": [
        _tela("1:2", "Home"),
        _no("1:5", "Fluxo", "SECTION", x=0, y=1000, w=900, h=900,
            filhos=[_tela("1:6", "Passo 1", x=100, y=1100)]),
    ]}


def test_link_de_pagina_lista_as_telas_e_a_escolha_lembra_a_pagina(api):
    api.nos["0:1"] = _pagina()
    api.nos["1:6"] = _tela("1:6", "Passo 1", x=100, y=1100)
    lista = _buscar(["0:1"])
    assert [(o["id"], o["pagina"]) for o in lista["opcoes"]] == [
        ("1:2", "Página 1"), ("1:6", "Página 1 / Fluxo")]
    assert api.pedidos == [("nos", {"ids": "0:1", "depth": "2"})]
    assert _buscar(["0:1"]) == lista and len(api.pedidos) == 1     # cache

    resposta = _buscar(ids=["1:6"])
    assert resposta["frames"][0]["pagina"] == "Página 1 / Fluxo"


def test_link_do_documento_lista_todas_as_paginas(api):
    api.nos["0:0"] = {"id": "0:0", "type": "DOCUMENT", "name": "Doc", "children": [
        _pagina(), {"id": "0:9", "type": "TEXT"}, "lixo"]}
    lista = _buscar(["0:0"])
    assert [o["id"] for o in lista["opcoes"]] == ["1:2", "1:6"]


def test_link_de_pagina_e_frame_juntos(api):
    api.nos["0:1"] = _pagina()
    api.nos["9:9"] = _tela("9:9", "Avulso")
    lista = _buscar(["0:1", "9:9"])
    assert [o["id"] for o in lista["opcoes"]] == ["1:2", "1:6", "9:9"]
    assert lista["opcoes"][-1]["pagina"] is None
    assert "depth" not in api.pedidos[0][1]          # não é só página: árvore inteira


def test_pagina_vazia(api):
    api.nos["0:1"] = {"id": "0:1", "name": "Rascunhos", "type": "CANVAS", "children": []}
    with pytest.raises(figma.ErroFigma, match='Não encontrei frames visíveis em a página "Rascunhos"'):
        _buscar(["0:1"])


def test_palpite_de_pagina_que_era_frame_busca_a_arvore_completa(api):
    api.nos["0:5"] = _tela("0:5", "Frame na raiz")
    resposta = _buscar(["0:5"])
    assert resposta["frames"][0]["id"] == "0:5"
    assert api.etapas() == ["nos", "nos", "imagens", "png"]
    assert api.pedidos[0][1].get("depth") == "2" and "depth" not in api.pedidos[1][1]


def test_secao_pelo_link_vira_lista_e_escolhida_na_lista_e_exportada(api):
    secao = _no("1:5", "Fluxo", "SECTION", x=0, y=0, w=900, h=900,
                filhos=[_tela("1:6", "Passo 1", x=100, y=100)])
    api.nos["1:5"] = secao
    # Escolhida na lista: a seção é exportada como imagem.
    exportada = _buscar(ids=["1:5"])
    assert exportada["frames"][0]["id"] == "1:5"
    assert _buscar(ids=["1:5"]) == exportada              # cache "frames-secao"
    n = len(api.pedidos)
    # Pelo link: a mesma seção vira a lista das telas de dentro (outro cache).
    lista = _buscar(["1:5"])
    assert [(o["id"], o["pagina"]) for o in lista["opcoes"]] == [("1:6", "Fluxo")]
    assert len(api.pedidos) == n + 1


def test_pagina_escolhida_na_lista_e_recusada(api):
    api.nos["0:1"] = _pagina()
    with pytest.raises(figma.ErroFigma, match='"Página 1" é uma página inteira') as exc:
        _buscar(ids=["0:1"])
    assert exc.value.status == 400
    api.nos["0:2"] = {"id": "0:2", "type": "CANVAS"}
    with pytest.raises(figma.ErroFigma, match='"0:2" é uma página inteira'):
        _buscar(ids=["0:2"])


# ---- Conteúdo grande demais -------------------------------------------------------------

def test_secao_enorme_pelo_link_lista_com_dois_niveis(api):
    api.nos["1:5"] = _no("1:5", "Fluxo", "SECTION", filhos=[_tela("1:6", "Passo 1")])
    api.falhas["nos"] = [_too_large()]
    lista = _buscar(["1:5"])
    assert [o["id"] for o in lista["opcoes"]] == ["1:6"]
    assert [p[1].get("depth") for p in api.pedidos] == [None, "2"]


def test_frame_enorme_pelo_link_explica_que_e_grande_demais(api):
    api.nos["1:2"] = _tela()
    api.falhas["nos"] = [_too_large()]
    with pytest.raises(figma.ErroFigma, match="O frame é grande demais") as exc:
        _buscar(["1:2"])
    assert exc.value.http == 413 and exc.value.detalhe == "too large"


@pytest.mark.parametrize("node_ids, ids, falha", [
    ((), ["1:2"], _too_large),          # escolhido na lista: não tenta de novo
    (("0:1",), None, _too_large),       # já era leitura parcial
    (("1:2",), None, lambda: _http(404, "Not found")),
])
def test_erros_na_leitura_dos_nos_sao_repassados(api, node_ids, ids, falha):
    api.falhas["nos"] = [falha()]
    with pytest.raises(figma.ErroFigma):
        _buscar(node_ids, ids=ids)
    assert len(api.pedidos) == 1


# ---- Renderização -------------------------------------------------------------------

def test_imagens_sem_mapa_com_e_sem_motivo(api):
    api.nos["1:2"] = _tela()
    api.corpos["imagens"] = {"err": "Render timeout."}
    with pytest.raises(figma.ErroFigma, match="não gerou as imagens: Render timeout\\.$") as exc:
        _buscar(["1:2"])
    assert exc.value.status == 400
    api.corpos["imagens"] = {"err": None, "images": None}
    with pytest.raises(figma.ErroFigma, match="resposta inesperada") as exc:
        _buscar(["1:2"])
    assert exc.value.status == 502


def test_frame_sem_imagem_renderizada_e_nomeado(api):
    api.nos["1:2"] = _tela()
    api.nos["1:3"] = {"id": "1:3", "type": "FRAME"}
    api.imagens = {"1:2": None, "1:3": ""}
    with pytest.raises(figma.ErroFigma, match='"Home" \\(1:2\\), "1:3" \\(1:3\\)') as exc:
        _buscar(ids=["1:2", "1:3"])
    assert exc.value.status == 400


def test_png_bloqueado_segue_so_com_camadas_e_nao_entra_no_cache(api, console):
    api.nos["1:2"] = _tela()
    api.pngs[_url_png("1:2")] = urllib.error.URLError("proxy barrou o S3")
    resposta = _buscar(["1:2"])
    (frame,) = resposta["frames"]
    assert frame["sem_imagem"] is True and frame["base64"] is None and frame["mime"] is None
    assert (frame["largura"], frame["altura"]) == (200, 400)      # caixa x escala
    assert "proxy barrou o S3" in frame["imagem_erro"]
    assert frame["inventario"]
    assert '"Home"' in resposta["aviso"] and "Enviar imagem" in resposta["aviso"]
    assert any("Download da imagem falhou" in linha for linha in console)
    # A próxima busca tenta a imagem de novo.
    del api.pngs[_url_png("1:2")]
    assert _buscar(["1:2"])["frames"][0]["sem_imagem"] is False


def test_sem_imagem_e_sem_camadas_mostra_o_erro_real(api, console):
    api.nos["1:2"] = {"id": "1:2", "name": "", "type": "FRAME"}       # sem caixa, sem filhos
    api.pngs[_url_png("1:2")] = urllib.error.URLError("proxy barrou o S3")
    with pytest.raises(figma.ErroFigma, match="proxy barrou o S3") as exc:
        _buscar(["1:2"])
    assert exc.value.status == 502


def test_soma_das_imagens_tem_teto(api, monkeypatch):
    monkeypatch.setattr(figma, "_MAX_BYTES_PNGS", len(PNG) + 1)
    api.nos["1:2"] = _tela("1:2")
    api.nos["1:3"] = _tela("1:3", "Login")
    with pytest.raises(figma.ErroFigma, match="Busque menos frames") as exc:
        _buscar(ids=["1:2", "1:3"])
    assert exc.value.status == 400


def test_imagem_grande_demais_continua_sendo_erro(api):
    api.nos["1:2"] = _tela()
    api.pngs[_url_png("1:2")] = Resposta(b"", {"Content-Length": str(41 * 1024 * 1024)})
    with pytest.raises(figma.ErroFigma, match="passou de 40 MB") as exc:
        _buscar(["1:2"])
    assert exc.value.status == 400


# ---- buscar(): falha inesperada -------------------------------------------------------

def test_falha_inesperada_vira_502_sem_vazar_o_token(api, monkeypatch, console):
    def quebra(*_a, **_k):
        raise RuntimeError("bug com " + TOKEN)
    monkeypatch.setattr(figma, "_buscar_lista", quebra)
    with pytest.raises(figma.ErroFigma, match="Falha inesperada") as exc:
        _buscar()
    assert exc.value.status == 502
    assert console and TOKEN not in "".join(console) and "***" in "".join(console)


# ---- Peças da lista ---------------------------------------------------------------------

def test_juntar():
    assert figma._juntar("Página", "Seção") == "Página / Seção"
    assert figma._juntar(None, "Seção") == "Seção"
    assert figma._juntar(None, "") is None


def test_opcao_sem_caixa_e_sem_nome():
    assert figma._opcao({"id": "1:2"}, "") == {
        "id": "1:2", "nome": "1:2", "pagina": None, "tipo": "", "largura": 0, "altura": 0}


def test_tem_telas():
    assert figma._tem_telas({"children": [_no("1:1", "x")]})
    assert not figma._tem_telas({"children": [_no("1:1", "x", visible=False), "lixo",
                                              _no("1:2", "r", "RECTANGLE")]})
    assert not figma._tem_telas({})


def test_nos_em_ordem_de_leitura_agrupa_por_linha():
    # "direita" está um pouco mais alta, mas na mesma linha (tolerância = meia
    # altura): a linha é lida da esquerda para a direita.
    nos = [_no("baixo", "baixo", x=0, y=500, h=100),
           _no("direita", "direita", x=500, y=40, h=100),
           _no("esquerda", "esquerda", x=0, y=45, h=100),
           {"id": "sem-caixa"}]
    assert [n["id"] for n in figma._nos_em_ordem_de_leitura(nos)] == \
        ["sem-caixa", "esquerda", "direita", "baixo"]
    assert figma._nos_em_ordem_de_leitura([]) == []


def test_resposta_opcoes_tira_repetidos_e_vazios():
    opcoes = [{"id": "1:2", "tipo": "FRAME", "pagina": "P"},
              {"id": "1:2", "tipo": "FRAME", "pagina": "P"},
              {"id": "", "tipo": "FRAME", "pagina": None}]
    resposta = figma._resposta_opcoes(CHAVE, None, opcoes, TK, "vazio")
    assert resposta == {"arquivo": {"key": CHAVE, "nome": ""},
                        "opcoes": [{"id": "1:2", "tipo": "FRAME", "pagina": "P"}],
                        "aviso": None}
    assert figma._memo_obter(TK, CHAVE) == {"1:2": "P"}
    with pytest.raises(figma.ErroFigma, match="^vazio$"):
        figma._resposta_opcoes(CHAVE, None, [{"id": "", "tipo": "FRAME"}], TK, "vazio")


def test_opcoes_dos_alvos_sem_conteiner_nem_id():
    with pytest.raises(figma.ErroFigma, match="Não encontrei frames visíveis em este link"):
        figma._opcoes_dos_alvos(CHAVE, "x", [{"id": "1:2", "doc": {"type": "FRAME"}}], TK)


# ---- Memória de páginas ------------------------------------------------------------------

def test_memo_guarda_paginas_com_limites(monkeypatch):
    monkeypatch.setattr(figma, "_MEMO_MAX_IDS", 2)
    monkeypatch.setattr(figma, "_MEMO_MAX_ARQUIVOS", 2)
    figma._memo_guardar(TK, "a", [{"id": "1:1", "pagina": "P1"}, {"id": "1:2", "pagina": None},
                                  {"id": "1:3", "pagina": "P3"}])
    figma._memo_guardar(TK, "a", [{"id": "1:4", "pagina": "P4"}])
    assert figma._memo_obter(TK, "a") == {"1:3": "P3", "1:4": "P4"}
    figma._memo_guardar(TK, "b", [{"id": "1:1", "pagina": "B"}])
    figma._memo_guardar(TK, "c", [{"id": "1:1", "pagina": "C"}])
    assert figma._memo_obter(TK, "a") == {}            # o mais antigo saiu
    assert figma._memo_obter(TK, "c") == {"1:1": "C"}
    copia = figma._memo_obter(TK, "c")
    copia["x"] = "y"
    assert "x" not in figma._memo_obter(TK, "c")
    assert figma._memo_obter("outro-token", "c") == {}


# ---- Cache ----------------------------------------------------------------------------------

@pytest.mark.parametrize("valor, esperado", [
    (30, 1800.0), ("1.5", 90.0), (0, 0.0), (None, 0.0), (-5, 0.0), ("abc", 0.0), (object(), 0.0),
])
def test_ttl_cache(monkeypatch, valor, esperado):
    monkeypatch.setattr(figma, "FIGMA_CACHE_MIN", valor)
    assert figma._ttl_cache() == esperado


def test_cache_desligado_nao_guarda_nem_devolve(monkeypatch):
    monkeypatch.setattr(figma, "FIGMA_CACHE_MIN", 0)
    figma._cache_guardar("k", {"frames": []})
    assert len(figma._cache) == 0
    figma._cache["k"] = (float("inf"), 1, {"x": 1})
    assert figma._cache_obter("k") is None


def test_cache_devolve_copia_e_expira(relogio):
    figma._cache_guardar("k", {"frames": [{"id": "1:2", "base64": "AAAA"}]})
    obtido = figma._cache_obter("k")
    obtido["frames"].clear()
    assert figma._cache_obter("k") == {"frames": [{"id": "1:2", "base64": "AAAA"}]}
    assert figma._cache_obter("outra") is None
    relogio["t"] += 1801
    assert figma._cache_obter("k") is None
    assert "k" not in figma._cache


def test_cache_guardar_limpa_expirados_e_respeita_limites(relogio, monkeypatch):
    figma._cache_guardar("velho", {})
    relogio["t"] += 1801
    figma._cache_guardar("novo", {})
    assert list(figma._cache) == ["novo"]

    monkeypatch.setattr(figma, "_CACHE_MAX_ITENS", 2)
    figma._cache_guardar("b", {})
    figma._cache_guardar("c", {})
    assert list(figma._cache) == ["b", "c"]

    # Teto em bytes: "c" (4096) + "d" (4106) passa de 8197 -> sai o mais antigo.
    monkeypatch.setattr(figma, "_CACHE_MAX_BYTES", 4096 * 2 + 5)
    figma._cache_guardar("d", {"frames": [{"base64": "x" * 10}]})
    assert list(figma._cache) == ["d"]
    # Resposta que sozinha passa do teto nem entra (e não derruba as outras).
    figma._cache_guardar("enorme", {"frames": [{"base64": "x" * 5000}]})
    assert list(figma._cache) == ["d"]


def test_tamanho_resposta():
    assert figma._tamanho_resposta({}) == 4096
    assert figma._tamanho_resposta({"frames": [{"base64": "abc"}, {"base64": None}, {}]}) == 4099


def test_limpar_cache_esquece_tudo():
    figma._cache_guardar("k", {})
    figma._memo_guardar(TK, CHAVE, [{"id": "1:2", "pagina": "P"}])
    figma._limpar_cache()
    assert len(figma._cache) == 0 and figma._memo_obter(TK, CHAVE) == {}


def test_na_ordem():
    resposta = {"frames": [{"id": "a"}, {"id": "x"}, {"id": "b"}]}
    assert [f["id"] for f in figma._na_ordem(resposta, ["b", "a"])["frames"]] == ["b", "a", "x"]
    assert figma._na_ordem({}, ["a"]) == {"frames": []}
