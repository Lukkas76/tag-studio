/* Tagueamento GA4 via Google Tag Manager — implementa o mapa
   "[Bot] Tag Auditor | Home v1" (tagueamento.pdf, GTM-NR72PXBG).

   São 9 disparos na tela "Home" do mapa: 1 de visualização (page_view, quando
   a tela é apresentada) e 8 de interação (Event_Data, no clique do elemento
   mapeado). A "Home" do mapa é a tela do chat do Tag Auditor (/auditor,
   auditor.html) - NÃO a Home da plataforma (/), que não tem mapa homologado
   e não dispara eventos (por isso este arquivo só é carregado no auditor).

   Camada separada de propósito: o app (chat.js/auditor.js) não sabe de
   analytics e o analytics não interfere no app. Este arquivo pendura os
   próprios listeners nos elementos do auditor.html; a única chamada que vem
   de fora é TA.tag.pageView(), feita pelo auditor.js quando a tela do Tag
   Auditor aparece (depois de confirmar a sessão, antes de TA.iniciarApp()).

   Config: TAG_* e GTM_ID em tagauditor/config.py -> window.TAG_CONFIG.tag.
   Com GTM_ID vazio nada é disparado (o servidor também não injeta o script). */
(function(){
  'use strict';
  var TA = window.TA;
  var CFG = (TA.cfg && TA.cfg.tag) || {};
  var LIGADO = !!CFG.gtmId;

  // ---- Mapa oficial ------------------------------------------------------
  // Uma linha por evento do documento. Os valores de location, action,
  // element_name e index são os HOMOLOGADOS — o GTM casa os disparos por eles.
  // Não altere sem atualizar o mapa (e vice-versa).
  // Obs.: "selcao-canal_*" está escrito assim no documento (sem o "e" de
  // "selecao"). Mantido igual de propósito: o que vale é o valor homologado.
  var MAPA = {
    'novo-chat':       { index: '2', location: 'menu-lateral', action: 'clique-botao', element_name: 'novo-chat' },
    'anexo':           { index: '3', location: 'barra-chat',   action: 'clique-botao', element_name: 'anexo' },
    'link-confluence': { index: '4', location: 'barra-chat',   action: 'clique-botao', element_name: 'link-confluence' },
    'enviar':          { index: '5', location: 'barra-chat',   action: 'clique-botao', element_name: 'enviar' },
    'canal-web':       { index: '6', location: 'barra-chat',   action: 'clique-botao', element_name: 'selcao-canal_web' },
    'canal-webview':   { index: '7', location: 'barra-chat',   action: 'clique-botao', element_name: 'selcao-canal_webview' },
    'canal-appnativo': { index: '8', location: 'barra-chat',   action: 'clique-botao', element_name: 'selcao-canal_appnativo' },
    'contato':         { index: '9', location: 'barra-chat',   action: 'clique-botao', element_name: 'contato' }
  };
  var INDEX_PAGE_VIEW = '1';

  // Valor de data-canal (auditor.html) -> chave do mapa
  var CANAL_PARA_CHAVE = {
    'Web': 'canal-web',
    'WebView': 'canal-webview',
    'App Nativo': 'canal-appnativo'
  };

  // ---- Envio para o dataLayer -------------------------------------------
  function produto(){
    return { product: CFG.produto || 'tag-auditor', flow: CFG.fluxo || 'home' };
  }
  // Hash MD5 da conta logada, calculado no servidor (login_google.py) e guardado
  // com a sessão. Sem login ainda, vai vazio em vez de quebrar o disparo.
  function usuario(){
    var u = TA.usuarioSalvo() || {};
    return { user_id: u.user_id || '' };
  }
  // Marca da taxonomia nova, na raiz do push. Os triggers "CE - * - New" do
  // container casam por "DL - event_type equals new" — sem este campo NENHUMA
  // tag do GA4 dispara, mesmo com o resto do dataLayer correto.
  // Não está no tagueamento.pdf: é exigência do container do GTM.
  function eventType(){
    return CFG.eventType || 'new';
  }
  function push(evento){
    if (!LIGADO) return false;
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push(evento);
    if (CFG.debug && window.console && console.log){
      console.log('[tag] ' + evento.event + ' index=' + evento.index, evento);
    }
    return true;
  }

  TA.tag = {
    mapa: MAPA,

    // Evento 1 do mapa: a tela do Tag Auditor ("Home" no mapa) foi
    // apresentada ao usuário. Chamado pelo auditor.js.
    pageView: function(){
      return push({
        'event': 'page_view',
        'event_type': eventType(),
        'page': {
          'page_location': CFG.pageLocation || 'tag-auditor/home',
          'page_title': CFG.pageTitle || 'Tag Auditor | Home'
        },
        'product': produto(),
        'user': usuario(),
        'index': INDEX_PAGE_VIEW
      });
    },

    // Eventos 2 a 9 do mapa: interação do usuário. `chave` é uma chave de MAPA.
    evento: function(chave){
      var m = MAPA[chave];
      if (!m){
        if (window.console && console.warn) console.warn('[tag] evento fora do mapa: ' + chave);
        return false;
      }
      return push({
        'event': 'Event_Data',
        'event_type': eventType(),
        'ga_event': {
          'location': m.location,
          'action': m.action,
          'element_name': m.element_name
        },
        'product': produto(),
        'user': usuario(),
        'index': m.index
      });
    }
  };

  // ---- Ligação com os elementos da tela ---------------------------------
  // Todos existem no auditor.html desde o carregamento (o app só fica oculto
  // até a sessão ser confirmada), então dá para pendurar os listeners uma
  // única vez.
  function aoClicar(elemento, chave){
    if (!elemento) return;
    elemento.addEventListener('click', function(){ TA.tag.evento(chave); });
  }

  function ligar(){
    if (!LIGADO) return;

    aoClicar(document.getElementById('btnNovo'), 'novo-chat');            // menu-lateral
    aoClicar(document.getElementById('btnAnexo'), 'anexo');               // barra-chat
    aoClicar(document.getElementById('btnConfluence'), 'link-confluence');
    aoClicar(document.getElementById('btnEnviar'), 'enviar');

    // Seletor de canal do plano (Web / WebView / App Nativo)
    var canais = document.querySelectorAll('#canalArea .canal-opt');
    Array.prototype.forEach.call(canais, function(b){
      aoClicar(b, CANAL_PARA_CHAVE[b.getAttribute('data-canal')]);
    });

    // Contatos do rodapé da barra lateral (os dois e-mails do time). Delegado:
    // pega qualquer link do bloco, um evento só para os dois.
    var contatos = document.querySelector('.sidebar-rodape .rodape-contatos');
    if (contatos){
      contatos.addEventListener('click', function(ev){
        var alvo = ev.target;
        while (alvo && alvo !== contatos && alvo.tagName !== 'A'){ alvo = alvo.parentNode; }
        if (alvo && alvo.tagName === 'A') TA.tag.evento('contato');
      });
    }
  }

  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', ligar);
  } else {
    ligar();
  }
})();
