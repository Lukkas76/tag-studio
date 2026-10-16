/* Tela de login da plataforma com a conta Google (fluxo de dispositivo,
   estilo "login de TV").
   O app mostra um código curto; o usuário digita em google.com/device e
   autoriza; o servidor recebe os dados direto do Google. Sem redirect,
   funciona em qualquer URL (IP/nome interno, sem HTTPS).

   No load, a sessão salva é conferida com o servidor: válida = entra direto.
   Entrar = voltar para a ferramenta que pediu o login (?volta=/auditor...)
   ou abrir a Home (hub.js). Avatar, menu da conta e logout ficam no
   sessao.js, comum a todas as páginas. Ordem de carga (index.html):
   nucleo -> api -> sessao -> hub -> login. */
(function(){
  'use strict';
  var TA = window.TA;

  var msgErroLogin = document.getElementById('msgErroLogin');
  var LOGIN_GOOGLE_OK = !!TA.cfg.loginGoogleConfigurado;

  var btnAuthGoogle = document.getElementById('btnAuthGoogle');
  var areaDevice = document.getElementById('areaDevice');
  var codigoDevice = document.getElementById('codigoDevice');
  var btnCopiarCodigo = document.getElementById('btnCopiarCodigo');
  var linkDevice = document.getElementById('linkDevice');
  var statusDevice = document.getElementById('statusDevice');
  var pedidoLogin = null;
  var timerLogin = null;

  function iniciarBotaoGoogle(){
    if (!LOGIN_GOOGLE_OK){
      msgErroLogin.textContent = 'Login Google não configurado: preencha ' +
        'GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET (cliente "TVs e dispositivos ' +
        'de entrada limitada") no tagauditor/config.py e reinicie o servidor.';
    }
  }

  function pararPolling(){
    if (timerLogin){ clearInterval(timerLogin); timerLogin = null; }
  }
  function falhaLogin(msg){
    pararPolling();
    pedidoLogin = null;
    if (areaDevice) areaDevice.style.display = 'none';
    if (statusDevice) statusDevice.textContent = '';
    if (btnAuthGoogle) btnAuthGoogle.disabled = false;
    msgErroLogin.textContent = msg || 'Falha no login com o Google.';
  }

  // Passo 1: pede ao servidor um código de dispositivo e abre google.com/device
  if (btnAuthGoogle){
    btnAuthGoogle.addEventListener('click', function(){
      if (!LOGIN_GOOGLE_OK){ iniciarBotaoGoogle(); return; }
      msgErroLogin.textContent = '';
      btnAuthGoogle.disabled = true;
      TA.api.loginInicio()
      .then(function(res){
        if (!res.ok){ falhaLogin(res.dados.erro); return; }
        pedidoLogin = res.dados.pedido_id;
        codigoDevice.textContent = res.dados.user_code;
        linkDevice.href = res.dados.verification_url;
        areaDevice.style.display = 'block';
        var copiou = false;
        try { copiou = TA.copiarTexto(res.dados.user_code); } catch (e) {}
        statusDevice.textContent = copiou
            ? 'Código copiado! Cole na aba do Google e autorize.'
            : 'Aguardando autorização no Google...';
        window.open(res.dados.verification_url, '_blank', 'noopener');
        pararPolling();
        timerLogin = setInterval(consultarLogin, 3000);
      })
      .catch(function(){ falhaLogin('Erro ao conectar com o servidor.'); });
    });
  }

  // Passo 2: fica perguntando ao servidor se o Google já liberou o acesso
  function consultarLogin(){
    if (!pedidoLogin) return;
    TA.api.loginStatus(pedidoLogin)
    .then(function(res){
      if (res.ok && res.dados.success){
        pararPolling();
        pedidoLogin = null;
        var usuario = res.dados.usuario || {};
        usuario.admin = !!res.dados.admin;
        TA.salvarSessao(res.dados.token, usuario);
        msgErroLogin.textContent = '';
        entrar(usuario);
      } else if (res.ok && res.dados.status === 'aguardando'){
        // segue aguardando a autorização
      } else {
        falhaLogin(res.dados.erro);
      }
    })
    .catch(function(){ /* rede oscilou: tenta na próxima consulta */ });
  }

  // Ao voltar para esta aba (depois de autorizar no Google), consulta na hora
  document.addEventListener('visibilitychange', function(){
    if (!document.hidden && pedidoLogin) consultarLogin();
  });

  if (btnCopiarCodigo){
    btnCopiarCodigo.addEventListener('click', function(){
      var codigo = (codigoDevice.textContent || '').trim();
      if (!codigo) return;
      var ok = false;
      try { ok = TA.copiarTexto(codigo); } catch (e) {}
      btnCopiarCodigo.textContent = ok ? '✓ Copiado' : '✕ Não copiou';
      setTimeout(function(){ btnCopiarCodigo.textContent = '⧉ Copiar código'; }, 1800);
    });
  }

  // Tira o estado "validando" do <html> (posto pelo index.html quando há
  // token salvo): o login ou a Home podem aparecer.
  function liberarTela(){
    document.documentElement.classList.remove('validando');
  }
  // Rede lenta: se a conferência da sessão demorar, mostra o login em vez de
  // deixar só o spinner. Se a sessão for confirmada depois, entra igual.
  var timerValidando = setTimeout(liberarTela, 8000);

  // Sessão confirmada (no load ou ao fim do login): volta para a ferramenta
  // que pediu o login (só as páginas aceitas pelo sessao.js) ou abre a Home.
  function entrar(usuario){
    clearTimeout(timerValidando);
    // Pode chegar aqui com um login em andamento (sessão confirmada depois
    // do aviso de rede lenta): para de consultar o Google.
    pararPolling();
    pedidoLogin = null;
    var destino = TA.sessao.destinoVolta();
    if (destino){
      location.replace(destino);   // o "validando" segue até a página trocar
      return;
    }
    // ?volta= que não vale nada (página fora da lista): limpa da barra de
    // endereço para não ficar na URL da Home.
    if (/[?&]volta=/.test(location.search || '') && window.history && history.replaceState){
      history.replaceState(null, '', location.pathname);
    }
    liberarTela();
    TA.hub.mostrar(usuario);   // esconde o login e mostra a Home
  }

  function mostrarLogin(){
    clearTimeout(timerValidando);
    liberarTela();
    iniciarBotaoGoogle();
  }

  // Sessão salva no navegador: confirma com o servidor antes de entrar (se o
  // servidor foi reiniciado, a sessão morreu e fica o login).
  TA.sessao.validar().then(function(usuario){
    if (usuario) entrar(usuario);
    else mostrarLogin();
  }, mostrarLogin);
})();
