/* Sessão compartilhada pelas páginas da plataforma (Home, Tag Auditor,
   Tag Planner): confirma o token salvo com o servidor, desenha o avatar e o
   menu da conta no topo e faz o logout.

   Páginas internas (auditor/planner) chamam TA.sessao.exigir(): sem sessão
   válida voltam para o login com ?volta=<página>, e o login devolve a pessoa
   para lá depois de entrar. Ordem de carga: nucleo -> api -> sessao. */
(function(){
  'use strict';
  var TA = window.TA;

  // Páginas para onde o login pode devolver a pessoa (evita redirecionar
  // para qualquer endereço vindo da URL).
  var PAGINAS_VOLTA = ['/auditor', '/planner', '/admin'];

  // Confirma a sessão salva com o servidor. Resolve com o usuário
  // ({email, nome, foto, user_id, admin}) ou null (sem sessão válida).
  // Servidor reiniciado = sessão morta: limpa o que ficou no navegador.
  function validar(){
    var token = TA.token();
    if (!token) return Promise.resolve(null);
    return TA.api.me(token).then(function(r){
      if (!r.ok){
        if (r.status === 401) TA.limparSessao();
        return null;
      }
      return r.json().then(function(d){
        var usuario = d.usuario || {};
        usuario.admin = !!d.admin;
        TA.salvarSessao(null, usuario);
        return usuario;
      });
    }, function(){ return null; });
  }

  // Endereço do login que devolve a pessoa para esta página depois de entrar
  // (só para as páginas da lista; na Home é o próprio "/"). O servidor também
  // atende "/auditor/": sem a barra final o destino bate com PAGINAS_VOLTA.
  function loginComVolta(){
    var caminho = location.pathname.replace(/\/+$/, '') || '/';
    return PAGINAS_VOLTA.indexOf(caminho) !== -1
      ? '/?volta=' + encodeURIComponent(caminho) : '/';
  }

  // Para páginas internas: sem sessão, volta ao login (e retorna depois).
  // A Promise só resolve com usuário válido.
  function exigir(){
    return validar().then(function(usuario){
      if (usuario) return usuario;
      location.replace(loginComVolta());
      return new Promise(function(){});   // a página vai sair daqui
    });
  }

  // Destino pedido em ?volta= (só caminhos da lista), ou null.
  function destinoVolta(){
    var m = /[?&]volta=([^&#]*)/.exec(location.search || '');
    if (!m) return null;
    var destino = '';
    try { destino = decodeURIComponent(m[1]); } catch (e) { return null; }
    return PAGINAS_VOLTA.indexOf(destino) !== -1 ? destino : null;
  }

  // Dados da conta que ficam no navegador e não podem passar para a próxima
  // pessoa que entrar nele: o token pessoal do Figma (credencial) sai junto.
  var CHAVES_DA_CONTA = ['tagplanner_figma_token'];
  function limparDadosDaConta(){
    CHAVES_DA_CONTA.forEach(function(k){
      try { localStorage.removeItem(k); } catch (e) {}
    });
  }

  // Botão "Sair". Se a página tiver trabalho em andamento (análise ou
  // auditoria esperando a IA), pergunta ANTES de derrubar a sessão: depois do
  // logout, escolher "Ficar" no aviso do navegador deixaria a pessoa numa
  // página sem sessão. Cada ferramenta informa o seu trabalho em
  // TA.trabalhoPendente() (texto do aviso, ou vazio).
  function sair(){
    var pendente = (typeof TA.trabalhoPendente === 'function') ? TA.trabalhoPendente() : '';
    if (pendente && !confirm(pendente + ' Sair mesmo assim?')) return;
    TA.saindo = true;           // já confirmado: as páginas não avisam de novo
    var token = TA.token();
    var finalizar = function(){ TA.limparSessao(); limparDadosDaConta(); location.href = '/'; };
    if (!token){ finalizar(); return; }
    // Invalida a sessão também no servidor (se falhar, sai do mesmo jeito)
    TA.api.logout(token).then(finalizar, finalizar);
  }

  // Sessão recusada pelo servidor no meio do uso (401: servidor reiniciado ou
  // sessão expirada). Vai para o login com ?volta=, para a pessoa voltar à
  // ferramenta em que estava depois de entrar de novo (como antes, quando o
  // chat ficava na própria página do login).
  function sessaoExpirada(){
    TA.saindo = true;
    TA.limparSessao();
    location.href = loginComVolta();
  }
  TA.sair = sessaoExpirada;   // usado pelo api.js quando o servidor devolve 401

  // ---- Avatar do usuário no topo + menu com dados da conta e "Sair" -------
  function avatarPadrao(u){
    var letra = (((u && (u.nome || u.email)) || '?').trim().charAt(0) || '?').toUpperCase();
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">' +
        '<rect width="64" height="64" rx="32" fill="#CC092F"/>' +
        '<text x="32" y="42" font-family="Segoe UI, Arial, sans-serif" font-size="28" ' +
        'font-weight="600" fill="#FFFFFF" text-anchor="middle">' + letra + '</text></svg>';
    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
  }

  var menuLigado = false;
  // Preenche avatar/nome/e-mail e liga o menu (abrir/fechar, Sair). Os
  // elementos são os do bloco .usuario-area, igual em todas as páginas.
  function renderMenu(usuario){
    var u = usuario || TA.usuarioSalvo() || {};
    var padrao = avatarPadrao(u);
    [document.getElementById('fotoUsuario'), document.getElementById('menuFoto')]
    .forEach(function(img){
      if (!img) return;
      img.onerror = function(){ img.onerror = null; img.src = padrao; };
      img.src = u.foto || padrao;
    });
    var nome = document.getElementById('menuNome');
    var email = document.getElementById('menuEmail');
    if (nome) nome.textContent = u.nome || 'Usuário';
    if (email) email.textContent = u.email || '';
    // Painel administrativo: só para os e-mails de EMAILS_ADMIN
    var btnAdmin = document.getElementById('btnAdmin');
    if (btnAdmin) btnAdmin.style.display = u.admin ? 'flex' : 'none';

    if (menuLigado) return;
    menuLigado = true;
    var usuarioArea = document.getElementById('usuarioArea');
    var btnUsuario = document.getElementById('btnUsuario');
    var menuUsuario = document.getElementById('menuUsuario');
    var btnSair = document.getElementById('btnSair');
    if (btnUsuario && menuUsuario){
      btnUsuario.setAttribute('aria-haspopup', 'true');
      btnUsuario.setAttribute('aria-expanded', 'false');
      var fecharMenu = function(){
        menuUsuario.classList.remove('aberto');
        btnUsuario.setAttribute('aria-expanded', 'false');
      };
      btnUsuario.addEventListener('click', function(ev){
        ev.stopPropagation();
        var aberto = menuUsuario.classList.toggle('aberto');
        btnUsuario.setAttribute('aria-expanded', aberto ? 'true' : 'false');
      });
      // Clique fora do menu fecha o menu
      document.addEventListener('click', function(ev){
        if (menuUsuario.classList.contains('aberto') && usuarioArea && !usuarioArea.contains(ev.target)){
          fecharMenu();
        }
      });
      // Esc fecha e devolve o foco ao avatar
      document.addEventListener('keydown', function(ev){
        if (ev.key === 'Escape' && menuUsuario.classList.contains('aberto')){
          fecharMenu();
          btnUsuario.focus();
        }
      });
    }
    if (btnSair) btnSair.addEventListener('click', sair);
  }

  TA.sessao = {
    validar: validar,
    exigir: exigir,
    destinoVolta: destinoVolta,
    renderMenu: renderMenu,
    sair: sair
  };
})();
