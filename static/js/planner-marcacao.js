/* Tag Planner — marcação visual: caixas sobre a tela, legenda sincronizada,
   PNG anotado e JSON de coordenadas.

   Quem desenha é o navegador, a partir das coordenadas que o modelo devolve
   no bloco `marcacao` (o servidor extrai e normaliza). O modelo mede cada
   caixa em relação à IMAGEM QUE RECEBEU — que pode ser só uma parte de uma
   tela alta, e reduzida. bboxNaTela converte para a TELA INTEIRA (0..1):
     1) figma_ref que existe no inventário da tela -> posição exata da camada
        (vem do arquivo de design, mais confiável que a estimativa visual);
     2) bbox_normalizada; se algum valor > 1,5 e todos <= 100, o modelo
        mandou porcentagem (÷100). Inválida -> bbox_px ÷ tamanho da parte
        enviada. Nada válido -> o elemento fica só na legenda;
     3) parte -> tela: as partes são faixas da largura inteira, então x e
        largura não mudam; y = recorte.y + y·recorte.altura e
        altura = altura·recorte.altura. Tudo limitado a [0,1].

   Funções puras (testáveis no node): bboxNaTela, normalizarClassificacao,
   mapearElementos, montarJson, textoLegenda, compararIds, slug. */
(function(){
  'use strict';
  var TA = window.TA;

  // Cores e traço por classificação (SPEC §7). O texto do rótulo segue o
  // contraste: branco no vermelho e no cinza-escuro; escuro no amarelo e no
  // cinza-claro (branco neles fica abaixo de 4,5:1).
  var CLASSES = {
    OBRIGATORIO:        { cor: '#CC092F', texto: '#FFFFFF', tracejado: false, css: 'pl-c-obr',
                          nome: 'Obrigatório', singular: 'obrigatório', plural: 'obrigatórios' },
    OPCIONAL:           { cor: '#F2B705', texto: '#1E232B', tracejado: false, css: 'pl-c-opc',
                          nome: 'Opcional/diagnóstico', singular: 'opcional', plural: 'opcionais' },
    NAO_TAGUEAR:        { cor: '#8B909C', texto: '#1E232B', tracejado: true, css: 'pl-c-nao',
                          nome: 'Não taguear', singular: 'não taguear', plural: 'não taguear' },
    NECESSITA_CONTEXTO: { cor: '#5A616E', texto: '#FFFFFF', tracejado: true, css: 'pl-c-ctx',
                          nome: 'Necessita contexto', singular: 'a validar', plural: 'a validar' }
  };
  // Por padrão só o que é para taguear aparece; o resto fica sob demanda.
  function visivelPorPadrao(classe){ return classe === 'OBRIGATORIO' || classe === 'OPCIONAL'; }

  var PAD_CAIXA = 4;        // px: a caixa fica um pouco FORA do elemento (não cobre o texto)
  var FONTE_CANVAS = '"Segoe UI", "Helvetica Neue", Arial, sans-serif';

  // ---- Utilitários puros -----------------------------------------------------
  function semAcento(s){
    s = String(s == null ? '' : s);
    if (s.normalize) s = s.normalize('NFD').replace(/[̀-ͯ]/g, '');
    return s;
  }

  function arred(n, casas){
    var f = Math.pow(10, casas == null ? 6 : casas);
    return Math.round(n * f) / f;
  }

  function limitar01(n){ return n < 0 ? 0 : (n > 1 ? 1 : n); }

  function numero(v){
    if (typeof v === 'number') return v;
    if (typeof v === 'string' && v.trim() !== '') return parseFloat(v.replace(',', '.'));
    return NaN;
  }

  function primeiro(obj, chaves){
    for (var i = 0; i < chaves.length; i++){
      if (obj[chaves[i]] !== undefined && obj[chaves[i]] !== null) return obj[chaves[i]];
    }
    return undefined;
  }

  // {x, y, largura, altura} com 4 números >= 0 (aceita os apelidos
  // width/height/w/h por tolerância), ou null.
  function lerCaixa(b){
    if (!b || typeof b !== 'object') return null;
    var c = {
      x: numero(b.x),
      y: numero(b.y),
      largura: numero(primeiro(b, ['largura', 'width', 'w'])),
      altura: numero(primeiro(b, ['altura', 'height', 'h']))
    };
    var v = [c.x, c.y, c.largura, c.altura];
    for (var i = 0; i < v.length; i++){
      if (!(isFinite(v[i]) && v[i] >= 0)) return null;
    }
    return c;
  }

  // Normalizada válida (0..1), já convertendo porcentagem; null se não der.
  function lerNormalizada(b){
    var c = lerCaixa(b);
    if (!c) return null;
    var fonte = 'normalizada';
    var maior = Math.max(c.x, c.y, c.largura, c.altura);
    if (maior > 1.5){
      if (maior > 100) return null;           // parece pixel, não proporção
      c = { x: c.x / 100, y: c.y / 100, largura: c.largura / 100, altura: c.altura / 100 };
      fonte = 'porcentagem';
    }
    if (!(c.largura > 0 && c.altura > 0)) return null;
    c.fonte = fonte;
    return c;
  }

  function fecharNaTela(b, fonte){
    var x = limitar01(b.x), y = limitar01(b.y);
    var x2 = limitar01(b.x + b.largura), y2 = limitar01(b.y + b.altura);
    if (!(x2 - x > 0 && y2 - y > 0)) return null;
    return { x: arred(x), y: arred(y), largura: arred(x2 - x), altura: arred(y2 - y), fonte: fonte };
  }

  function acharNoInventario(ref, inventario){
    if (!ref || !inventario || !inventario.length) return null;
    var alvo = String(ref).trim().toUpperCase();
    for (var i = 0; i < inventario.length; i++){
      var item = inventario[i];
      if (item && item.ref != null && String(item.ref).trim().toUpperCase() === alvo) return item;
    }
    return null;
  }

  // Caixa do elemento na TELA inteira (0..1) ou null (ver cabeçalho).
  // tela = {largura, altura, partes:[{parte, largura, altura, recorte:{y, altura}}], figma:{inventario}}
  function bboxNaTela(el, tela){
    if (!el || typeof el !== 'object') return null;
    tela = tela || {};

    // 1) camada do Figma
    var item = acharNoInventario(el.figma_ref, tela.figma && tela.figma.inventario);
    if (item){
      var bi = lerCaixa(item.bbox);
      if (bi && bi.largura > 0 && bi.altura > 0){
        var naTela = fecharNaTela(bi, 'figma');
        if (naTela) return naTela;
      }
    }

    // parte em que o elemento foi visto (fora do intervalo: a mais próxima)
    var partes = Array.isArray(tela.partes) ? tela.partes : [];
    var idx = parseInt(el.parte, 10);
    if (!(idx >= 1)) idx = 1;
    if (partes.length && idx > partes.length) idx = partes.length;
    var parte = partes[idx - 1] || null;

    // 2) normalizada (ou porcentagem) -> senão pixels ÷ tamanho da parte
    var b = lerNormalizada(el.bbox_normalizada);
    var fonte = b ? b.fonte : null;
    if (!b){
      var px = lerCaixa(el.bbox_px);
      if (px && px.largura > 0 && px.altura > 0){
        var pw = (parte && parte.largura > 0) ? parte.largura : tela.largura;
        var ph = (parte && parte.altura > 0) ? parte.altura : tela.altura;
        if (pw > 0 && ph > 0){
          b = { x: px.x / pw, y: px.y / ph, largura: px.largura / pw, altura: px.altura / ph };
          fonte = 'px';
        }
      }
    }
    if (!b) return null;

    // 3) da parte para a tela inteira
    var rec = (parte && parte.recorte) || {};
    var ry = numero(rec.y), ra = numero(rec.altura);
    if (!(isFinite(ry) && ry >= 0)) ry = 0;
    if (!(isFinite(ra) && ra > 0)) ra = 1;
    return fecharNaTela({ x: b.x, largura: b.largura, y: ry + b.y * ra, altura: b.altura * ra }, fonte);
  }

  // O servidor já manda OBRIGATORIO|OPCIONAL|NAO_TAGUEAR|NECESSITA_CONTEXTO;
  // isto só tolera variações (acentos, barra, espaços) e, sem classificação,
  // usa a decisão. Desconhecido vira "a validar": melhor esconder do que
  // mandar taguear por engano.
  function normalizarClassificacao(classificacao, decisao){
    var t = semAcento(classificacao).toUpperCase().replace(/[_\s]+/g, ' ').trim();
    if (t){
      if (t.indexOf('OBRIGAT') !== -1) return 'OBRIGATORIO';
      if (t.indexOf('OPCION') !== -1 || t.indexOf('DIAGN') !== -1) return 'OPCIONAL';
      if (t.indexOf('NAO TAGUEAR') !== -1 || t.indexOf('DESCART') !== -1) return 'NAO_TAGUEAR';
      if (t.indexOf('CONTEXTO') !== -1 || t.indexOf('VALIDAR') !== -1) return 'NECESSITA_CONTEXTO';
    }
    var d = semAcento(decisao).toUpperCase().replace(/[_\s]+/g, ' ').trim();
    if (d === 'TAGUEAR') return 'OBRIGATORIO';
    if (d.indexOf('OPCION') !== -1) return 'OPCIONAL';
    if (d.indexOf('NAO TAGUEAR') !== -1) return 'NAO_TAGUEAR';
    return 'NECESSITA_CONTEXTO';
  }

  // Ordem natural dos IDs (T2 antes de T10); empate pela ordem de chegada.
  function compararIds(a, b){
    var ma = /(\d+)/.exec(a.id || ''), mb = /(\d+)/.exec(b.id || '');
    if (ma && mb && Number(ma[1]) !== Number(mb[1])) return Number(ma[1]) - Number(mb[1]);
    if ((a.id || '') !== (b.id || '')) return (a.id || '') < (b.id || '') ? -1 : 1;
    return (a.ordem || 0) - (b.ordem || 0);
  }

  // Elementos agrupados por tela, já com a caixa na tela inteira.
  // Resolve {porTela: [[item]], orfaos: [item]}; item = {id, el, classe, bbox, tela, ordem}.
  // "Órfão" = imagem que não existe nesta análise (só aparece na legenda).
  function mapearElementos(marcacao, telas){
    telas = telas || [];
    var lista = [];
    if (marcacao && Array.isArray(marcacao.elementos)) lista = marcacao.elementos;
    else if (Array.isArray(marcacao)) lista = marcacao;
    var porTela = telas.map(function(){ return []; });
    var orfaos = [];
    lista.forEach(function(el, ordem){
      if (!el || typeof el !== 'object') return;
      var item = {
        id: String(el.id || ('T' + (ordem + 1))).trim(),
        el: el,
        classe: normalizarClassificacao(el.classificacao, el.decisao),
        ordem: ordem,
        bbox: null,
        tela: -1
      };
      var i = parseInt(el.imagem, 10);
      if (!(i >= 1)) i = 1;
      if (i > telas.length){ orfaos.push(item); return; }
      item.tela = i - 1;
      item.bbox = bboxNaTela(el, telas[i - 1]);
      porTela[i - 1].push(item);
    });
    porTela.forEach(function(arr){ arr.sort(compararIds); });
    orfaos.sort(compararIds);
    return { porTela: porTela, orfaos: orfaos };
  }

  function textoLegenda(item){
    var el = item.el || {};
    var leg = String(el.legenda == null ? '' : el.legenda).trim();
    if (leg) return leg;
    var dec = String(el.decisao == null ? '' : el.decisao).trim();
    return item.id + ' — ' + (dec || CLASSES[item.classe].nome.toUpperCase());
  }

  // Legenda sem o "T1 — " do começo (o ID já aparece ao lado, na lista).
  function legendaSemId(item){
    var t = textoLegenda(item);
    var re = new RegExp('^\\s*' + item.id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*[—–:-]\\s*', 'i');
    return t.replace(re, '') || t;
  }

  function slug(s, padrao){
    var t = semAcento(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    t = t.slice(0, 60).replace(/-+$/, '');
    return t || padrao || 'tela';
  }

  // JSON de coordenadas para download (SPEC §7): por tela, com a caixa em
  // pixels da tela guardada e normalizada à tela inteira. Traz todos os
  // elementos (inclusive os descartados): é dado, não desenho.
  function montarJson(registro){
    var telas = (registro && registro.telas) || [];
    var mapa = mapearElementos(registro && registro.marcacao, telas);
    return telas.map(function(tela, i){
      var W = tela.largura, H = tela.altura;
      return {
        tela: i + 1,
        nome: tela.nome || ('Tela ' + (i + 1)),
        largura: W,
        altura: H,
        elementos: mapa.porTela[i].map(function(item){
          var b = item.bbox, el = item.el;
          return {
            id: item.id,
            texto: el.texto == null ? '' : String(el.texto),
            classificacao: item.classe,
            decisao: el.decisao == null ? '' : String(el.decisao),
            legenda: textoLegenda(item),
            bbox_px: b ? { x: Math.round(b.x * W), y: Math.round(b.y * H),
                           largura: Math.round(b.largura * W), altura: Math.round(b.altura * H) } : null,
            bbox_normalizada: b ? { x: arred(b.x, 4), y: arred(b.y, 4),
                                    largura: arred(b.largura, 4), altura: arred(b.altura, 4) } : null
          };
        })
      };
    });
  }

  // ---- Canvas: PNG anotado ---------------------------------------------------
  function carregarImagem(src){
    return new Promise(function(ok, falha){
      var img = new Image();
      img.onload = function(){ ok(img); };
      img.onerror = function(){ falha(new Error('Não foi possível ler a imagem da tela.')); };
      img.src = src;
    });
  }

  // Quebra o texto em linhas que cabem na largura (palavra longa demais é
  // cortada por caractere).
  function quebrarTexto(ctx, texto, largura){
    var palavras = String(texto || '').split(/\s+/);
    var linhas = [], atual = '';
    function empurrarLonga(p){
      var pedaco = '';
      for (var i = 0; i < p.length; i++){
        if (pedaco && ctx.measureText(pedaco + p.charAt(i)).width > largura){ linhas.push(pedaco); pedaco = ''; }
        pedaco += p.charAt(i);
      }
      return pedaco;
    }
    palavras.forEach(function(p){
      if (!p) return;
      var teste = atual ? atual + ' ' + p : p;
      if (ctx.measureText(teste).width <= largura){ atual = teste; return; }
      if (atual) linhas.push(atual);
      atual = ctx.measureText(p).width > largura ? empurrarLonga(p) : p;
    });
    if (atual) linhas.push(atual);
    return linhas.length ? linhas : [''];
  }

  function caminhoArredondado(ctx, x, y, w, h, r){
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.arcTo(x + w, y, x + w, y + r, r);
    ctx.lineTo(x + w, y + h - r);
    ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
    ctx.lineTo(x + r, y + h);
    ctx.arcTo(x, y + h, x, y + h - r, r);
    ctx.lineTo(x, y + r);
    ctx.arcTo(x, y, x + r, y, r);
    ctx.closePath();
  }

  function retanguloCaixa(item, W, H, pad){
    var b = item.bbox;
    var x = b.x * W - pad, y = b.y * H - pad;
    var x1 = b.x * W + b.largura * W + pad, y1 = b.y * H + b.altura * H + pad;
    // o PNG não tem margem: a caixa não pode sair da imagem
    x = Math.max(0, x); y = Math.max(0, y);
    x1 = Math.min(W, x1); y1 = Math.min(H, y1);
    return { x: x, y: y, w: x1 - x, h: y1 - y };
  }

  function desenharCaixa(ctx, item, W, H, borda, pad){
    var cls = CLASSES[item.classe];
    var r = retanguloCaixa(item, W, H, pad);
    var m = borda / 2;     // o traço é centrado no contorno: recua meio traço
    ctx.save();
    ctx.setLineDash(cls.tracejado ? [borda * 3, borda * 2] : []);
    ctx.lineJoin = 'round';
    // halo branco: a caixa aparece em qualquer fundo (vermelho, amarelo, foto)
    ctx.strokeStyle = 'rgba(255,255,255,0.9)';
    ctx.lineWidth = borda + 2;
    ctx.strokeRect(r.x + m, r.y + m, Math.max(1, r.w - borda), Math.max(1, r.h - borda));
    ctx.strokeStyle = cls.cor;
    ctx.lineWidth = borda;
    ctx.strokeRect(r.x + m, r.y + m, Math.max(1, r.w - borda), Math.max(1, r.h - borda));
    ctx.restore();
  }

  function sobrepoe(a, b){
    return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  }

  // Onde fica o rótulo "T1" de uma caixa sem cobrir OUTRA caixa nem outro
  // rótulo (cobriria o texto do botão vizinho): por fora em cima (esquerda,
  // depois direita), por fora embaixo (esquerda, direita). Sem lugar livre,
  // vale a regra simples: em cima; sem espaço, embaixo; sem espaço, dentro.
  // Pura: serve à tela (px do DOM) e ao PNG (px do canvas).
  //   r = caixa {x,y,w,h}; lw/lh = tamanho do rótulo; W/H = área da imagem;
  //   caixas = as DEMAIS caixas; ocupados = rótulos já colocados;
  //   folga = quanto o rótulo passa da borda da caixa (CSS left/right:-3px).
  function posicaoRotulo(r, lw, lh, W, H, caixas, ocupados, folga){
    folga = folga || 0;
    var cand = [
      { lado: 'acima', x: r.x - folga, y: r.y - lh - 2 },
      { lado: 'acima-direita', x: r.x + r.w + folga - lw, y: r.y - lh - 2 },
      { lado: 'abaixo', x: r.x - folga, y: r.y + r.h + 2 },
      { lado: 'abaixo-direita', x: r.x + r.w + folga - lw, y: r.y + r.h + 2 }
    ];
    function cabe(c){
      return c.y >= 0 && c.y + lh <= H && c.x >= -folga - 1 && c.x + lw <= W + folga + 1;
    }
    function livre(c){
      var ret = { x: c.x, y: c.y, w: lw, h: lh };
      for (var i = 0; i < caixas.length; i++){ if (sobrepoe(ret, caixas[i])) return false; }
      for (var j = 0; j < ocupados.length; j++){ if (sobrepoe(ret, ocupados[j])) return false; }
      return true;
    }
    for (var k = 0; k < cand.length; k++){
      if (cabe(cand[k]) && livre(cand[k])) return cand[k];
    }
    if (cabe(cand[0])) return cand[0];
    if (cabe(cand[2])) return cand[2];
    return { lado: 'dentro', x: r.x, y: r.y };
  }

  // Rótulo "T1" por fora da caixa, sem cobrir as vizinhas (posicaoRotulo).
  // rets = retângulos de TODAS as caixas desenhadas; ocupados = rótulos já
  // desenhados (a função acrescenta o deste).
  function desenharRotulo(ctx, item, W, H, pad, fonte, rets, ocupados){
    var cls = CLASSES[item.classe];
    var r = retanguloCaixa(item, W, H, pad);
    var padX = Math.round(fonte * 0.42);
    var lw = Math.ceil(ctx.measureText(item.id).width) + 2 * padX;
    var lh = Math.round(fonte * 1.5);
    var outras = (rets || []).filter(function(o){
      return !(o.x === r.x && o.y === r.y && o.w === r.w && o.h === r.h);
    });
    var pos = posicaoRotulo(r, lw, lh, W, H, outras, ocupados || [], 0);
    var x = Math.min(Math.max(0, pos.x), Math.max(0, W - lw));
    var y = Math.min(Math.max(0, pos.y), Math.max(0, H - lh));
    if (ocupados) ocupados.push({ x: x, y: y, w: lw, h: lh });
    ctx.save();
    ctx.fillStyle = cls.cor;
    caminhoArredondado(ctx, x, y, lw, lh, Math.round(lh * 0.22));
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,0.9)';
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = cls.texto;
    ctx.textBaseline = 'middle';
    ctx.fillText(item.id, x + padX, y + lh / 2 + 1);
    ctx.restore();
  }

  function desenharQuadrado(ctx, x, y, lado, cls){
    ctx.save();
    if (cls.tracejado){
      ctx.fillStyle = '#FFFFFF';
      ctx.fillRect(x, y, lado, lado);
      ctx.setLineDash([Math.max(2, lado / 5), Math.max(2, lado / 7)]);
      ctx.strokeStyle = cls.cor;
      ctx.lineWidth = Math.max(2, Math.round(lado / 6));
      ctx.strokeRect(x + 1, y + 1, lado - 2, lado - 2);
    } else {
      ctx.fillStyle = cls.cor;
      caminhoArredondado(ctx, x, y, lado, lado, Math.round(lado * 0.2));
      ctx.fill();
    }
    ctx.restore();
  }

  function paraBlob(canvas){
    return new Promise(function(ok, falha){
      function porDataUrl(){
        try {
          var url = canvas.toDataURL('image/png');
          var bin = atob(url.slice(url.indexOf(',') + 1));
          var bytes = new Uint8Array(bin.length);
          for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
          ok(new Blob([bytes], { type: 'image/png' }));
        } catch (e){ falha(new Error('O navegador não conseguiu gerar o PNG.')); }
      }
      if (canvas.toBlob){
        canvas.toBlob(function(b){ if (b) ok(b); else porDataUrl(); }, 'image/png');
      } else {
        porDataUrl();
      }
    });
  }

  // PNG anotado de UMA tela: imagem no tamanho guardado + caixas + rótulos +
  // faixa de legenda embaixo (uma linha por elemento, com o quadradinho da
  // cor). Fundo branco. itens = os visíveis no momento (o que se vê é o que
  // se baixa). opcoes: {titulo, rodape}.
  function gerarPng(tela, itens, opcoes){
    opcoes = opcoes || {};
    return carregarImagem(tela.dataUrl).then(function(img){
      var W = img.naturalWidth || tela.largura, H = img.naturalHeight || tela.altura;
      // unidade relativa: 1 = tela de celular com 1080 px de largura
      var u = Math.max(0.5, W / 1080);
      var borda = Math.max(3, Math.round(4 * u));
      var pad = Math.max(4, Math.round(8 * u));
      var fRot = Math.max(13, Math.round(24 * u));
      var fLeg = Math.max(13, Math.round(23 * u));
      var fTit = Math.round(fLeg * 1.18);
      var fRod = Math.max(11, Math.round(fLeg * 0.78));
      var margem = Math.max(14, Math.round(30 * u));
      var lh = Math.round(fLeg * 1.45);
      var lhTit = Math.round(fTit * 1.35);
      var lhRod = Math.round(fRod * 1.45);
      var quad = Math.round(fLeg * 0.9);
      var recuo = quad + Math.round(fLeg * 0.6);
      var gapItem = Math.round(fLeg * 0.4);

      var med = document.createElement('canvas').getContext('2d');
      med.font = '700 ' + fTit + 'px ' + FONTE_CANVAS;
      var linhasTit = quebrarTexto(med, opcoes.titulo || tela.nome || 'Tela', W - 2 * margem);
      med.font = '400 ' + fLeg + 'px ' + FONTE_CANVAS;
      var largTexto = Math.max(40, W - 2 * margem - recuo);
      var blocos = itens.map(function(item){
        var t = textoLegenda(item);
        if (item.el && item.el.texto) t += ' (“' + String(item.el.texto) + '”)';
        if (!item.bbox) t += ' — sem posição na imagem';
        return { item: item, linhas: quebrarTexto(med, t, largTexto) };
      });
      med.font = '400 ' + fRod + 'px ' + FONTE_CANVAS;
      var linhasRod = quebrarTexto(med, opcoes.rodape ||
        'Vermelho: obrigatório · amarelo: opcional/diagnóstico · cinza tracejado: não taguear/a validar', W - 2 * margem);

      var nLinhas = 0;
      blocos.forEach(function(b){ nLinhas += b.linhas.length; });
      var altLeg = margem + linhasTit.length * lhTit + Math.round(fLeg * 0.5) +
        (blocos.length ? nLinhas * lh + (blocos.length - 1) * gapItem : lh) +
        Math.round(fLeg * 0.8) + linhasRod.length * lhRod + margem;

      var canvas = document.createElement('canvas');
      canvas.width = W;
      canvas.height = H + altLeg;
      var ctx = canvas.getContext('2d');
      ctx.fillStyle = '#FFFFFF';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, W, H);

      itens.forEach(function(item){ if (item.bbox) desenharCaixa(ctx, item, W, H, borda, pad); });
      // rótulos depois de todas as caixas: ficam por cima, sem cobrir vizinhas
      ctx.font = '700 ' + fRot + 'px ' + FONTE_CANVAS;
      var rets = itens.filter(function(i){ return i.bbox; })
        .map(function(i){ return retanguloCaixa(i, W, H, pad); });
      var ocupados = [];
      itens.forEach(function(item){
        if (item.bbox) desenharRotulo(ctx, item, W, H, pad, fRot, rets, ocupados);
      });

      // faixa de legenda
      var y = H;
      ctx.fillStyle = '#E9EAF0';
      ctx.fillRect(0, y, W, Math.max(1, Math.round(u * 2)));
      y += margem;
      ctx.textBaseline = 'middle';
      ctx.fillStyle = '#1E232B';
      ctx.font = '700 ' + fTit + 'px ' + FONTE_CANVAS;
      linhasTit.forEach(function(l){ ctx.fillText(l, margem, y + lhTit / 2); y += lhTit; });
      y += Math.round(fLeg * 0.5);
      ctx.font = '400 ' + fLeg + 'px ' + FONTE_CANVAS;
      if (!blocos.length){
        ctx.fillStyle = '#6A7180';
        ctx.fillText('Nenhum elemento marcado nesta tela.', margem, y + lh / 2);
        y += lh;
      }
      blocos.forEach(function(b, k){
        desenharQuadrado(ctx, margem, y + Math.round((lh - quad) / 2), quad, CLASSES[b.item.classe]);
        ctx.fillStyle = '#1E232B';
        b.linhas.forEach(function(l){ ctx.fillText(l, margem + recuo, y + lh / 2); y += lh; });
        if (k < blocos.length - 1) y += gapItem;
      });
      y += Math.round(fLeg * 0.8);
      ctx.fillStyle = '#6A7180';
      ctx.font = '400 ' + fRod + 'px ' + FONTE_CANVAS;
      linhasRod.forEach(function(l){ ctx.fillText(l, margem, y + lhRod / 2); y += lhRod; });

      return paraBlob(canvas).then(function(blob){
        canvas.width = 0; canvas.height = 0;     // devolve a memória já
        return blob;
      });
    });
  }

  // Baixa um Blob com o nome dado (link temporário).
  function baixarBlob(blob, nome){
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = nome;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(function(){
      if (a.parentNode) a.parentNode.removeChild(a);
      URL.revokeObjectURL(url);
    }, 2000);
  }

  // ---- Visualizador (DOM) ----------------------------------------------------
  function mk(tag, classe, texto){
    var e = document.createElement(tag);
    if (classe) e.className = classe;
    if (texto != null) e.textContent = texto;
    return e;
  }

  function pct(n){ return (n * 100).toFixed(4) + '%'; }

  function reduzMovimento(){
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e){ return false; }
  }

  var SVG_BAIXAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12"></path><path d="m7 10 5 5 5-5"></path><path d="M5 21h14"></path></svg>';
  var SVG_AMPLIAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h6v6"></path><path d="M9 21H3v-6"></path><path d="m21 3-7 7"></path><path d="m3 21 7-7"></path></svg>';
  var SVG_AJUSTAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 14h6v6"></path><path d="M20 10h-6V4"></path><path d="m14 10 7-7"></path><path d="m3 21 7-7"></path></svg>';
  var SVG_INFO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 11v5"></path><path d="M12 8h.01"></path></svg>';

  // opcoes: {registro, plataforma}. Devolve {el, atualizar(registro, {nota}),
  // destacar(id), tem(id), destruir()}.
  function criarVisualizador(opcoes){
    var registro = opcoes.registro;
    var plataforma = opcoes.plataforma || 'Tag Studio';
    var est = { aba: 0, todos: false, fixado: null, ampliado: {}, nota: null };
    var mapa = mapearElementos(registro.marcacao, registro.telas);

    var raiz = mk('section', 'pl-mv pl-cartao');
    raiz.setAttribute('aria-labelledby', 'mvTitulo');

    // cabeçalho: título + contagem + downloads
    var cab = mk('div', 'pl-mv-cab');
    var cabTxt = mk('div', 'pl-mv-cab-txt');
    var h2 = mk('h2', null, 'Marcação visual');
    h2.id = 'mvTitulo';
    var resumoEl = mk('p', 'pl-mv-resumo');
    resumoEl.id = 'mvResumo';
    cabTxt.appendChild(h2);
    cabTxt.appendChild(resumoEl);
    var baixar = mk('div', 'pl-mv-baixar');
    var btnPng = mk('button', 'pl-btn-mini');
    btnPng.type = 'button';
    btnPng.id = 'btnBaixarPng';
    btnPng.innerHTML = SVG_BAIXAR + '<span>Baixar PNG marcado</span>';
    var btnJson = mk('button', 'pl-btn-mini');
    btnJson.type = 'button';
    btnJson.id = 'btnBaixarJson';
    btnJson.innerHTML = SVG_BAIXAR + '<span>Baixar coordenadas (JSON)</span>';
    baixar.appendChild(btnPng);
    baixar.appendChild(btnJson);
    cab.appendChild(cabTxt);
    cab.appendChild(baixar);
    raiz.appendChild(cab);

    var avisoEl = mk('div', 'pl-mv-aviso');
    avisoEl.setAttribute('role', 'status');
    raiz.appendChild(avisoEl);

    var abas = mk('div', 'pl-mv-abas');
    abas.setAttribute('role', 'tablist');
    abas.setAttribute('aria-label', 'Telas analisadas');
    raiz.appendChild(abas);

    var painel = mk('div', 'pl-mv-painel');
    painel.id = 'mvPainel';
    painel.setAttribute('role', 'tabpanel');
    raiz.appendChild(painel);

    var ferramentas = mk('div', 'pl-mv-ferramentas');
    var lblTodos = mk('label', 'pl-switch');
    var chkTodos = document.createElement('input');
    chkTodos.type = 'checkbox';
    chkTodos.id = 'mvMostrarTodos';
    var trilho = mk('span', 'pl-switch-trilho');
    trilho.setAttribute('aria-hidden', 'true');
    var txtTodos = mk('span', 'pl-switch-txt');
    lblTodos.appendChild(chkTodos);
    lblTodos.appendChild(trilho);
    lblTodos.appendChild(txtTodos);
    ferramentas.appendChild(lblTodos);
    painel.appendChild(ferramentas);

    var palco = mk('div', 'pl-mv-palco');
    palco.id = 'mvPalco';
    painel.appendChild(palco);

    // Legenda da imagem + zoom logo abaixo do palco (não cobre a tela).
    var rodapePalco = mk('div', 'pl-mv-rodape-palco');
    var legendaTela = mk('span', 'pl-mv-legenda-tela');
    var btnZoom = mk('button', 'pl-btn-mini pl-btn-zoom');
    btnZoom.type = 'button';
    btnZoom.id = 'mvZoom';
    rodapePalco.appendChild(legendaTela);
    rodapePalco.appendChild(btnZoom);
    painel.appendChild(rodapePalco);

    var listaLeg = mk('ul', 'pl-legenda');
    listaLeg.id = 'mvLegenda';
    listaLeg.setAttribute('aria-label', 'Legenda dos elementos marcados');
    painel.appendChild(listaLeg);

    var erroDownload = mk('p', 'pl-msg erro');
    erroDownload.setAttribute('role', 'alert');
    raiz.appendChild(erroDownload);

    var figura = null;

    function visivel(item){ return est.todos || visivelPorPadrao(item.classe); }
    function itensDaAba(){
      var lista = (mapa.porTela[est.aba] || []).slice();
      // órfãos (imagem que não existe) só aparecem na legenda da 1ª tela
      if (est.aba === 0 && mapa.orfaos.length) lista = lista.concat(mapa.orfaos);
      return lista;
    }
    function todosItens(){
      var t = [];
      mapa.porTela.forEach(function(l){ t = t.concat(l); });
      return t.concat(mapa.orfaos);
    }
    function contar(lista){
      var c = { OBRIGATORIO: 0, OPCIONAL: 0, NAO_TAGUEAR: 0, NECESSITA_CONTEXTO: 0 };
      lista.forEach(function(i){ c[i.classe]++; });
      return c;
    }
    function plural(n, cls){
      return n + ' ' + (n === 1 ? CLASSES[cls].singular : CLASSES[cls].plural);
    }

    function renderResumo(){
      var todos = todosItens();
      if (!registro.marcacao || !todos.length){ resumoEl.textContent = ''; return; }
      var c = contar(todos);
      var partes = [plural(c.OBRIGATORIO, 'OBRIGATORIO'), plural(c.OPCIONAL, 'OPCIONAL')];
      var outros = c.NAO_TAGUEAR + c.NECESSITA_CONTEXTO;
      if (outros) partes.push(outros + ' descartado' + (outros === 1 ? '' : 's') + '/a validar');
      resumoEl.textContent = partes.join(' · ');
    }

    function renderAviso(){
      avisoEl.innerHTML = '';
      var textos = [];
      if (!registro.marcacao){
        textos.push('A marcação visual não veio nesta resposta' +
          (registro.marcacaoErro ? ' (' + registro.marcacaoErro + ')' : '') +
          '. O relatório continua válido; para tentar de novo, peça na caixa de perguntas: ' +
          '“Refaça o bloco de coordenadas da marcação”.');
      } else if (!todosItens().length){
        textos.push('O modelo não marcou nenhum elemento nas imagens.');
      }
      if (est.nota) textos.push(est.nota);
      avisoEl.hidden = !textos.length;
      textos.forEach(function(t){
        var p = mk('p', 'pl-mv-aviso-linha');
        p.innerHTML = SVG_INFO;
        p.appendChild(mk('span', null, t));
        avisoEl.appendChild(p);
      });
      var semMarcacao = !registro.marcacao || !todosItens().length;
      btnPng.disabled = false;     // sem caixas ainda dá para baixar a tela com a legenda vazia
      btnJson.disabled = semMarcacao;
    }

    function renderAbas(){
      abas.innerHTML = '';
      var telas = registro.telas || [];
      abas.hidden = telas.length < 2;
      if (telas.length < 2){
        painel.removeAttribute('aria-labelledby');
        return;
      }
      telas.forEach(function(t, i){
        var b = mk('button', 'pl-mv-aba');
        b.type = 'button';
        b.id = 'mvAba' + i;
        b.setAttribute('role', 'tab');
        b.setAttribute('aria-controls', 'mvPainel');
        var ativa = i === est.aba;
        b.setAttribute('aria-selected', ativa ? 'true' : 'false');
        b.tabIndex = ativa ? 0 : -1;
        b.appendChild(mk('span', 'pl-mv-aba-num', String(i + 1)));
        b.appendChild(mk('span', 'pl-mv-aba-nome', t.nome || ('Tela ' + (i + 1))));
        var n = (mapa.porTela[i] || []).filter(visivel).length;
        var cont = mk('span', 'pl-mv-aba-cont', String(n));
        cont.setAttribute('aria-label', n + (n === 1 ? ' elemento marcado' : ' elementos marcados'));
        b.appendChild(cont);
        b.title = (i + 1) + '. ' + (t.nome || 'Tela') + ' — ' + n + (n === 1 ? ' elemento marcado' : ' elementos marcados');
        b.addEventListener('click', function(){ trocarAba(i, false); });
        b.addEventListener('keydown', function(ev){
          var alvo = null;
          if (ev.key === 'ArrowRight') alvo = (i + 1) % telas.length;
          else if (ev.key === 'ArrowLeft') alvo = (i - 1 + telas.length) % telas.length;
          else if (ev.key === 'Home') alvo = 0;
          else if (ev.key === 'End') alvo = telas.length - 1;
          if (alvo === null) return;
          ev.preventDefault();
          trocarAba(alvo, true);
        });
        abas.appendChild(b);
      });
      painel.setAttribute('aria-labelledby', 'mvAba' + est.aba);
    }

    function trocarAba(i, focar){
      if (i === est.aba) return;
      est.aba = i;
      est.fixado = null;
      renderAbas();
      renderPainel();
      if (focar){
        var b = document.getElementById('mvAba' + i);
        if (b) b.focus();
      }
    }

    function ampliadoAtual(){
      var tela = registro.telas[est.aba];
      if (est.ampliado[est.aba] != null) return est.ampliado[est.aba];
      // telas muito altas (página web inteira) ficariam minúsculas ajustadas
      return !!tela && tela.altura / tela.largura > 2.6;
    }

    function renderFerramentas(){
      var tela = registro.telas[est.aba] || {};
      legendaTela.textContent = 'Imagem ' + (est.aba + 1) + ' — ' + (tela.nome || 'Tela') +
        (tela.largura && tela.altura ? ' · ' + tela.largura + ' × ' + tela.altura + ' px' : '');
      var ocultos = todosItens().filter(function(i){ return !visivelPorPadrao(i.classe); }).length;
      lblTodos.hidden = !ocultos;
      ferramentas.hidden = !ocultos;
      chkTodos.checked = est.todos;
      txtTodos.textContent = 'Mostrar descartados/a validar (' + ocultos + ')';
      var amp = ampliadoAtual();
      btnZoom.innerHTML = amp ? SVG_AJUSTAR + '<span>Ajustar</span>' : SVG_AMPLIAR + '<span>Ampliar</span>';
      btnZoom.setAttribute('aria-pressed', amp ? 'true' : 'false');
      btnZoom.title = amp ? 'Ver a tela inteira de uma vez' : 'Ver a tela na largura toda (role para ver o resto)';
    }

    function criarCaixa(item){
      var b = item.bbox;
      var c = mk('div', 'pl-caixa ' + CLASSES[item.classe].css);
      c.setAttribute('data-id', item.id);
      c.setAttribute('role', 'button');
      c.tabIndex = 0;
      var desc = textoLegenda(item) + (item.el.texto ? ' — “' + item.el.texto + '”' : '');
      c.setAttribute('aria-label', desc);
      c.title = desc;
      c.style.left = 'calc(' + pct(b.x) + ' - ' + PAD_CAIXA + 'px)';
      c.style.top = 'calc(' + pct(b.y) + ' - ' + PAD_CAIXA + 'px)';
      c.style.width = 'calc(' + pct(b.largura) + ' + ' + (2 * PAD_CAIXA) + 'px)';
      c.style.height = 'calc(' + pct(b.altura) + ' + ' + (2 * PAD_CAIXA) + 'px)';
      var rot = mk('span', 'pl-caixa-rotulo', item.id);
      rot.setAttribute('aria-hidden', 'true');
      c.appendChild(rot);
      c.addEventListener('mouseenter', function(){ destacar(item.id); });
      c.addEventListener('mouseleave', function(){ soltar(); });
      c.addEventListener('focus', function(){ destacar(item.id); });
      c.addEventListener('blur', function(){ soltar(); });
      c.addEventListener('click', function(){ alternarFixado(item.id, 'legenda'); });
      c.addEventListener('keydown', function(ev){
        if (ev.key === 'Enter' || ev.key === ' '){ ev.preventDefault(); alternarFixado(item.id, 'legenda'); }
      });
      return c;
    }

    // Rótulo por fora da caixa sem cobrir outra caixa nem outro rótulo
    // (mesma regra do PNG: posicaoRotulo). As classes só movem o rótulo.
    var CLASSES_LADO = {
      'acima': [], 'acima-direita': ['pl-rot-direita'], 'abaixo': ['pl-rot-abaixo'],
      'abaixo-direita': ['pl-rot-abaixo', 'pl-rot-direita'], 'dentro': ['pl-rot-dentro']
    };
    function ajustarRotulos(){
      if (!figura) return;
      var largura = figura.clientWidth, altura = figura.clientHeight;
      var caixas = Array.prototype.slice.call(figura.querySelectorAll('.pl-caixa'))
        .filter(function(c){ return c.offsetWidth > 0 && c.offsetHeight > 0; });   // escondidas ficam fora
      var rets = caixas.map(function(c){
        return { x: c.offsetLeft, y: c.offsetTop, w: c.offsetWidth, h: c.offsetHeight };
      });
      var ocupados = [];
      caixas.forEach(function(c, i){
        c.classList.remove('pl-rot-abaixo', 'pl-rot-dentro', 'pl-rot-direita');
        var rot = c.firstChild;
        var lw = (rot && rot.offsetWidth) || 24, lh = (rot && rot.offsetHeight) || 18;
        var outras = rets.filter(function(_, j){ return j !== i; });
        var pos = posicaoRotulo(rets[i], lw, lh, largura, altura, outras, ocupados, 3);
        CLASSES_LADO[pos.lado].forEach(function(cls){ c.classList.add(cls); });
        ocupados.push({ x: pos.x, y: pos.y, w: lw, h: lh });
      });
    }

    // Altura máxima da imagem no modo "ajustar": a tela INTEIRA tem de caber
    // na área visível. Na coluna fixa ao lado do relatório, desconta o resto
    // do cartão (cabeçalho, ferramentas, legenda); empilhado, usa ~72% da janela.
    function alturaDisponivel(){
      var vh = window.innerHeight || document.documentElement.clientHeight || 800;
      var col = raiz.parentNode;
      var estiloCol = col && col.nodeType === 1 ? window.getComputedStyle(col) : null;
      if (!estiloCol || estiloCol.position !== 'sticky') return Math.max(280, Math.round(vh * 0.72));
      var maxCol = parseFloat(estiloCol.maxHeight);
      if (!(maxCol > 0)) maxCol = vh - 100;
      var resto = raiz.offsetHeight - (figura ? figura.offsetHeight : 0);
      // Com legenda longa a imagem não encolhe além de ~42% da janela: a
      // coluna rola um pouco, mas a tela continua legível.
      return Math.max(240, Math.round(vh * 0.42), Math.floor(maxCol - resto - 4));
    }

    function ajustarLayout(){
      if (!figura || !document.body.contains(raiz)) return;
      var img = figura.querySelector('img');
      if (img){
        if (ampliadoAtual()){
          if (img.style.maxHeight !== 'none') img.style.maxHeight = 'none';
        } else {
          var alvo = alturaDisponivel();
          var atual = parseFloat(img.style.maxHeight) || 0;
          // tolerância de 2 px: evita ciclo infinito com o ResizeObserver
          if (Math.abs(atual - alvo) > 2) img.style.maxHeight = alvo + 'px';
        }
        // "Ampliar" só aparece quando muda algo: a tela foi reduzida pela
        // altura (telas largas já ocupam a largura toda)
        if (img.naturalWidth){
          var estilo = window.getComputedStyle(palco);
          var largura = palco.clientWidth - parseFloat(estilo.paddingLeft) - parseFloat(estilo.paddingRight);
          var reduzida = img.offsetWidth < largura - 2 && img.naturalWidth > img.offsetWidth + 1;
          btnZoom.hidden = !ampliadoAtual() && !reduzida;
        }
      }
      ajustarRotulos();
    }

    var quadroPendente = false;
    function agendarAjuste(){
      if (quadroPendente) return;
      quadroPendente = true;
      (window.requestAnimationFrame || setTimeout)(function(){ quadroPendente = false; ajustarLayout(); });
    }

    function renderPalco(){
      palco.innerHTML = '';
      figura = null;
      var tela = registro.telas[est.aba];
      if (!tela) return;
      figura = mk('div', 'pl-mv-figura');
      figura.classList.toggle('pl-ampliada', ampliadoAtual());
      var img = document.createElement('img');
      img.alt = 'Tela ' + (est.aba + 1) + ': ' + (tela.nome || '');
      img.src = tela.dataUrl;
      figura.appendChild(img);
      itensDaAba().forEach(function(item){
        if (item.bbox && item.tela === est.aba && visivel(item)) figura.appendChild(criarCaixa(item));
      });
      palco.appendChild(figura);
      img.addEventListener('load', agendarAjuste);
      agendarAjuste();
      aplicarDestaque();
    }

    function renderLegenda(){
      listaLeg.innerHTML = '';
      var itens = itensDaAba().filter(visivel);
      if (!itens.length){
        var vazio = mk('li', 'pl-legenda-vazia',
          !registro.marcacao ? 'Sem marcação para esta tela.' :
          (est.todos ? 'Nenhum elemento marcado nesta tela.' : 'Nenhum elemento obrigatório ou opcional nesta tela.'));
        listaLeg.appendChild(vazio);
        return;
      }
      itens.forEach(function(item){
        var li = mk('li');
        var b = mk('button', 'pl-leg ' + CLASSES[item.classe].css);
        b.type = 'button';
        b.setAttribute('data-id', item.id);
        b.setAttribute('aria-pressed', est.fixado === item.id ? 'true' : 'false');
        var cor = mk('span', 'pl-leg-cor');
        cor.setAttribute('aria-hidden', 'true');
        cor.title = CLASSES[item.classe].nome;
        var id = mk('span', 'pl-leg-id', item.id);
        var txt = mk('span', 'pl-leg-txt');
        txt.appendChild(document.createTextNode(legendaSemId(item)));
        if (item.el.texto) txt.appendChild(mk('span', 'pl-leg-el', '“' + item.el.texto + '”'));
        if (!item.bbox){
          txt.appendChild(mk('span', 'pl-leg-sem', item.tela < 0
            ? 'imagem ' + (item.el.imagem || '?') + ' não existe nesta análise'
            : 'sem posição na imagem'));
        }
        b.appendChild(cor);
        b.appendChild(id);
        b.appendChild(txt);
        b.addEventListener('mouseenter', function(){ destacar(item.id); });
        b.addEventListener('mouseleave', function(){ soltar(); });
        b.addEventListener('focus', function(){ destacar(item.id); });
        b.addEventListener('blur', function(){ soltar(); });
        b.addEventListener('click', function(){ alternarFixado(item.id, 'caixa'); });
        li.appendChild(b);
        listaLeg.appendChild(li);
      });
    }

    function renderPainel(){
      // destaque fixado num elemento que deixou de aparecer (toggle desligado,
      // outra aba) não pode apagar as caixas que continuam na tela
      if (est.fixado && !itensDaAba().some(function(i){ return i.id === est.fixado && visivel(i); })){
        est.fixado = null;
      }
      renderFerramentas();
      renderPalco();
      renderLegenda();
      agendarAjuste();
    }

    // ---- destaque sincronizado caixa <-> legenda ----
    var realce = null;      // destaque temporário (hover/foco)
    function aplicarDestaque(){
      var alvo = realce || est.fixado;
      // só esmaece as demais se a caixa destacada estiver nesta tela
      var temCaixa = !!alvo && !!figura && !!porId('.pl-caixa', alvo);
      if (figura) figura.classList.toggle('pl-com-destaque', temCaixa);
      var nos = raiz.querySelectorAll('.pl-caixa, .pl-leg');
      for (var i = 0; i < nos.length; i++){
        var id = nos[i].getAttribute('data-id');
        nos[i].classList.toggle('pl-destaque', !!alvo && id === alvo);
        if (nos[i].classList.contains('pl-leg')){
          nos[i].setAttribute('aria-pressed', est.fixado === id ? 'true' : 'false');
        }
      }
    }
    function destacar(id){ realce = id; aplicarDestaque(); }
    function soltar(){ realce = null; aplicarDestaque(); }
    function porId(seletor, id){
      var nos = raiz.querySelectorAll(seletor);
      for (var i = 0; i < nos.length; i++){ if (nos[i].getAttribute('data-id') === id) return nos[i]; }
      return null;
    }
    function rolarAte(no){
      if (!no || !no.scrollIntoView) return;
      try { no.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: reduzMovimento() ? 'auto' : 'smooth' }); }
      catch (e){ no.scrollIntoView(false); }
    }
    function alternarFixado(id, rolarPara){
      est.fixado = (est.fixado === id) ? null : id;
      aplicarDestaque();
      if (!est.fixado) return;
      rolarAte(porId(rolarPara === 'caixa' ? '.pl-caixa' : '.pl-leg', id));
    }

    // ---- eventos fixos ----
    chkTodos.addEventListener('change', function(){
      est.todos = chkTodos.checked;
      renderAbas();
      renderPainel();
    });
    btnZoom.addEventListener('click', function(){
      est.ampliado[est.aba] = !ampliadoAtual();
      renderFerramentas();
      if (figura) figura.classList.toggle('pl-ampliada', ampliadoAtual());
      ajustarLayout();
    });
    btnPng.addEventListener('click', function(){
      var tela = registro.telas[est.aba];
      if (!tela) return;
      var itens = itensDaAba().filter(function(i){ return visivel(i) && i.tela === est.aba; });
      erroDownload.textContent = '';
      btnPng.disabled = true;
      var rotuloOriginal = btnPng.innerHTML;
      btnPng.innerHTML = SVG_BAIXAR + '<span>Gerando…</span>';
      gerarPng(tela, itens, {
        titulo: 'Imagem ' + (est.aba + 1) + ' — ' + (tela.nome || 'Tela'),
        rodape: 'Planner · ' + plataforma + ' — vermelho: obrigatório · amarelo: opcional/diagnóstico' +
          (est.todos ? ' · cinza tracejado: não taguear/a validar' : '')
      }).then(function(blob){
        baixarBlob(blob, slug(tela.nome, 'tela-' + (est.aba + 1)) + '-marcado.png');
      }).catch(function(e){
        erroDownload.textContent = 'Não foi possível gerar o PNG: ' + ((e && e.message) || 'erro desconhecido') + '.';
      }).then(function(){
        btnPng.disabled = false;
        btnPng.innerHTML = rotuloOriginal;
      });
    });
    btnJson.addEventListener('click', function(){
      erroDownload.textContent = '';
      try {
        var json = JSON.stringify(montarJson(registro), null, 2);
        baixarBlob(new Blob([json], { type: 'application/json;charset=utf-8' }),
                   slug(registro.titulo, 'analise') + '-coordenadas.json');
      } catch (e){
        erroDownload.textContent = 'Não foi possível gerar o JSON: ' + ((e && e.message) || 'erro desconhecido') + '.';
      }
    });

    // Reajusta quando o cartão muda de tamanho (entrou na página, a imagem
    // carregou, a legenda cresceu, a coluna mudou de largura) e quando a
    // janela muda de altura (o cartão pode não mudar, mas o espaço sim).
    var observador = null;
    if (window.ResizeObserver){
      observador = new window.ResizeObserver(agendarAjuste);
      observador.observe(raiz);
    }
    window.addEventListener('resize', agendarAjuste);

    function renderTudo(){
      if (est.aba >= (registro.telas || []).length) est.aba = 0;
      renderResumo();
      renderAviso();
      renderAbas();
      renderPainel();
    }
    renderTudo();

    return {
      el: raiz,
      // Nova marcação (pergunta de acompanhamento): substitui a anterior.
      atualizar: function(novo, op){
        registro = novo;
        mapa = mapearElementos(registro.marcacao, registro.telas);
        est.fixado = null;
        est.nota = (op && op.nota) || null;
        renderTudo();
      },
      // Destaca um elemento vindo de fora (ID clicado na tabela do relatório).
      destacar: function(id){
        var alvo = null;
        todosItens().forEach(function(item){ if (!alvo && item.id === id) alvo = item; });
        if (!alvo) return false;
        var mudou = false;
        if (!visivel(alvo)){ est.todos = true; mudou = true; }
        if (alvo.tela >= 0 && alvo.tela !== est.aba){ est.aba = alvo.tela; mudou = true; }
        if (mudou){ renderAbas(); renderPainel(); }
        est.fixado = id;
        aplicarDestaque();
        var caixa = porId('.pl-caixa', id);
        rolarAte(caixa || porId('.pl-leg', id));
        return true;
      },
      tem: function(id){
        return todosItens().some(function(item){ return item.id === id; });
      },
      // Força o ajuste (quem chama acabou de pôr o cartão na página).
      ajustar: agendarAjuste,
      destruir: function(){
        window.removeEventListener('resize', agendarAjuste);
        if (observador) observador.disconnect();
      }
    };
  }

  TA.plannerMarcacao = {
    CLASSES: CLASSES,
    visivelPorPadrao: visivelPorPadrao,
    // puras
    bboxNaTela: bboxNaTela,
    posicaoRotulo: posicaoRotulo,
    normalizarClassificacao: normalizarClassificacao,
    mapearElementos: mapearElementos,
    montarJson: montarJson,
    textoLegenda: textoLegenda,
    legendaSemId: legendaSemId,
    compararIds: compararIds,
    slug: slug,
    // navegador
    gerarPng: gerarPng,
    baixarBlob: baixarBlob,
    criarVisualizador: criarVisualizador
  };
})();
