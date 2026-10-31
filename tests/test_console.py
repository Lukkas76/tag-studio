from tagauditor import console


def test_valor_externo_sai_em_uma_linha_so(capsys):
    console.escrever("[teste] rota {} status {}", "/api\r\nlinha falsa", 200)
    assert capsys.readouterr().out == "[teste] rota /api | linha falsa status 200\n"


def test_caracteres_de_controle_viram_espaco(capsys):
    console.escrever("[teste] {}", "a\x1b[31mb\x00c")
    assert capsys.readouterr().out == "[teste] a [31mb c\n"


def test_numero_continua_numero_para_formatacao(capsys):
    console.escrever("[teste] {:.1f}s, {} item(ns)", 2.345, 3)
    assert capsys.readouterr().out == "[teste] 2.3s, 3 item(ns)\n"


def test_sem_valores_o_modelo_sai_como_esta(capsys):
    console.escrever("[teste] chaves {} ficam")
    assert capsys.readouterr().out == "[teste] chaves {} ficam\n"
