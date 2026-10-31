"""Subida do servidor (main), banner com os endereços de acesso, registro no
DNS do Windows e backup periódico dos logs — sem abrir porta, sem threads
vivas, sem subprocess de verdade e sem dormir."""
import re
import time
import types

import pytest

from tagauditor import atividade
from tagauditor import servidor

_ENDERECOS_REAIS = servidor._enderecos_de_acesso


@pytest.fixture(autouse=True)
def _isolado(monkeypatch, tmp_path):
    monkeypatch.setattr(atividade, "_PASTA", str(tmp_path / "logs"))


# ---- _enderecos_de_acesso ---------------------------------------------------

class _SocketUdp:
    def __init__(self, ip, falha_connect):
        self.ip, self.falha_connect = ip, falha_connect
        self.destino, self.fechado = None, False

    def connect(self, destino):
        self.destino = destino
        if self.falha_connect:
            raise OSError("sem rota")

    def getsockname(self):
        return (self.ip, 54321)

    def close(self):
        self.fechado = True


def _socket_falso(nome="MAQUINA", fqdn="maquina.corp.local.", ip_udp="10.1.2.3",
                  falha_connect=False, ip_por_nome="10.9.8.7"):
    """Módulo socket de mentira: valor Exception = a função levanta."""
    criados = []

    def responde(valor):
        def funcao(*_args):
            if isinstance(valor, Exception):
                raise valor
            return valor
        return funcao

    def criar(familia, tipo):
        if isinstance(ip_udp, Exception):
            raise ip_udp
        s = _SocketUdp(ip_udp, falha_connect)
        criados.append(s)
        return s

    return types.SimpleNamespace(
        AF_INET=2, SOCK_DGRAM=2, socket=criar, criados=criados,
        gethostname=responde(nome), getfqdn=responde(fqdn),
        gethostbyname=responde(ip_por_nome))


def test_enderecos_nome_fqdn_e_ip(monkeypatch):
    falso = _socket_falso()
    monkeypatch.setattr(servidor, "socket", falso)
    assert servidor._enderecos_de_acesso(8765) == (
        "http://maquina:8765", "http://maquina.corp.local:8765", "http://10.1.2.3:8765")
    assert falso.criados[0].destino == ("10.255.255.255", 1)
    assert falso.criados[0].fechado is True


def test_enderecos_nome_com_ponto_igual_ao_fqdn_vira_url_do_nome(monkeypatch):
    monkeypatch.setattr(servidor, "socket", _socket_falso(nome="Maquina.Corp", fqdn="maquina.corp"))
    nome, fqdn, ip = servidor._enderecos_de_acesso(80)
    assert (nome, fqdn) == ("http://maquina.corp:80", None)


def test_enderecos_fqdn_sem_ponto_e_ignorado(monkeypatch):
    monkeypatch.setattr(servidor, "socket", _socket_falso(nome="pc01", fqdn="pc01"))
    assert servidor._enderecos_de_acesso(80)[:2] == ("http://pc01:80", None)


def test_enderecos_getfqdn_quebrado_fica_so_com_o_nome(monkeypatch):
    monkeypatch.setattr(servidor, "socket", _socket_falso(nome="pc01", fqdn=OSError("dns")))
    assert servidor._enderecos_de_acesso(80) == ("http://pc01:80", None, "http://10.1.2.3:80")


def test_enderecos_connect_falha_usa_ip_pelo_nome(monkeypatch):
    falso = _socket_falso(falha_connect=True, ip_por_nome="10.9.8.7")
    monkeypatch.setattr(servidor, "socket", falso)
    assert servidor._enderecos_de_acesso(9000)[2] == "http://10.9.8.7:9000"
    assert falso.criados[0].fechado is True        # fechou mesmo com a falha


def test_enderecos_ip_de_loopback_e_descartado(monkeypatch):
    monkeypatch.setattr(servidor, "socket",
                        _socket_falso(ip_udp=OSError("sem socket"), ip_por_nome="127.0.1.1"))
    assert servidor._enderecos_de_acesso(80)[2] is None


def test_enderecos_tudo_quebrado_devolve_nada(monkeypatch):
    monkeypatch.setattr(servidor, "socket", _socket_falso(
        nome=OSError("sem nome"), ip_udp=OSError("sem socket")))
    assert servidor._enderecos_de_acesso(80) == (None, None, None)


def test_enderecos_nome_vazio(monkeypatch):
    monkeypatch.setattr(servidor, "socket", _socket_falso(
        nome=None, fqdn=None, ip_udp=OSError("x"), ip_por_nome=""))
    assert servidor._enderecos_de_acesso(80) == (None, None, None)


# ---- _registrar_dns ---------------------------------------------------------

def _sys_falso(plataforma):
    return types.SimpleNamespace(platform=plataforma, exit=servidor.sys.exit)


def test_registrar_dns_fora_do_windows_nao_faz_nada(monkeypatch):
    monkeypatch.setattr(servidor, "sys", _sys_falso("linux"))
    monkeypatch.setattr(servidor.subprocess, "run", lambda *a, **k: pytest.fail("não devia rodar"))
    assert servidor._registrar_dns() is None


def test_registrar_dns_no_windows_roda_ipconfig(monkeypatch, capsys):
    chamadas = []
    monkeypatch.setattr(servidor, "sys", _sys_falso("win32"))
    monkeypatch.setattr(servidor.subprocess, "run", lambda cmd, **k: chamadas.append((cmd, k)))
    servidor._registrar_dns()
    cmd, opcoes = chamadas[0]
    assert cmd == ["ipconfig", "/registerdns"]
    assert opcoes["timeout"] == 30 and opcoes["check"] is False
    assert "[dns] Nome da máquina re-registrado" in capsys.readouterr().out


def test_registrar_dns_no_windows_com_falha_so_avisa(monkeypatch, capsys):
    def run(*a, **k):
        raise FileNotFoundError("ipconfig não encontrado")

    monkeypatch.setattr(servidor, "sys", _sys_falso("win32"))
    monkeypatch.setattr(servidor.subprocess, "run", run)
    servidor._registrar_dns()
    assert "Não consegui rodar 'ipconfig /registerdns' (ipconfig não encontrado)" in \
        capsys.readouterr().out


# ---- _backup_logs_periodico -------------------------------------------------

class _Parar(Exception):
    pass


def test_backup_periodico_copia_e_dorme_24h(monkeypatch, capsys):
    destinos, sonos = [], []
    copiados = iter([3, 0])
    monkeypatch.setattr(servidor, "PASTA_LOGS_BACKUP", "backup-logs")
    monkeypatch.setattr(servidor.atividade, "backup",
                        lambda destino: destinos.append(destino) or next(copiados))

    def dormir(segundos):
        sonos.append(segundos)
        if len(sonos) == 2:
            raise _Parar

    monkeypatch.setattr(servidor, "time", types.SimpleNamespace(sleep=dormir, time=time.time))
    with pytest.raises(_Parar):
        servidor._backup_logs_periodico()
    assert destinos == ["backup-logs", "backup-logs"]
    assert sonos == [24 * 3600, 24 * 3600]
    saida = capsys.readouterr().out
    assert saida.count("[backup]") == 1         # só avisa quando copiou algo
    assert "3 arquivo(s) de log copiado(s) para backup-logs." in saida


# ---- main -------------------------------------------------------------------

def _fabrica_servidor(falhas):
    estado = {"enderecos": [], "serviu": False, "manipulador": None}

    class _Servidor:
        def __init__(self, endereco, manipulador):
            estado["enderecos"].append(endereco)
            if len(estado["enderecos"]) <= falhas:
                raise OSError("porta em uso")
            estado["manipulador"] = manipulador

        def serve_forever(self):
            estado["serviu"] = True
            raise KeyboardInterrupt

    return _Servidor, estado


def _renovador():
    pass


def _aquecer():
    pass


def _rota_google():
    pass


@pytest.fixture
def ambiente_main(monkeypatch):
    threads, eventos, tokens = [], [], []

    class _Thread:
        def __init__(self, target=None, daemon=None, **_):
            self.target, self.daemon, self.iniciada = target, daemon, False
            threads.append(self)

        def start(self):
            self.iniciada = True        # não roda nada de verdade

    monkeypatch.setattr(servidor, "threading", types.SimpleNamespace(Thread=_Thread))
    monkeypatch.setattr(servidor.atividade, "registrar",
                        lambda acao, *a, **k: eventos.append((acao, k)))
    monkeypatch.setattr(servidor, "obter_token", lambda: tokens.append(1) or "tok")
    monkeypatch.setattr(servidor, "renovador_token", _renovador)
    monkeypatch.setattr(servidor, "aquecer_agente", _aquecer)
    monkeypatch.setattr(servidor, "rota_google", _rota_google)
    monkeypatch.setattr(servidor, "SESSION_ID", None)
    monkeypatch.setattr(servidor, "PASTA_LOGS_BACKUP", "")
    monkeypatch.setattr(servidor, "AQUECER_AGENTE", False)
    monkeypatch.setattr(servidor, "MODO_ASSINCRONO", True)
    monkeypatch.setattr(servidor, "_enderecos_de_acesso", lambda porta: (None, None, None))
    srv, estado = _fabrica_servidor(falhas=0)
    monkeypatch.setattr(servidor, "ServidorChat", srv)
    return types.SimpleNamespace(threads=threads, eventos=eventos, tokens=tokens,
                                 estado=estado, monkeypatch=monkeypatch)


def test_main_sobe_na_primeira_porta_e_encerra_no_ctrl_c(ambiente_main, capsys):
    servidor.main()
    estado = ambiente_main.estado
    assert estado["enderecos"] == [("0.0.0.0", servidor.PORTA_INICIAL)]
    assert estado["manipulador"] is servidor.ManipuladorChat
    assert estado["serviu"] is True
    assert re.fullmatch(r"865\d{9}", servidor.SESSION_ID)
    assert ambiente_main.eventos == [("servidor_iniciado", {"porta": servidor.PORTA_INICIAL})]
    assert ambiente_main.tokens == [1]
    alvos = [t.target for t in ambiente_main.threads]
    assert alvos == [servidor._registrar_dns, _rota_google, _renovador]
    assert all(t.daemon and t.iniciada for t in ambiente_main.threads)
    saida = capsys.readouterr().out
    assert "  {} - Auditor + Planner".format(servidor.NOME_PLATAFORMA.upper()) in saida
    assert "No seu PC, abra:  http://127.0.0.1:{}".format(servidor.PORTA_INICIAL) in saida
    assert "[inicialização] Token pronto." in saida
    assert "http://SEU-IP:{}".format(servidor.PORTA_INICIAL) in saida
    assert "Encerrado. Até mais!" in saida


def test_main_pula_portas_ocupadas(ambiente_main):
    srv, estado = _fabrica_servidor(falhas=2)
    ambiente_main.monkeypatch.setattr(servidor, "ServidorChat", srv)
    servidor.main()
    p = servidor.PORTA_INICIAL
    assert [e[1] for e in estado["enderecos"]] == [p, p + 1, p + 2]
    assert ambiente_main.eventos == [("servidor_iniciado", {"porta": p + 2})]


def test_main_sem_porta_livre_sai_com_erro(ambiente_main, capsys):
    srv, estado = _fabrica_servidor(falhas=10 ** 6)
    ambiente_main.monkeypatch.setattr(servidor, "ServidorChat", srv)
    with pytest.raises(SystemExit) as saida:
        servidor.main()
    assert saida.value.code == 1
    assert len(estado["enderecos"]) == 20
    assert ambiente_main.threads == [] and ambiente_main.eventos == []
    assert "Não encontrei uma porta livre" in capsys.readouterr().out


def test_main_com_backup_aquecimento_e_sem_token(ambiente_main, capsys):
    mp = ambiente_main.monkeypatch
    mp.setattr(servidor, "PASTA_LOGS_BACKUP", "backup-logs")
    mp.setattr(servidor, "AQUECER_AGENTE", True)
    mp.setattr(servidor, "MODO_ASSINCRONO", False)

    def sem_token():
        raise RuntimeError("Bridge indisponível")

    mp.setattr(servidor, "obter_token", sem_token)
    servidor.main()
    alvos = [t.target for t in ambiente_main.threads]
    assert alvos == [servidor._registrar_dns, _rota_google, servidor._backup_logs_periodico,
                     _renovador, _aquecer]
    assert "Ainda sem token (Bridge indisponível). Vou tentar em 2º plano." in \
        capsys.readouterr().out


def test_main_aquecimento_desligado_no_modo_assincrono(ambiente_main):
    ambiente_main.monkeypatch.setattr(servidor, "AQUECER_AGENTE", True)
    servidor.main()
    assert _aquecer not in [t.target for t in ambiente_main.threads]


P = servidor.PORTA_INICIAL


@pytest.mark.parametrize("enderecos, esperados, ausentes", [
    (("http://pc01:{}".format(P), "http://pc01.corp:{}".format(P), "http://10.1.1.1:{}".format(P)),
     ["COMPARTILHE ESTA URL", "      http://pc01:{}\n".format(P),
      "http://pc01.corp:{}  (use esta se a de cima não abrir)".format(P),
      "IP atual (muda a cada VPN - evite compartilhar): http://10.1.1.1:{}".format(P)],
     ["Para outros na rede"]),
    ((None, "http://pc01.corp:{}".format(P), None),
     ["COMPARTILHE ESTA URL", "      http://pc01.corp:{}  \n".format(P)],
     ["use esta se a de cima", "IP atual", "Para outros na rede"]),
    (("http://pc01:{}".format(P), None, None),
     ["COMPARTILHE ESTA URL", "      http://pc01:{}\n".format(P)],
     ["IP atual", "Para outros na rede"]),
    ((None, None, "http://10.1.1.1:{}".format(P)),
     ["Para outros na rede:  http://10.1.1.1:{}".format(P), "este IP muda quando a VPN"],
     ["COMPARTILHE ESTA URL", "SEU-IP"]),
    ((None, None, None),
     ["Para outros na rede:  http://SEU-IP:{}".format(P), "ipconfig"],
     ["COMPARTILHE ESTA URL"]),
])
def test_main_banner_conforme_os_enderecos(ambiente_main, capsys, enderecos, esperados, ausentes):
    ambiente_main.monkeypatch.setattr(servidor, "_enderecos_de_acesso", lambda porta: enderecos)
    servidor.main()
    saida = capsys.readouterr().out
    for texto in esperados:
        assert texto in saida
    for texto in ausentes:
        assert texto not in saida


def test_main_usa_os_enderecos_reais_com_socket_falso(ambiente_main, monkeypatch, capsys):
    """Integração main + _enderecos_de_acesso sem tocar na rede."""
    monkeypatch.setattr(servidor, "_enderecos_de_acesso", _ENDERECOS_REAIS)
    monkeypatch.setattr(servidor, "socket", types.SimpleNamespace(
        AF_INET=2, SOCK_DGRAM=2,
        gethostname=lambda: "pc01", getfqdn=lambda: "pc01",
        gethostbyname=lambda nome: "10.2.2.2",
        socket=lambda *a: (_ for _ in ()).throw(OSError("sem socket"))))
    servidor.main()
    saida = capsys.readouterr().out
    assert "      http://pc01:{}\n".format(P) in saida
    assert "IP atual (muda a cada VPN - evite compartilhar): http://10.2.2.2:{}".format(P) in saida
