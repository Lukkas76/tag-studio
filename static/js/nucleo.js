/* Núcleo do front: namespace TA, config injetada e utilitários comuns.
   Carregado primeiro em todas as páginas da plataforma:
     index.html   nucleo -> api -> sessao -> hub -> login
     auditor.html nucleo -> markdown -> plano -> api -> sessao -> tagueamento -> chat -> auditor
     planner.html nucleo -> markdown -> api -> sessao -> planner-* -> planner */
(function(){
  'use strict';

  var TA = window.TA = {
    cfg: window.TAG_CONFIG || {}
  };

  // ---- Sessão salva no navegador (token + dados da conta Google) ----------
  TA.token = function(){
    return localStorage.getItem('tag_auditor_token');
  };
  TA.usuarioSalvo = function(){
    try { return JSON.parse(localStorage.getItem('tag_auditor_user') || 'null'); }
    catch (e) { return null; }
  };
  TA.salvarSessao = function(token, usuario){
    if (token) localStorage.setItem('tag_auditor_token', token);
    localStorage.setItem('tag_auditor_user', JSON.stringify(usuario || {}));
  };
  TA.limparSessao = function(){
    localStorage.removeItem('tag_auditor_token');
    localStorage.removeItem('tag_auditor_user');
  };

  // Copia texto funcionando também em HTTP (navigator.clipboard só existe em
  // HTTPS/localhost; na URL de rede usa o truque do textarea + execCommand).
  TA.copiarTexto = function(texto){
    if (window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText) {
      // Sem o catch, uma recusa (aba sem foco) vira exceção não tratada
      navigator.clipboard.writeText(texto).catch(function(){});
      return true;
    }
    var ta = document.createElement('textarea');
    ta.value = texto;
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(ta);
    return ok;
  };
})();
