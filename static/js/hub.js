/* Home da plataforma (index.html, depois do login): saudação, cartões das
   ferramentas e atalho do painel administrativo para admins.

   TA.hub.mostrar(usuario) é chamada pelo login.js quando a sessão é
   confirmada (no load ou ao fim do login). A Home não dispara eventos GA4:
   não há mapa homologado para ela. Textos que vêm da conta (nome) entram
   sempre por textContent, nunca como HTML. */
(function(){
  'use strict';
  var TA = window.TA;

  function el(id){ return document.getElementById(id); }

  function nomePlataforma(){
    return (TA.cfg.plataforma && TA.cfg.plataforma.nome) || 'Tag Studio';
  }

  // "Lucas Silva" -> "Lucas". Nome todo em maiúsculas (comum em cadastro
  // corporativo) vira "Lucas" para a saudação não parecer um grito.
  function primeiroNome(usuario){
    var nome = String((usuario && usuario.nome) || '').trim().split(/\s+/)[0] || '';
    if (nome && nome === nome.toUpperCase() && nome !== nome.toLowerCase()){
      nome = nome.charAt(0) + nome.slice(1).toLowerCase();
    }
    return nome;
  }

  // ---- Saudação --------------------------------------------------------------
  function renderSaudacao(usuario){
    var alvo = el('hubSaudacao');
    if (!alvo) return;
    var nome = primeiroNome(usuario);
    alvo.textContent = '';
    if (!nome){ alvo.textContent = 'Olá.'; return; }
    var span = document.createElement('span');
    span.className = 'hub-nome';
    span.textContent = nome;
    alvo.appendChild(document.createTextNode('Olá, '));
    alvo.appendChild(span);
    alvo.appendChild(document.createTextNode('.'));
  }

  // ---- Mostrar a Home ---------------------------------------------------------
  function mostrar(usuario){
    var u = usuario || TA.usuarioSalvo() || {};
    var telaLogin = el('telaLogin');
    var telaHub = el('telaHub');
    if (telaLogin) telaLogin.style.display = 'none';
    telaHub.hidden = false;

    TA.sessao.renderMenu(u);
    // Já estamos no início: o atalho "Início da plataforma" do menu sobra aqui
    var btnInicio = el('btnInicio');
    if (btnInicio) btnInicio.style.display = 'none';

    renderSaudacao(u);
    document.title = nomePlataforma() + ' | Início';
    el('hubAdmin').hidden = !u.admin;

    // Foco no título: depois do login, o botão que tinha o foco sumiu; assim
    // teclado e leitor de tela começam pela saudação.
    var titulo = el('hubTitulo');
    if (titulo){
      try { titulo.focus({ preventScroll: true }); } catch (e) { titulo.focus(); }
    }
  }

  TA.hub = { mostrar: mostrar };
})();
