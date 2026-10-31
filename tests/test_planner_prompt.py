"""Tag Planner: system prompt (regras + documentação + instruções), mensagem
multimodal para a IA, estimativa de tokens e chamada à IA (_gerar)."""
import os

import pytest

from tagauditor import planner
from tagauditor.planner import ErroPlanner


@pytest.fixture
def arquivos(tmp_path, monkeypatch):
    """Regras e documentação em tmp_path, com o cache do prompt zerado."""
    regras = tmp_path / "regras.txt"
    doc = tmp_path / "doc.md"
    regras.write_text("﻿REGRAS DO TIME\n", encoding="utf-8")
    doc.write_text("DOCUMENTAÇÃO OFICIAL", encoding="utf-8")
    monkeypatch.setattr(planner, "PLANNER_ARQUIVO_PROMPT", str(regras))
    monkeypatch.setattr(planner, "PLANNER_ARQUIVO_DOCUMENTACAO", str(doc))
    monkeypatch.setitem(planner._cache_prompt, "chave", None)
    monkeypatch.setitem(planner._cache_prompt, "texto", None)
    return regras, doc


# ---- System prompt ----------------------------------------------------------

def test_caminho_relativo_e_da_pasta_do_projeto(tmp_path):
    assert planner._caminho_projeto("regras.txt") == os.path.join(planner.BASE_DIR, "regras.txt")
    assert planner._caminho_projeto(str(tmp_path)) == str(tmp_path)


def test_assinatura_de_arquivo_inexistente(tmp_path):
    assert planner._assinatura(str(tmp_path / "nao-existe.txt")) is None
    arquivo = tmp_path / "a.txt"
    arquivo.write_bytes(b"abc")
    assert planner._assinatura(str(arquivo))[1] == 3


def test_prompt_junta_regras_documentacao_e_instrucoes(arquivos, capsys):
    texto = planner.obter_system_prompt()
    assert texto == planner.SEPARADOR_PROMPT.join([
        "REGRAS DO TIME",
        planner.TITULO_DOCUMENTACAO + "\n\nDOCUMENTAÇÃO OFICIAL",
        planner.INSTRUCOES_FERRAMENTA,
    ])
    assert "System prompt carregado" in capsys.readouterr().out


def test_prompt_fica_em_cache_enquanto_os_arquivos_nao_mudam(arquivos, monkeypatch):
    primeiro = planner.obter_system_prompt()

    def nao_deveria_ler(_caminho):
        raise AssertionError("leu o arquivo de novo")
    monkeypatch.setattr(planner, "_ler_texto", nao_deveria_ler)
    assert planner.obter_system_prompt() is primeiro


def test_prompt_recarrega_quando_o_arquivo_muda(arquivos, capsys):
    regras, _doc = arquivos
    planner.obter_system_prompt()
    regras.write_text("REGRAS NOVAS, bem mais longas que antes", encoding="utf-8")
    texto = planner.obter_system_prompt()
    assert texto.startswith("REGRAS NOVAS, bem mais longas que antes")
    assert "recarregado (arquivo alterado)" in capsys.readouterr().out


def test_prompt_sem_documentacao_configurada(arquivos, monkeypatch):
    monkeypatch.setattr(planner, "PLANNER_ARQUIVO_DOCUMENTACAO", "")
    texto = planner.obter_system_prompt()
    assert texto == "REGRAS DO TIME" + planner.SEPARADOR_PROMPT + planner.INSTRUCOES_FERRAMENTA


@pytest.mark.parametrize("qual", ["inexistente", "pasta"])
def test_prompt_segue_sem_documentacao_ilegivel(arquivos, monkeypatch, tmp_path, capsys, qual):
    caminho = tmp_path / "doc-ruim"
    if qual == "pasta":
        caminho.mkdir()
    monkeypatch.setattr(planner, "PLANNER_ARQUIVO_DOCUMENTACAO", str(caminho))
    texto = planner.obter_system_prompt()
    assert planner.TITULO_DOCUMENTACAO not in texto
    assert texto.endswith(planner.INSTRUCOES_FERRAMENTA)
    assert "sigo sem a base de conhecimento" in capsys.readouterr().out


def test_prompt_em_ansi_e_lido_como_cp1252(arquivos, capsys):
    regras, _doc = arquivos
    regras.write_bytes("Regras de mensuração".encode("cp1252"))
    assert planner.obter_system_prompt().startswith("Regras de mensuração")
    assert "não está em UTF-8" in capsys.readouterr().out


def test_prompt_sem_arquivo_de_regras_configurado(arquivos, monkeypatch):
    monkeypatch.setattr(planner, "PLANNER_ARQUIVO_PROMPT", "")
    with pytest.raises(ErroPlanner, match="não está configurado") as erro:
        planner.obter_system_prompt()
    assert erro.value.status == 500


def test_prompt_sem_arquivo_de_regras(arquivos):
    regras, _doc = arquivos
    regras.unlink()
    with pytest.raises(ErroPlanner, match="não foi encontrado") as erro:
        planner.obter_system_prompt()
    assert erro.value.status == 500


def test_prompt_com_regras_ilegiveis(arquivos, monkeypatch, tmp_path):
    pasta = tmp_path / "regras-pasta"
    pasta.mkdir()
    monkeypatch.setattr(planner, "PLANNER_ARQUIVO_PROMPT", str(pasta))
    with pytest.raises(ErroPlanner, match="não foi encontrado") as erro:
        planner.obter_system_prompt()
    assert erro.value.status == 500


def test_prompt_com_regras_vazias(arquivos):
    regras, _doc = arquivos
    regras.write_text(" \n\n ", encoding="utf-8")
    with pytest.raises(ErroPlanner, match="está vazio") as erro:
        planner.obter_system_prompt()
    assert erro.value.status == 500


def test_prompt_da_etapa_de_duvidas_troca_as_instrucoes(arquivos, monkeypatch):
    monkeypatch.setattr(planner, "PLANNER_ESCLARECER_MAX_PERGUNTAS", 2)
    texto = planner._system_prompt_esclarecimento()
    assert texto.startswith("REGRAS DO TIME")
    assert "DOCUMENTAÇÃO OFICIAL" in texto
    assert planner.INSTRUCOES_FERRAMENTA not in texto
    assert "Faça no máximo 2 perguntas" in texto
    assert "{max}" not in texto


def test_prompt_da_etapa_de_duvidas_com_base_diferente(monkeypatch):
    monkeypatch.setattr(planner, "obter_system_prompt", lambda: "SÓ REGRAS")
    monkeypatch.setattr(planner, "PLANNER_ESCLARECER_MAX_PERGUNTAS", 0)
    texto = planner._system_prompt_esclarecimento()
    assert texto.startswith("SÓ REGRAS" + planner.SEPARADOR_PROMPT)
    assert "Faça no máximo 1 perguntas" in texto


# ---- Texto do pedido --------------------------------------------------------

def _parte(n=1, total=1, largura=100, altura=200, faixa=0):
    return {"parte": n, "mime": "image/png", "base64": "QUJD", "largura": largura,
            "altura": altura, "faixa": faixa,
            "recorte": {"y": (n - 1) / float(total), "altura": 1.0 / total}}


def _tela(indice=1, nome="Home", partes=None, origem="upload", figma=None,
          largura=100, altura=200):
    return {"indice": indice, "nome": nome, "origem": origem, "largura": largura,
            "altura": altura, "figma": figma,
            "partes": [_parte()] if partes is None else partes}


def _contexto(**campos):
    ctx = {"pergunta": "Quantos contratam?", "produto": "", "jornada": "", "tela": "",
           "canal": "", "plataforma": "", "tela_anterior": "", "tela_seguinte": "",
           "mapa_metricas": "", "observacoes": "", "esclarecimentos": []}
    ctx.update(campos)
    return ctx


INVENTARIO = [
    {"ref": "F1", "id": "1:2", "tipo": "INSTANCE", "nome": "Button/Primary", "texto": "Solicitar",
     "interativo": True, "bbox": {"x": 0.05, "y": 0.8, "largura": 0.9, "altura": 0.06}},
    {"ref": "F2", "id": "", "tipo": "", "nome": "", "texto": "", "interativo": False,
     "bbox": None},
]


def test_linha_do_inventario():
    assert planner._linha_inventario(INVENTARIO[0]) == (
        'F1 | INSTANCE "Button/Primary" | texto: "Solicitar" | interativo | '
        "x=0.050 y=0.800 l=0.900 a=0.060")
    assert planner._linha_inventario(INVENTARIO[1]) == "F2"
    item = dict(INVENTARIO[1], nome="Card")
    assert planner._linha_inventario(item) == 'F2 | "Card"'


def test_bloco_de_codigo_com_cerca_maior_que_as_crases_do_texto():
    assert planner._bloco_codigo("abc") == "```\nabc\n```"
    assert planner._bloco_codigo("tem ```` dentro") == "`````\ntem ```` dentro\n`````"


def test_origem_descrita():
    assert planner._descrever_origem(_tela(origem="colada")) == "colada"
    assert planner._descrever_origem(_tela(origem="figma", figma={"node_id": "1:2"})) == \
        "Figma (frame 1:2)"
    assert planner._descrever_origem(_tela(origem="figma")) == "Figma"


def test_linhas_da_tela_por_quantidade_de_partes():
    assert planner._linhas_tela(_tela()) == [
        '- Imagem 1 — "Home" — origem: upload — 100×200 px (1 parte: 100×200 px)']
    dividida = _tela(partes=[_parte(1, 3), _parte(2, 3), _parte(3, 3)], altura=600)
    linhas = planner._linhas_tela(dividida)
    assert linhas[0] == '- Imagem 1 — "Home" — origem: upload — dividida em 3 partes:'
    assert linhas[1] == "  - parte 1 de 3: 100×200 px — faixa de 0% a 33% da altura da tela"
    assert linhas[3] == "  - parte 3 de 3: 100×200 px — faixa de 67% a 100% da altura da tela"
    so_camadas = _tela(partes=[], origem="figma", figma={"node_id": "1:2"})
    assert planner._linhas_tela(so_camadas) == [
        '- Imagem 1 — "Home" — origem: Figma (frame 1:2) — 100×200 px — SEM IMAGEM: só o '
        "inventário de camadas do Figma (a imagem não pôde ser baixada)"]
    sem_tamanho = _tela(partes=[], largura=None, altura=None)
    assert "origem: upload — SEM IMAGEM" in planner._linhas_tela(sem_tamanho)[0]


def test_texto_do_pedido_minimo():
    texto = planner.montar_texto_contexto(_contexto(), [_tela()])
    linhas = texto.split("\n")
    assert linhas[:4] == ["## Pedido de análise de mensuração",
                          "**Pergunta de negócio:** Quantos contratam?",
                          "**Canal:** " + planner.CANAL_NAO_INFORMADO,
                          "**Plataforma:** " + planner.NAO_INFORMADO]
    assert "**Mapa de métricas existente:**\n" + planner.NAO_INFORMADO in texto
    for ausente in ("**Produto:**", "**Jornada:**", "Esclarecimentos", "Inventário", "SEM IMAGEM"):
        assert ausente not in texto


def test_texto_do_pedido_completo():
    ctx = _contexto(produto="Cartão", jornada="Contratação", tela="Oferta", canal="App nativo",
                    plataforma="Android", tela_anterior="Home", tela_seguinte="Sucesso",
                    observacoes="Campanha X", mapa_metricas="evento ``` estranho",
                    esclarecimentos=[
                        {"pergunta": "Qual a conversão?", "resposta": "Contrato\nassinado",
                         "premissa": ""},
                        {"pergunta": "Inclui PJ?", "resposta": "", "premissa": "Só PF"},
                        {"pergunta": "Qual o canal?", "resposta": "", "premissa": ""},
                    ])
    telas = [_tela(1, "Oferta", figma={"node_id": "1:2", "inventario": INVENTARIO}),
             _tela(2, "Resumo", partes=[], origem="figma",
                   figma={"node_id": "3:4", "inventario": INVENTARIO[:1]}),
             _tela(3, "Fim", partes=[], origem="figma", figma=None)]
    texto = planner.montar_texto_contexto(ctx, telas)
    for esperado in ("**Produto:** Cartão", "**Jornada:** Contratação", "**Nome da tela:** Oferta",
                     "**Canal:** App nativo", "**Plataforma:** Android",
                     "**Tela anterior:** Home", "**Próxima tela:** Sucesso",
                     "**Observações:** Campanha X", "````\nevento ``` estranho\n````",
                     "## Esclarecimentos antes da análise",
                     "1. **Qual a conversão?**\n   - Resposta do usuário: Contrato assinado",
                     "2. **Inclui PJ?**\n   - Sem resposta. Premissa assumida: Só PF",
                     "3. **Qual o canal?**\n   - Sem resposta. Premissa assumida: use o seu "
                     "melhor julgamento",
                     "Imagens sem arquivo de imagem (2, 3)",
                     '## Inventário de camadas do Figma — Imagem 1 "Oferta"',
                     '## Inventário de camadas do Figma — Imagem 2 "Resumo"'):
        assert esperado in texto
    assert 'Imagem 3 "Fim"' not in texto


def test_rotulo_de_cada_parte():
    tela = _tela()
    assert planner._rotulo_parte(tela, _parte(), 1) == 'Imagem 1 ("Home") — 100×200 px'
    assert planner._rotulo_parte(tela, _parte(2, 3, faixa=24), 3) == (
        'Imagem 1 — parte 2 de 3 ("Home") — 100×200 px, com faixa de identificação de 24 px '
        "no topo")


def test_mensagem_multimodal_com_rotulo_antes_de_cada_imagem():
    telas = [_tela(1, "A"), _tela(2, "B", partes=[_parte(1, 2), _parte(2, 2)])]
    conteudo, texto, user_content = planner.montar_mensagem_analise(_contexto(), telas, "high")
    assert conteudo[0] == {"type": "text", "text": texto}
    assert [p["type"] for p in conteudo] == ["text", "text", "image_url", "text", "image_url",
                                             "text", "image_url"]
    assert conteudo[1]["text"].startswith('Imagem 1 ("A")')
    assert conteudo[3]["text"].startswith("Imagem 2 — parte 1 de 2")
    assert conteudo[2]["image_url"] == {"url": "data:image/png;base64,QUJD", "detail": "high"}
    assert user_content == texto + "\n\n" + planner.NOTA_IMAGENS_HISTORICO
    assert planner.caracteres_de_texto(conteudo) == sum(
        len(p["text"]) for p in conteudo if p["type"] == "text")


def test_mensagem_sem_detalhe_omite_o_campo():
    conteudo, _texto, _uc = planner.montar_mensagem_analise(_contexto(), [_tela()], None)
    assert conteudo[2]["image_url"] == {"url": "data:image/png;base64,QUJD"}


def test_mensagem_so_com_camadas_vai_como_texto_puro():
    telas = [_tela(partes=[], origem="figma", figma={"inventario": INVENTARIO})]
    conteudo, texto, user_content = planner.montar_mensagem_analise(_contexto(), telas, "high")
    assert conteudo == texto == user_content
    assert planner.caracteres_de_texto(conteudo) == len(texto)


def test_caracteres_de_texto_ignora_o_que_nao_e_texto():
    assert planner.caracteres_de_texto(None) == 0
    assert planner.caracteres_de_texto([{"type": "text", "text": None}, "solto",
                                        {"type": "text", "text": "abc"}]) == 3


# ---- Estimativa de tokens das imagens ---------------------------------------

@pytest.mark.parametrize("largura, altura, detalhe, tokens", [
    (100, 200, "high", 34),           # 4 x 7 patches = 28 -> 33,6 -> 34
    (100, 200, None, 34),             # sem detalhe = "auto" = "high"
    (100, 200, " AUTO ", 34),
    (4096, 4096, "high", 3000),       # reduzida a 2048 e no teto de 2500 patches
    (4096, 4096, "low", 4916),        # 64 x 64 = 4096 patches (teto 6144)
    (7000, 100, "original", 677),     # 6000 x 85 -> 188 x 3 patches
    (0, 0, "high", 2),
])
def test_estimativa_de_tokens_de_uma_imagem(largura, altura, detalhe, tokens):
    assert planner.estimar_tokens_imagem(largura, altura, detalhe) == tokens


def test_estimativa_de_tokens_das_telas():
    telas = [_tela(), _tela(2, partes=[_parte(), _parte()]), _tela(3, partes=[])]
    assert planner.estimar_tokens_telas(telas, "high") == 3 * 34


# ---- Recusa de imagem e chamada à IA ----------------------------------------

@pytest.mark.parametrize("erro, recusa", [
    (RuntimeError("HTTP 400: invalid image_url"), True),
    (RuntimeError("Bridge: content unsupported"), True),
    (RuntimeError("HTTP 500 ao processar a imagem"), False),
    (RuntimeError("HTTP 429: image rate limit"), False),
    (RuntimeError("Tempo esgotado esperando a imagem"), False),
    (RuntimeError("Não consegui conectar à Bridge"), False),
    (RuntimeError("erro desconhecido"), False),
    (ValueError("HTTP 400 image"), False),
])
def test_recusa_de_imagem_so_quando_o_conteudo_foi_recusado(erro, recusa):
    assert planner.eh_recusa_de_imagem(erro) is recusa


def test_gerar_chama_a_bridge_com_os_parametros_do_planner(monkeypatch):
    chamadas = []

    def gerar_texto(mensagens, system_prompt, **kw):
        chamadas.append((mensagens, system_prompt, kw))
        return 1234, "uso sem formato"
    monkeypatch.setattr(planner.bridge, "gerar_texto", gerar_texto)
    monkeypatch.setattr(planner, "PLANNER_REASONING_EFFORT", None)
    monkeypatch.setattr(planner, "PLANNER_MODELO_LLM", "modelo-x")
    monkeypatch.setattr(planner, "PLANNER_MAX_TOKENS_RESPOSTA", 99)
    texto, uso = planner._gerar([{"role": "user", "content": "oi"}], "SYS", 7)
    assert (texto, uso) == ("1234", {})
    mensagens, system_prompt, kw = chamadas[0]
    assert system_prompt == "SYS"
    assert kw == {"modelo": "modelo-x", "max_tokens": 99, "reasoning_effort": "",
                  "rotulo": "planner", "tokens_extra_estimados": 7}


@pytest.mark.parametrize("resposta", [None, "", "   \n"])
def test_gerar_com_resposta_vazia(monkeypatch, resposta):
    monkeypatch.setattr(planner.bridge, "gerar_texto", lambda *a, **k: (resposta, {"total": 1}))
    with pytest.raises(ErroPlanner, match="A IA respondeu vazio") as erro:
        planner._gerar([], "SYS", 0)
    assert erro.value.status == 502


def test_falha_da_ia_vira_502():
    erro = planner._falha_ia(RuntimeError("HTTP 503"))
    assert erro.status == 502
    assert str(erro) == "Não consegui concluir a análise com a IA: HTTP 503"
