/* Abertura da página do Tag Auditor (/auditor): confirma a sessão antes de
   mostrar o chat. Sem sessão válida, o TA.sessao.exigir() devolve a pessoa
   ao login com ?volta=/auditor, e o login a traz de volta para cá.

   Mesma sequência que o antigo login.js fazia ao abrir o app (a ordem
   importa): mostra o app -> avatar/menu da conta -> page_view do mapa GA4
   -> TA.iniciarApp() (chat.js). Ordem de carga: nucleo -> markdown -> plano
   -> api -> sessao -> tagueamento -> chat -> auditor. */
(function(){
  'use strict';
  var TA = window.TA;

  function abrirApp(usuario){
    var app = document.querySelector('.app');
    if (app) app.style.display = 'flex';
    // Tira o "validando" (style.css): sai o spinner de conferência da sessão
    document.documentElement.classList.remove('validando');
    TA.sessao.renderMenu(usuario);
    // Evento 1 do mapa de tagueamento: a tela do Tag Auditor foi apresentada.
    // Aqui (e não no load da página) porque só agora a tela aparece e o
    // user_id da conta já está salvo (vai no push).
    if (TA.tag) TA.tag.pageView();
    TA.iniciarApp();
  }

  // Auditoria esperando a IA: o chat.js trava o #btnEnviar enquanto envia,
  // manda em partes ou busca no Confluence. Sair agora (Início, Sair, fechar a
  // aba) perderia a pergunta e a resposta - o chat só grava os dois no fim.
  function auditoriaEmAndamento(){
    var b = document.getElementById('btnEnviar');
    return !!(b && b.disabled);
  }
  // Texto do aviso que o "Sair" mostra antes de derrubar a sessão (sessao.js)
  TA.trabalhoPendente = function(){
    return auditoriaEmAndamento() ? 'Há uma auditoria em andamento e ela será perdida.' : '';
  };
  window.addEventListener('beforeunload', function(ev){
    if (TA.saindo || !auditoriaEmAndamento()) return;    // "Sair" já perguntou
    ev.preventDefault();
    ev.returnValue = '';
  });

  TA.sessao.exigir().then(abrirApp, function(){
    // Falha inesperada ao conferir a sessão (ex.: resposta corrompida do
    // servidor). O login confere de novo e, se for o caso, pede para entrar;
    // erros do próprio app (dentro de abrirApp) não caem aqui, então não há
    // risco de ficar indo e voltando entre as páginas.
    location.replace('/?volta=' + encodeURIComponent('/auditor'));
  });
})();
