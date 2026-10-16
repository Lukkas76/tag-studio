/* Renderização de markdown da resposta da IA (tabelas, listas, código...)
   + filtro visual "linhas sem ajustes" (FILTRAR_SEM_AJUSTES no config.py). */
(function(){
  'use strict';
  var TA = window.TA;
  var FILTRAR_OK_PADRAO = !!TA.cfg.filtrarOk;

  // Aspas também: o texto vem da IA e pode cair dentro de atributo (href).
  function escaparHTML(s){
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
  }
  function inline(s){
    s = s.replace(/`([^`]+)`/g, function(_, c){ return '<code>' + c + '</code>'; });
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return s;
  }
  function linhaTabela(linha){
    var t = linha.trim().replace(/^\|/, '').replace(/\|$/, '');
    return t.split('|').map(function(c){ return c.trim(); });
  }
  function ehSeparadorTabela(linha){
    return /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(linha) && linha.indexOf('-') !== -1;
  }

  var COLUNAS_STATUS = ['status','observa','conformidade','resultado','situa',
                        'problema','ajuste','avalia','diagn','parecer','conclus',
                        'veredi','corre','audit','nota'];
  function temSimboloProblema(s){
    return /[⚠❌❗✖]|🔴|🚫/.test(String(s == null ? '' : s));
  }
  function normCelula(s){
    return String(s == null ? '' : s)
      .replace(/[✅✔✓️]|🟢/g, '')
      .replace(/\*\*/g, '').replace(/`/g, '')
      .trim().toLowerCase();
  }
  function celulaOk(s){
    if (temSimboloProblema(s)) return false;
    var t = normCelula(s);
    if (t === '' || /^[-–—_.]+$/.test(t)) return true;
    if (/^ok\b/.test(t) && t.indexOf('ajustar') === -1) return true;
    if (/^sem (ajuste|problema|inconsist|erro|corre|pend|altera|mudan|observa)/.test(t)) return true;
    if (/^nenhum(a)? (ajuste|problema|inconsist|erro|corre|pend|altera|mudan|observa)/.test(t)) return true;
    if (/^(conforme|correto|corretos|v[aá]lido|aprovado|adequado|em conformidade)\b/.test(t)) return true;
    if (/^(n\/?a|na)$/.test(t)) return true;
    return false;
  }
  function celulaProblema(s){
    if (temSimboloProblema(s)) return true;
    var t = normCelula(s);
    if (!t || celulaOk(s)) return false;
    if (/(inconsist|ajustar|ajuste necess|corrigir|corre[cç][aã]o sugerida|trocar para|inv[aá]lid|incorret|divergen|fora do padr|n[aã]o conforme|aten[cç][aã]o|pend[eê]ncia|ortogr[aá]fic|duplicad|quebra de padr|nomenclatura errada)/.test(t)) return true;
    if (t.indexOf('=') === -1 && /\b(erro|erros|ausente|ausentes|faltando|falta)\b/.test(t)) return true;
    return false;
  }
  function linhaSemAjustes(header, cells){
    var i;
    for (i = 0; i < cells.length; i++){
      if (celulaProblema(cells[i])) return false;
    }
    var idxStatus = [];
    for (i = 0; i < header.length; i++){
      var h = normCelula(header[i]);
      for (var p = 0; p < COLUNAS_STATUS.length; p++){
        if (h.indexOf(COLUNAS_STATUS[p]) !== -1){ idxStatus.push(i); break; }
      }
    }
    if (idxStatus.length){
      for (i = 0; i < idxStatus.length; i++){
        if (!celulaOk(cells[idxStatus[i]])) return false;
      }
      return true;
    }
    for (i = 0; i < cells.length; i++){
      var t = normCelula(cells[i]);
      if (t && !/^[-–—_.]+$/.test(t) && celulaOk(cells[i])) return true;
    }
    return false;
  }

  // opcoes.filtrarOk === false desliga o filtro "linhas sem ajustes" (que só
  // faz sentido nas tabelas da auditoria; no Tag Planner esconderia linhas).
  TA.renderMarkdown = function(texto, opcoes){
    var FILTRAR_OK = (opcoes && opcoes.filtrarOk === false) ? false : FILTRAR_OK_PADRAO;
    if (texto == null) texto = '';
    texto = String(texto)
      .replace(/\\r\\n/g, '\n').replace(/\\n/g, '\n').replace(/\\t/g, '\t')
      .replace(/\r\n/g, '\n');

    var linhas = texto.split('\n');
    var out = [];
    var i = 0;
    var listaAberta = null;
    function fecharLista(){ if (listaAberta){ out.push('</' + listaAberta + '>'); listaAberta = null; } }

    while (i < linhas.length){
      var linha = linhas[i];

      if (/^\s*```/.test(linha)){
        fecharLista();
        i++;
        var buff = [];
        while (i < linhas.length && !/^\s*```/.test(linhas[i])){ buff.push(escaparHTML(linhas[i])); i++; }
        i++;
        out.push('<pre><code>' + buff.join('\n') + '</code></pre>');
        continue;
      }

      var prox = (i + 1 < linhas.length) ? linhas[i + 1] : '';
      var pareceLinha = linha.indexOf('|') !== -1 && /\|/.test(linha.trim());
      if (pareceLinha && (ehSeparadorTabela(prox) || (prox.indexOf('|') !== -1 && linha.trim().charAt(0) === '|'))){
        fecharLista();
        var header = linhaTabela(linha);
        var temSeparador = ehSeparadorTabela(prox);
        i += temSeparador ? 2 : 1;
        var corpo = [];
        while (i < linhas.length && linhas[i].indexOf('|') !== -1 && linhas[i].trim() !== '' && !ehSeparadorTabela(linhas[i])){
          corpo.push(linhaTabela(linhas[i]));
          i++;
        }
        var htmlLinhas = '', ocultas = 0;
        corpo.forEach(function(r){
          var ok = FILTRAR_OK && linhaSemAjustes(header, r);
          if (ok) ocultas++;
          htmlLinhas += ok ? '<tr class="linha-ok">' : '<tr>';
          for (var k = 0; k < header.length; k++){ htmlLinhas += '<td>' + inline(escaparHTML(r[k] != null ? r[k] : '')) + '</td>'; }
          htmlLinhas += '</tr>';
        });
        var html = '<div class="tabela-wrap"><table><thead><tr>';
        header.forEach(function(c){ html += '<th>' + inline(escaparHTML(c)) + '</th>'; });
        html += '</tr></thead><tbody>' + htmlLinhas + '</tbody></table>';
        if (ocultas > 0){
          var restantes = corpo.length - ocultas;
          html += '<div class="filtro-ok"><button type="button" class="btn-toggle-ok" data-ocultas="' + ocultas + '">'
                + (restantes === 0
                    ? 'Todos os ' + ocultas + ' eventos sem ajustes — mostrar'
                    : 'Mostrar ' + ocultas + ' sem ajustes')
                + '</button></div>';
        }
        html += '</div>';
        out.push(html);
        continue;
      }

      var mh = linha.match(/^\s*(#{1,6})\s+(.*)$/);
      if (mh){
        fecharLista();
        var n = mh[1].length;
        out.push('<h' + n + '>' + inline(escaparHTML(mh[2].trim())) + '</h' + n + '>');
        i++; continue;
      }

      if (/^\s*([-*_])\1{2,}\s*$/.test(linha)){
        fecharLista(); out.push('<hr>'); i++; continue;
      }

      if (/^\s*>\s?/.test(linha)){
        fecharLista();
        out.push('<blockquote>' + inline(escaparHTML(linha.replace(/^\s*>\s?/, ''))) + '</blockquote>');
        i++; continue;
      }

      var mu = linha.match(/^\s*[-*+]\s+(.*)$/);
      if (mu){
        if (listaAberta !== 'ul'){ fecharLista(); out.push('<ul>'); listaAberta = 'ul'; }
        out.push('<li>' + inline(escaparHTML(mu[1])) + '</li>');
        i++; continue;
      }
      var mo = linha.match(/^\s*\d+[.)]\s+(.*)$/);
      if (mo){
        if (listaAberta !== 'ol'){ fecharLista(); out.push('<ol>'); listaAberta = 'ol'; }
        out.push('<li>' + inline(escaparHTML(mo[1])) + '</li>');
        i++; continue;
      }

      if (linha.trim() === ''){ fecharLista(); i++; continue; }

      fecharLista();
      var par = [linha];
      i++;
      while (i < linhas.length && linhas[i].trim() !== ''
             && !/^\s*(#{1,6})\s/.test(linhas[i])
             && linhas[i].indexOf('|') === -1
             && !/^\s*[-*+]\s+/.test(linhas[i])
             && !/^\s*\d+[.)]\s+/.test(linhas[i])
             && !/^\s*```/.test(linhas[i])
             && !/^\s*>\s?/.test(linhas[i])){
        par.push(linhas[i]); i++;
      }
      out.push('<p>' + inline(escaparHTML(par.join(' '))) + '</p>');
    }
    fecharLista();
    return out.join('\n');
  };
})();
