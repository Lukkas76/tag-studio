/* Chamadas ao servidor (fetch). Todas devolvem Promises com { ok, dados }.
   Em 401 nas rotas protegidas, derruba a sessão local (TA.sair, definido
   em sessao.js) e rejeita com mensagem amigável. */
(function(){
  'use strict';
  var TA = window.TA;

  // Corpo da resposta como objeto, mesmo quando o servidor não devolve JSON
  // (ex.: 413 em texto puro) - assim quem chama sempre lê dados.erro.
  function corpoJson(r){
    return r.text().then(function(t){
      try { return JSON.parse(t); }
      catch (e) { return { erro: (t && t.length < 300) ? t : ('Erro HTTP ' + r.status + '.') }; }
    });
  }
  function comSessao(r){
    if (r.status === 401){
      if (TA.sair) TA.sair();
      return Promise.reject(new Error('Sessão expirada. Faça login novamente.'));
    }
    return corpoJson(r).then(function(d){ return { ok: r.ok, status: r.status, dados: d }; });
  }
  function json(r){
    return corpoJson(r).then(function(d){ return { ok: r.ok, status: r.status, dados: d }; });
  }
  function postAutenticado(url, corpo){
    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + TA.token()
      },
      body: JSON.stringify(corpo || {})
    }).then(comSessao);
  }

  TA.api = {
    me: function(token){
      return fetch('/api/me', { headers: { 'Authorization': 'Bearer ' + token } });
    },
    loginInicio: function(){
      return fetch('/api/login/inicio', { method: 'POST' }).then(json);
    },
    loginStatus: function(pedidoId){
      return fetch('/api/login/status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pedido_id: pedidoId })
      }).then(json);
    },
    logout: function(token){
      return fetch('/api/logout', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token }
      });
    },
    // Resumo de uso do mês para a Home (do usuário e da plataforma).
    resumo: function(){
      return fetch('/api/resumo', {
        headers: { 'Authorization': 'Bearer ' + TA.token() }
      }).then(comSessao);
    },
    // Envia mensagem/plano para a auditoria.
    // corpo: { texto, json_anexo, historico, session_id }
    enviar: function(corpo){
      return postAutenticado('/api/enviar', corpo);
    },
    // Registra o feedback (👍/👎) de uma resposta.
    // corpo: { voto: 'like'|'dislike', session_id, msg_id, comentario,
    //          ferramenta: 'auditor'|'planner' (ausente = auditor) }
    feedback: function(corpo){
      return postAutenticado('/api/feedback', corpo);
    },
    // Busca o plano .json de uma página do Confluence.
    confluence: function(url, nome){
      return postAutenticado('/api/confluence', { url: url, nome: nome || null });
    },
    // Tag Planner: frames de um arquivo do Figma.
    // corpo: { url, ids?: ['1:2', ...], token_figma?: 'figd_...' }
    plannerFigma: function(corpo){
      return postAutenticado('/api/planner/figma', corpo);
    },
    // Tag Planner: análise (modo 'analise', com telas) ou pergunta de
    // acompanhamento (modo 'pergunta', só texto + histórico).
    plannerAnalisar: function(corpo){
      return postAutenticado('/api/planner/analisar', corpo);
    }
  };
})();
