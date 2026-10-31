import ssl

from tagauditor import confluence, figma, login_google, rede


def test_tls_verifica_certificado_e_nome_do_servidor():
    assert rede.CTX_SSL.verify_mode == ssl.CERT_REQUIRED
    assert rede.CTX_SSL.check_hostname is True


def test_todas_as_integracoes_usam_o_contexto_verificado():
    # Bridge (rede), Confluence, login Google e Figma: um contexto só
    assert confluence.CTX_SSL is rede.CTX_SSL
    assert login_google.CTX_SSL is rede.CTX_SSL
    assert figma._CTX_FIGMA is rede.CTX_SSL
