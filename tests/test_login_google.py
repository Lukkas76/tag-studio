import re

from tagauditor import login_google


def test_user_id_tem_o_formato_do_mapa_de_tagueamento():
    uid = login_google._user_id_tagueamento("pessoa@exemplo.com")
    assert re.fullmatch(r"[0-9A-F]{32}", uid)


def test_user_id_e_estavel_e_diferente_por_pessoa():
    um = login_google._user_id_tagueamento("pessoa@exemplo.com")
    assert um == login_google._user_id_tagueamento("pessoa@exemplo.com")
    assert um != login_google._user_id_tagueamento("outra@exemplo.com")
