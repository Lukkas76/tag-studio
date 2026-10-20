/* Tag Planner — exportar a análise em PDF (sem bibliotecas).

   Monta um documento próprio para impressão em A4 — capa com a pergunta e o
   contexto, resumo da marcação, cada tela marcada (PNG gerado pelo mesmo
   desenho do "Baixar PNG marcado"), o relatório e as perguntas de
   acompanhamento — num iframe escondido e abre o diálogo de impressão do
   navegador, onde a pessoa escolhe "Salvar como PDF". Biblioteca de PDF
   viria de CDN, que a rede do banco pode bloquear; o navegador já sabe fazer.

   Uso: TA.plannerPdf.exportar(registro) -> Promise (resolve quando o diálogo
   abriu). Ordem de carga: depois de markdown.js e planner-marcacao.js. */
(function(){
  'use strict';
  var TA = window.TA;

  function esc(s){
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function dataHora(ms){
    var d = ms ? new Date(ms) : new Date();
    function p(n){ return (n < 10 ? '0' : '') + n; }
    return p(d.getDate()) + '/' + p(d.getMonth() + 1) + '/' + d.getFullYear() + ' às ' +
      p(d.getHours()) + ':' + p(d.getMinutes());
  }
  function blobParaDataUrl(blob){
    return new Promise(function(ok, falha){
      var leitor = new FileReader();
      leitor.onload = function(){ ok(leitor.result); };
      leitor.onerror = function(){ falha(new Error('Não foi possível ler a imagem marcada.')); };
      leitor.readAsDataURL(blob);
    });
  }

  // Telas marcadas em sequência (uma por vez: telas altas pesam no canvas).
  function imagensMarcadas(reg, mapa, plataforma){
    var M = TA.plannerMarcacao;
    var saida = [];
    var cadeia = Promise.resolve();
    (reg.telas || []).forEach(function(tela, i){
      cadeia = cadeia.then(function(){
        var itens = (mapa.porTela[i] || []).filter(function(it){ return M.visivelPorPadrao(it.classe); });
        return M.gerarPng(tela, itens, {
          titulo: 'Imagem ' + (i + 1) + ' — ' + (tela.nome || 'Tela'),
          rodape: 'Planner · ' + plataforma + ' — vermelho: obrigatório · amarelo: opcional/diagnóstico'
        }).then(blobParaDataUrl).then(function(url){
          saida.push({ indice: i + 1, nome: tela.nome || 'Tela', url: url, n: itens.length,
                       semImagem: !!tela.semImagem });
        }, function(){
          saida.push({ indice: i + 1, nome: tela.nome || 'Tela', url: null, n: itens.length });
        });
      });
    });
    return cadeia.then(function(){ return saida; });
  }

  function linhaContexto(rotulo, valor){
    if (!valor) return '';
    return '<tr><th>' + esc(rotulo) + '</th><td>' + esc(valor) + '</td></tr>';
  }

  var CSS = [
    '@page{size:A4;margin:16mm 15mm 18mm;}',
    '*{box-sizing:border-box;}',
    'html,body{margin:0;padding:0;}',
    'body{font-family:"Segoe UI",system-ui,Arial,sans-serif;color:#1E232B;font-size:10.5pt;line-height:1.5;',
    '-webkit-print-color-adjust:exact;print-color-adjust:exact;}',
    '.topo{display:flex;align-items:center;gap:12px;padding-bottom:10px;border-bottom:3px solid #CC092F;}',
    '.topo img{width:40px;height:40px;}',
    '.topo .marca{flex:1;}',
    '.topo .marca b{display:block;font-size:15pt;letter-spacing:-.01em;}',
    '.topo .marca span{font-size:8.5pt;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#CC092F;}',
    '.topo .data{font-size:8.5pt;color:#6A7180;text-align:right;}',
    '.rotulo{margin:18px 0 4px;font-size:8pt;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:#CC092F;}',
    'h1.pergunta{margin:0 0 14px;font-size:18pt;line-height:1.25;letter-spacing:-.01em;}',
    'table.ctx{width:100%;border-collapse:collapse;margin:0 0 12px;font-size:9.5pt;}',
    'table.ctx th{width:32%;text-align:left;font-weight:600;color:#6A7180;padding:5px 10px 5px 0;border-bottom:1px solid #EEF0F4;vertical-align:top;}',
    'table.ctx td{padding:5px 0;border-bottom:1px solid #EEF0F4;}',
    '.resumo{display:flex;gap:10px;margin:12px 0 4px;}',
    '.resumo div{flex:1;border:1px solid #E9EAF0;border-left:4px solid #CC092F;border-radius:8px;padding:8px 12px;}',
    '.resumo div.op{border-left-color:#F2B705;}',
    '.resumo div.ds{border-left-color:#8B909C;}',
    '.resumo b{display:block;font-size:16pt;line-height:1.1;}',
    '.resumo span{font-size:8.5pt;color:#6A7180;}',
    '.leitura{margin:18px 0 0;padding:12px 14px;border:1px solid #E9EAF0;border-radius:8px;background:#FAFBFD;font-size:9.3pt;}',
    '.leitura>b{display:block;margin-bottom:4px;font-size:10pt;}',
    '.leitura ul{margin:0;padding-left:0;list-style:none;}',
    '.leitura li{margin:4px 0;}',
    '.leitura .q{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:7px;vertical-align:-1px;}',
    '.q.ob{background:#CC092F;}.q.op{background:#F2B705;}.q.ds{border:2px dashed #8B909C;width:10px;height:10px;}',
    '.aviso{margin:12px 0;padding:9px 12px;border:1px solid #F2D58A;background:#FFF8E6;border-radius:8px;font-size:9.5pt;color:#6B4A00;}',
    'h2{font-size:13pt;margin:22px 0 10px;padding-bottom:5px;border-bottom:1px solid #E9EAF0;break-after:avoid;}',
    '.secao-quebra{break-before:page;}',
    '.tela{break-inside:avoid;margin:0 0 14px;text-align:center;}',
    '.tela img{max-width:100%;max-height:228mm;border:1px solid #E9EAF0;border-radius:6px;}',
    '.tela .sem{padding:30px;border:1px dashed #C9CEDB;border-radius:8px;color:#6A7180;}',
    '.nota{font-size:8.5pt;color:#6A7180;margin:4px 0 0;}',
    '.md h1,.md h2,.md h3,.md h4{break-after:avoid;}',
    '.md h3{font-size:12pt;margin:18px 0 6px;padding-bottom:3px;border-bottom:1px solid #E9EAF0;}',
    '.md h4{font-size:10.5pt;margin:12px 0 4px;}',
    '.md p{margin:6px 0;}',
    '.md ul,.md ol{margin:6px 0;padding-left:20px;}',
    '.md li{margin:2px 0;}',
    '.md code{background:#FBEEF1;border:1px solid #F3DBE1;border-radius:4px;padding:0 4px;font-family:Consolas,monospace;font-size:9pt;color:#A30726;}',
    '.md pre{background:#FAFBFD;border:1px solid #E9EAF0;border-radius:6px;padding:8px;white-space:pre-wrap;font-size:8.5pt;}',
    '.md pre code{background:none;border:none;color:#1E232B;}',
    '.md table{width:100%;border-collapse:collapse;margin:8px 0;font-size:8.8pt;}',
    '.md thead{display:table-header-group;}',
    '.md tr{break-inside:avoid;}',
    '.md th{background:#CC092F;color:#fff;text-align:left;font-weight:600;padding:5px 7px;border:1px solid #CC092F;}',
    '.md td{padding:5px 7px;border:1px solid #E3E5EC;vertical-align:top;}',
    '.md tbody tr:nth-child(even) td{background:#FAFBFD;}',
    '.md .filtro-ok{display:none;}',
    '.md a{color:#CC092F;}',
    '.pergunta-ac{margin:16px 0 6px;padding:8px 12px;background:#F5F6F9;border-radius:8px;font-weight:600;break-after:avoid;}',
    '.rodape{margin-top:26px;padding-top:8px;border-top:1px solid #E9EAF0;font-size:8pt;color:#8B909C;}'
  ].join('');

  function montarHtml(reg, imagens, mapa, plataforma){
    var c = reg.contexto || {};
    var u = (TA.usuarioSalvo && TA.usuarioSalvo()) || {};
    var origem = location.origin;
    var M = TA.plannerMarcacao;
    var todos = [];
    mapa.porTela.forEach(function(arr){ todos = todos.concat(arr); });
    function conta(classe){ return todos.filter(function(i){ return i.classe === classe; }).length; }
    var obrig = conta('OBRIGATORIO'), opc = conta('OPCIONAL');
    var desc = conta('NAO_TAGUEAR') + conta('NECESSITA_CONTEXTO');

    var msgs = reg.mensagens || [];
    var principal = null, extras = [];
    msgs.forEach(function(m, i){
      if (m.role === 'agente' && !principal){ principal = m; return; }
      if (principal && i > 0) extras.push(m);
    });

    var h = [];
    h.push('<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">');
    h.push('<title>' + esc('Planner - ' + (reg.titulo || 'análise')) + '</title>');
    h.push('<style>' + CSS + '</style></head><body>');
    h.push('<header class="topo"><img src="' + origem + '/static/planner-icone.svg" alt="">' +
      '<div class="marca"><b>Planner</b><span>Relatório de mensuração · ' + esc(plataforma) + '</span></div>' +
      '<div class="data">Análise de ' + esc(dataHora(reg.atualizadoEm || reg.criadoEm)) +
      '<br>Exportado em ' + esc(dataHora()) + '</div></header>');

    h.push('<div class="rotulo">Pergunta de negócio</div>');
    h.push('<h1 class="pergunta">' + esc(c.pergunta || reg.titulo || '') + '</h1>');
    h.push('<table class="ctx">' +
      linhaContexto('Produto', c.produto) + linhaContexto('Jornada', c.jornada) +
      linhaContexto('Nome da tela', c.tela) +
      linhaContexto('Canal', c.canal || 'não informado') +
      linhaContexto('Plataforma', c.plataforma || 'não informada') +
      linhaContexto('Tela anterior', c.tela_anterior) + linhaContexto('Próxima tela', c.tela_seguinte) +
      linhaContexto('Telas analisadas', (reg.telas || []).map(function(t, i){
        return (i + 1) + '. ' + (t.nome || 'Tela') + (t.semImagem ? ' (só camadas do Figma)' : '');
      }).join(' · ')) +
      linhaContexto('Observações', c.observacoes) +
      linhaContexto('Analista', [u.nome, u.email].filter(Boolean).join(' · ')) +
      '</table>');
    // Dúvidas que a IA levantou antes da análise: confirmado x suposto
    var duvidas = (c.esclarecimentos || []).filter(function(e){ return e && e.pergunta; });
    if (duvidas.length){
      h.push('<div class="rotulo">Dúvidas da IA antes da análise</div><table class="ctx">' +
        duvidas.map(function(e){
          return '<tr><th>' + esc(e.pergunta) + '</th><td>' + (e.resposta
            ? '<b>Confirmado:</b> ' + esc(e.resposta)
            : '<b>Premissa:</b> ' + esc(e.premissa || 'sem premissa informada')) + '</td></tr>';
        }).join('') + '</table>');
    }

    h.push('<div class="resumo"><div><b>' + obrig + '</b><span>obrigatórios</span></div>' +
      '<div class="op"><b>' + opc + '</b><span>opcionais/diagnóstico</span></div>' +
      '<div class="ds"><b>' + desc + '</b><span>não taguear/a validar</span></div></div>');
    if (reg.aviso) h.push('<div class="aviso">' + esc(reg.aviso) + '</div>');
    h.push('<div class="leitura"><b>Como ler este relatório</b><ul>' +
      '<li><span class="q ob"></span><b>Obrigatório</b>: evento necessário para responder à pergunta ' +
      '(numerador, denominador ou confirmação do resultado).</li>' +
      '<li><span class="q op"></span><b>Opcional/diagnóstico</b>: ajuda a explicar abandono, erro ou decisão do cliente.</li>' +
      '<li><span class="q ds"></span><b>Não taguear / a validar</b>: sem uso para a pergunta ou dependente de mais contexto ' +
      '(listado no relatório).</li>' +
      '<li>Campos marcados como <b>“a validar”</b> (product, flow, location…) precisam ser confirmados na ' +
      'taxonomia oficial antes de entrar no mapa de tagueamento.</li></ul></div>');

    // Marcação visual
    h.push('<h2 class="secao-quebra">Marcação visual</h2>');
    if (!reg.marcacao){
      h.push('<p class="nota">A análise não trouxe coordenadas para a marcação' +
        (reg.marcacaoErro ? ': ' + esc(reg.marcacaoErro) : '.') + '</p>');
    }
    imagens.forEach(function(img){
      h.push('<div class="tela">' + (img.url
        ? '<img src="' + img.url + '" alt="' + esc('Imagem ' + img.indice + ' — ' + img.nome) + '">'
        : '<div class="sem">Imagem ' + img.indice + ' — ' + esc(img.nome) + ': não foi possível gerar a imagem.</div>') +
        (img.semImagem ? '<p class="nota">Prévia desenhada a partir das camadas do Figma.</p>' : '') +
        '</div>');
    });
    if (mapa.orfaos.length){
      h.push('<p class="nota">Elementos sem posição nas imagens: ' + mapa.orfaos.map(function(i){
        return esc(M.textoLegenda(i));
      }).join(' · ') + '</p>');
    }

    // Relatório
    if (principal){
      h.push('<h2 class="secao-quebra">Relatório</h2>');
      h.push('<div class="md">' + TA.renderMarkdown(principal.display, { filtrarOk: false }) + '</div>');
    }
    // Perguntas de acompanhamento
    if (extras.length){
      h.push('<h2>Perguntas de acompanhamento</h2>');
      extras.forEach(function(m){
        if (m.role === 'usuario') h.push('<div class="pergunta-ac">' + esc(m.display) + '</div>');
        else if (m.role === 'agente') h.push('<div class="md">' + TA.renderMarkdown(m.display, { filtrarOk: false }) + '</div>');
      });
    }

    h.push('<div class="rodape">Gerado pelo Planner (' + esc(plataforma) + ' · Bradesco IA Gen). ' +
      'Recomendação gerada por IA: valide a taxonomia e o plano com a governança de analytics antes de ' +
      'implementar.</div>');
    h.push('</body></html>');
    return h.join('');
  }

  // Imprime o HTML num iframe escondido. Resolve quando o diálogo abriu.
  function imprimir(html, titulo){
    return new Promise(function(ok, falha){
      var iframe = document.createElement('iframe');
      iframe.setAttribute('aria-hidden', 'true');
      iframe.tabIndex = -1;
      iframe.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden;';
      document.body.appendChild(iframe);
      var doc = iframe.contentWindow.document;
      doc.open();
      doc.write(html);
      doc.close();
      var imgs = Array.prototype.slice.call(doc.images);
      Promise.all(imgs.map(function(img){
        return img.complete ? null : new Promise(function(r){ img.onload = img.onerror = r; });
      })).then(function(){
        // O Chrome sugere o nome do arquivo pelo título da página de cima
        var tituloOriginal = document.title;
        document.title = titulo;
        var limpar = function(){
          document.title = tituloOriginal;
          setTimeout(function(){ if (iframe.parentNode) iframe.parentNode.removeChild(iframe); }, 1000);
        };
        iframe.contentWindow.addEventListener('afterprint', limpar);
        try {
          iframe.contentWindow.focus();
          iframe.contentWindow.print();
          ok();
        } catch (e){
          limpar();
          falha(new Error('O navegador não abriu a impressão.'));
        }
        setTimeout(function(){ document.title = tituloOriginal; }, 3000);
      });
    });
  }

  function exportar(reg, plataforma){
    if (!reg) return Promise.reject(new Error('Abra uma análise para exportar.'));
    plataforma = plataforma || (TA.cfg.plataforma && TA.cfg.plataforma.nome) || 'Tag Studio';
    var mapa = TA.plannerMarcacao.mapearElementos(reg.marcacao, reg.telas || []);
    return imagensMarcadas(reg, mapa, plataforma).then(function(imagens){
      var titulo = 'Planner - ' + String(reg.titulo || 'análise').replace(/[\\/:*?"<>|]+/g, ' ').slice(0, 80);
      return imprimir(montarHtml(reg, imagens, mapa, plataforma), titulo);
    });
  }

  TA.plannerPdf = { exportar: exportar, _montarHtml: montarHtml };
})();
