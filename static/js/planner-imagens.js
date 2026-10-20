/* Tag Planner — imagens das telas no navegador (canvas, sem bibliotecas).

   Cada tela existe em duas versões:
   1) GUARDADA (é a exibida e a que vai para o histórico): reduzida só o
      necessário para não pesar no navegador — escala = min(1, 1600/w,
      12000/h, raiz(16e6/(w*h))) — e gravada em JPEG 0,92 sobre fundo BRANCO
      (um PNG com transparência viraria preto no JPEG).
   2) ENVIO: refeita a partir da guardada a cada análise. Assim "Tentar de
      novo" e "Duplicar e editar" funcionam só com o que está no histórico.
      O modelo reduz qualquer imagem para caber em 2048 px no maior lado: uma
      página alta inteira chegaria ilegível. Por isso telas altas (altura /
      largura > limiarDivisao e altura > 1600) vão em PARTES — faixas
      verticais da largura inteira, com 8% de sobreposição para nenhum botão
      ficar cortado ao meio — com altura de no máximo largura x proporcaoParte.
   O Azure aceita no máximo 10 imagens por requisição (maxPartes): quando a
   soma das partes passa disso, as telas com mais partes perdem partes (cada
   parte fica mais alta) até caber. Isso nunca bloqueia a pessoa.
   Com identificarImagens, cada imagem enviada ganha no topo uma faixa escura
   "IMAGEM 2 · PARTE 1 DE 3": o modelo lê o número na própria imagem e erra
   menos a numeração da marcação. A altura dela vai em `faixa` (px) e a
   marcação (planner-marcacao.js) desconta a faixa das coordenadas.

   As contas são funções puras (testáveis no node, sem navegador):
   escalaGuardada, partesDesejadas, planejarPartes, distribuirOrcamento,
   tamanhoEnvio e planejarEnvio. O resto usa canvas/Image do navegador. */
(function(){
  'use strict';
  var TA = window.TA;

  // Valores do config.py; o servidor manda os reais em TA.cfg.planner.
  // Estes só valem se alguma chave faltar.
  var PADRAO = {
    figmaConfigurado: false,
    maxTelas: 6,
    maxPartes: 10,
    maxMbImagens: 20,
    ladoMaxEnvio: 2048,
    qualidadeJpeg: 0.85,
    limiarDivisao: 2.4,
    proporcaoParte: 1.8,
    maxPartesPorTela: 6,
    identificarImagens: false,
    // etapa de dúvidas da IA antes da análise (PLANNER_ESCLARECER_* no config.py)
    esclarecer: false,
    maxPerguntas: 3
  };

  // Versão guardada/exibida (ver cabeçalho)
  var GUARDADA_LARGURA_MAX = 1600;
  var GUARDADA_ALTURA_MAX = 12000;
  var GUARDADA_AREA_MAX = 16e6;
  var GUARDADA_QUALIDADE = 0.92;

  // Divisão em partes
  var SOBREPOSICAO = 0.08;          // 8% da altura da parte se repete na seguinte
  var ALTURA_MIN_DIVISAO = 1600;    // até essa altura a tela inteira ainda sai legível

  // Se as imagens passarem de maxMbImagens, tenta uma vez com JPEG mais leve
  // antes de pedir para a pessoa tirar telas.
  var QUALIDADE_REDUZIDA = 0.7;

  // Arquivo de entrada: acima disso o navegador pode travar ao decodificar.
  var MB_MAX_ARQUIVO = 50;

  // image/jpg e image/pjpeg não são oficiais, mas alguns sistemas mandam assim.
  var MIMES_ACEITOS = ['image/png', 'image/jpeg', 'image/jpg', 'image/pjpeg', 'image/webp', 'image/gif'];
  var RE_EXTENSAO = /\.(png|jpe?g|webp|gif)$/i;

  // ---- Configuração --------------------------------------------------------
  function config(){
    var base = (TA && TA.cfg && TA.cfg.planner) || {};
    var c = {};
    for (var k in PADRAO){
      if (Object.prototype.hasOwnProperty.call(PADRAO, k)){
        var v = base[k];
        c[k] = (v !== undefined && v !== null && v !== '') ? v : PADRAO[k];
      }
    }
    return c;
  }

  // Completa uma config parcial (testes passam só o que querem variar).
  function completar(cfg){
    var c = config();
    if (cfg){
      for (var k in cfg){
        if (Object.prototype.hasOwnProperty.call(cfg, k) && cfg[k] !== undefined && cfg[k] !== null){
          c[k] = cfg[k];
        }
      }
    }
    return c;
  }

  function arred(n, casas){
    var f = Math.pow(10, casas == null ? 6 : casas);
    return Math.round(n * f) / f;
  }

  // ---- Contas puras --------------------------------------------------------
  // Escala da versão guardada (nunca amplia).
  function escalaGuardada(w, h){
    if (!(w > 0 && h > 0)) return 1;
    return Math.min(1, GUARDADA_LARGURA_MAX / w, GUARDADA_ALTURA_MAX / h,
                    Math.sqrt(GUARDADA_AREA_MAX / (w * h)));
  }

  // Quantas partes a tela pediria sozinha (sem o orçamento global).
  function partesDesejadas(w, h, cfg){
    var c = completar(cfg);
    if (!(w > 0 && h > 0)) return 1;
    if (!(h / w > c.limiarDivisao && h > ALTURA_MIN_DIVISAO)) return 1;
    var nominal = w * c.proporcaoParte;
    if (!(nominal > 0) || nominal >= h) return 1;
    var passo = nominal * (1 - SOBREPOSICAO);
    // o -1e-9 evita que um erro de arredondamento (2,0000000001) crie uma
    // parte a mais
    var n = Math.ceil((h - nominal) / passo - 1e-9) + 1;
    var teto = Math.max(1, Math.floor(c.maxPartesPorTela) || 1);
    return Math.max(1, Math.min(n, teto));
  }

  // Faixas verticais da tela (px da versão guardada) + recorte normalizado
  // pela altura da tela, que é o que o servidor repassa ao modelo e o que
  // a marcação usa para voltar da parte para a tela inteira.
  // `limite` = teto imposto pelo orçamento global de partes (opcional).
  // A altura da parte é a menor que cobre a tela com n partes e exatamente
  // 8% de sobreposição: sem limite fica <= largura x proporcaoParte (mais
  // legível); com limite cresce o necessário ("aumenta a altura da parte").
  function planejarPartes(w, h, cfg, limite){
    var n = partesDesejadas(w, h, cfg);
    if (limite != null && limite >= 1) n = Math.min(n, Math.floor(limite));
    if (n <= 1) return [{ parte: 1, y: 0, altura: h, recorte: { y: 0, altura: 1 } }];
    var alturaParte = Math.ceil(h / (1 + (n - 1) * (1 - SOBREPOSICAO)));
    var passo = (h - alturaParte) / (n - 1);
    var partes = [];
    for (var i = 0; i < n; i++){
      // a última encosta no fim da tela (sem sobra por arredondamento)
      var y = (i === n - 1) ? h - alturaParte : Math.round(i * passo);
      partes.push({
        parte: i + 1, y: y, altura: alturaParte,
        recorte: { y: arred(y / h), altura: arred(alturaParte / h) }
      });
    }
    return partes;
  }

  // Orçamento global: tira uma parte por vez da tela que tem mais partes até
  // a soma caber em maxPartes (mínimo 1 por tela). Desejada 0 = tela "só
  // camadas" (Figma sem imagem): não manda imagem e fica fora da conta.
  function distribuirOrcamento(desejadas, maxPartes){
    var n = desejadas.map(function(v){ return v === 0 ? 0 : Math.max(1, Math.floor(v) || 1); });
    var soma = 0;
    for (var i = 0; i < n.length; i++) soma += n[i];
    while (soma > maxPartes){
      var iMaior = -1;
      for (var j = 0; j < n.length; j++){
        if (n[j] > 1 && (iMaior < 0 || n[j] > n[iMaior])) iMaior = j;
      }
      if (iMaior < 0) break;      // todas já com 1 parte: não há o que reduzir
      n[iMaior]--;
      soma--;
    }
    return n;
  }

  // Tamanho de uma imagem enviada: cabe em ladoMax no maior lado (nunca amplia).
  function tamanhoEnvio(w, h, ladoMax){
    var e = Math.min(1, ladoMax / Math.max(w, h));
    return { largura: Math.max(1, Math.round(w * e)), altura: Math.max(1, Math.round(h * e)) };
  }

  // Altura (px) da faixa de identificação para uma imagem de `largura` px:
  // legível depois que o modelo reduz a imagem, sem roubar muito da tela.
  function alturaFaixa(largura){
    return Math.max(28, Math.min(72, Math.round(largura * 0.045)));
  }
  // Tela + faixa cabem juntas em ladoMax: se a soma passar, a tela encolhe.
  function tamanhoComFaixa(w, h, ladoMax){
    var tam = tamanhoEnvio(w, h, ladoMax);
    var faixa = alturaFaixa(tam.largura);
    if (tam.altura + faixa > ladoMax){
      tam = tamanhoEnvio(w, h, ladoMax - faixa);
      faixa = alturaFaixa(tam.largura);
    }
    return { largura: tam.largura, altura: tam.altura, faixa: faixa };
  }
  function textoFaixa(indice, parte, total){
    return 'IMAGEM ' + indice + (total > 1 ? ' · PARTE ' + parte + ' DE ' + total : '');
  }

  // Plano completo de uma análise: partes de cada tela já com o orçamento
  // global aplicado. telas = [{largura, altura, semImagem?}] (versão guardada).
  // Tela semImagem (prévia desenhada das camadas do Figma) não vai como
  // imagem: o servidor manda só o inventário de camadas dela ao modelo.
  function planejarEnvio(telas, cfg){
    var c = completar(cfg);
    var desejadas = telas.map(function(t){
      return t.semImagem ? 0 : partesDesejadas(t.largura, t.altura, c);
    });
    var finais = distribuirOrcamento(desejadas, c.maxPartes);
    var planos = telas.map(function(t, i){
      return t.semImagem ? [] : planejarPartes(t.largura, t.altura, c, finais[i]);
    });
    var total = 0, ajustado = false;
    for (var i = 0; i < finais.length; i++){
      total += finais[i];
      if (finais[i] < desejadas[i]) ajustado = true;
    }
    return {
      planos: planos, desejadas: desejadas, partes: finais, total: total,
      ajustado: ajustado, cabe: total <= c.maxPartes
    };
  }

  function formatarMb(mb){
    return (Math.round(mb * 10) / 10).toFixed(1).replace('.', ',');
  }

  // ---- Navegador (Image + canvas) ------------------------------------------
  function carregarImagem(src){
    return new Promise(function(ok, falha){
      var img = new Image();
      img.onload = function(){ ok(img); };
      img.onerror = function(){ falha(new Error('Não foi possível ler a imagem.')); };
      img.src = src;
    });
  }

  function novoCanvas(w, h){
    var canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    var ctx = canvas.getContext('2d');
    // Fundo BRANCO: o JPEG não tem transparência (sem isto, PNG transparente
    // vira preto e o modelo "vê" uma tela escura).
    ctx.fillStyle = '#FFFFFF';
    ctx.fillRect(0, 0, w, h);
    ctx.imageSmoothingEnabled = true;
    if ('imageSmoothingQuality' in ctx) ctx.imageSmoothingQuality = 'high';
    return { canvas: canvas, ctx: ctx };
  }

  // Zerar o canvas devolve a memória na hora (telas grandes pesam dezenas de MB).
  function liberar(canvas){
    canvas.width = 0;
    canvas.height = 0;
  }

  function versaoGuardada(img){
    var w = img.naturalWidth || img.width, h = img.naturalHeight || img.height;
    if (!(w > 0 && h > 0)) throw new Error('A imagem não tem dimensões válidas.');
    var e = escalaGuardada(w, h);
    var cw = Math.max(1, Math.round(w * e)), ch = Math.max(1, Math.round(h * e));
    var cv = novoCanvas(cw, ch);
    cv.ctx.drawImage(img, 0, 0, cw, ch);
    var dataUrl = cv.canvas.toDataURL('image/jpeg', GUARDADA_QUALIDADE);
    liberar(cv.canvas);
    // Sem memória para o canvas, o navegador devolve "data:," em vez de falhar.
    if (dataUrl.indexOf('data:image/jpeg') !== 0){
      throw new Error('O navegador não conseguiu converter a imagem (grande demais?).');
    }
    return { dataUrl: dataUrl, largura: cw, altura: ch, larguraOriginal: w, alturaOriginal: h };
  }

  function arquivoAceito(arquivo){
    if (!arquivo) return false;
    var tipo = String(arquivo.type || '').toLowerCase();
    if (tipo) return MIMES_ACEITOS.indexOf(tipo) !== -1;
    return RE_EXTENSAO.test(arquivo.name || '');
  }

  // Arquivo (upload, arrastar ou colar) -> versão guardada.
  function carregarArquivo(arquivo){
    var nome = (arquivo && arquivo.name) || 'imagem';
    if (!arquivoAceito(arquivo)){
      return Promise.reject(new Error('"' + nome + '" não é uma imagem aceita (PNG, JPG, WEBP ou GIF).'));
    }
    if (arquivo.size > MB_MAX_ARQUIVO * 1024 * 1024){
      return Promise.reject(new Error('"' + nome + '" tem mais de ' + MB_MAX_ARQUIVO +
        ' MB. Reduza a imagem e tente de novo.'));
    }
    var url = URL.createObjectURL(arquivo);
    return carregarImagem(url).then(function(img){
      try { return versaoGuardada(img); }
      finally { URL.revokeObjectURL(url); }
    }, function(){
      URL.revokeObjectURL(url);
      throw new Error('Não foi possível ler "' + nome + '". O arquivo pode estar corrompido.');
    });
  }

  // Frame do Figma (PNG em base64, sem o prefixo data:) -> versão guardada.
  // O inventário de camadas vem normalizado ao frame inteiro, então continua
  // valendo para a versão guardada (mesma proporção, só reduzida).
  function carregarBase64(mime, base64){
    return carregarImagem('data:' + (mime || 'image/png') + ';base64,' + base64)
      .then(versaoGuardada, function(){
        throw new Error('O Figma devolveu uma imagem que o navegador não conseguiu ler.');
      });
  }

  // Frame do Figma cujo PNG a rede/proxy barrou: desenha uma prévia (esqueleto)
  // a partir do inventário de camadas, no tamanho do frame. É só para a pessoa
  // ver a marcação: essa tela NÃO vai como imagem para o modelo (semImagem),
  // que analisa pelos textos e posições exatos das camadas.
  function previaDeCamadas(inventario, largura, altura){
    var w = Math.max(1, Math.round(Number(largura) || 360));
    var h = Math.max(1, Math.round(Number(altura) || 800));
    var e = escalaGuardada(w, h);
    var cw = Math.max(1, Math.round(w * e)), ch = Math.max(1, Math.round(h * e));
    var cv = novoCanvas(cw, ch);
    var ctx = cv.ctx;
    ctx.fillStyle = '#FAFBFD';
    ctx.fillRect(0, 0, cw, ch);
    var unidade = Math.max(1, cw / 400);          // traço/fonte proporcionais à largura
    // Maiores primeiro: cards e fundos ficam por baixo dos botões e textos.
    var itens = (inventario || []).filter(function(it){
      return it && it.bbox && it.bbox.largura > 0 && it.bbox.altura > 0;
    }).sort(function(a, b){
      return (b.bbox.largura * b.bbox.altura) - (a.bbox.largura * a.bbox.altura);
    });
    itens.forEach(function(it){
      var x = it.bbox.x * cw, y = it.bbox.y * ch;
      var bw = it.bbox.largura * cw, bh = it.bbox.altura * ch;
      ctx.fillStyle = it.interativo ? 'rgba(204,9,47,0.07)' : '#F1F2F6';
      ctx.fillRect(x, y, bw, bh);
      ctx.lineWidth = unidade;
      ctx.strokeStyle = it.interativo ? '#E4A0AE' : '#D6D9E2';
      ctx.strokeRect(x + unidade / 2, y + unidade / 2, Math.max(0, bw - unidade), Math.max(0, bh - unidade));
      var texto = String(it.texto || it.nome || '').replace(/\s+/g, ' ').trim();
      if (!texto || bh < 6) return;
      var fonte = Math.max(8, Math.min(bh * 0.55, 13 * unidade));
      ctx.save();
      ctx.beginPath();
      ctx.rect(x, y, bw, bh);
      ctx.clip();
      ctx.fillStyle = it.interativo ? '#A30726' : '#1E232B';
      ctx.font = (it.interativo ? '600 ' : '') + Math.round(fonte) + 'px "Segoe UI", Arial, sans-serif';
      ctx.textBaseline = 'middle';
      ctx.fillText(texto, x + Math.min(6 * unidade, bw * 0.1), y + bh / 2);
      ctx.restore();
    });
    var dataUrl = cv.canvas.toDataURL('image/jpeg', GUARDADA_QUALIDADE);
    liberar(cv.canvas);
    if (dataUrl.indexOf('data:image/jpeg') !== 0){
      return Promise.reject(new Error('O navegador não conseguiu desenhar a prévia da tela.'));
    }
    return Promise.resolve({ dataUrl: dataUrl, largura: cw, altura: ch, larguraOriginal: w,
                             alturaOriginal: h, semImagem: true });
  }

  // indice = número da tela na análise (1..N), para a faixa de identificação
  function gerarPartesTela(tela, partesPlano, c, qualidade, indice){
    if (!partesPlano.length) return Promise.resolve([]);   // tela "só camadas"
    return carregarImagem(tela.dataUrl).then(function(img){
      // Em uso normal a imagem tem exatamente largura x altura da tela; o
      // fator só protege contra um registro antigo com dimensões diferentes.
      var fx = (img.naturalWidth || tela.largura) / tela.largura;
      var fy = (img.naturalHeight || tela.altura) / tela.altura;
      return partesPlano.map(function(p){
        var tam = c.identificarImagens
          ? tamanhoComFaixa(tela.largura, p.altura, c.ladoMaxEnvio)
          : tamanhoEnvio(tela.largura, p.altura, c.ladoMaxEnvio);
        var faixa = tam.faixa || 0;
        var cv = novoCanvas(tam.largura, tam.altura + faixa);
        if (faixa){
          cv.ctx.fillStyle = '#16191F';
          cv.ctx.fillRect(0, 0, tam.largura, faixa);
          cv.ctx.fillStyle = '#FFFFFF';
          cv.ctx.font = '700 ' + Math.round(faixa * 0.5) + 'px "Segoe UI", Arial, sans-serif';
          cv.ctx.textBaseline = 'middle';
          cv.ctx.fillText(textoFaixa(indice, p.parte, partesPlano.length),
                          Math.round(faixa * 0.4), Math.round(faixa / 2), tam.largura - faixa * 0.8);
        }
        cv.ctx.drawImage(img, 0, p.y * fy, tela.largura * fx, p.altura * fy,
                         0, faixa, tam.largura, tam.altura);
        var url = cv.canvas.toDataURL('image/jpeg', qualidade);
        liberar(cv.canvas);
        if (url.indexOf('data:image/jpeg') !== 0){
          throw new Error('O navegador não conseguiu preparar a tela "' + (tela.nome || '') + '".');
        }
        // largura/altura = a imagem enviada inteira (com a faixa): são as
        // dimensões que o modelo vê e em que devolve as coordenadas
        return {
          parte: p.parte, mime: 'image/jpeg', base64: url.slice(url.indexOf(',') + 1),
          largura: tam.largura, altura: tam.altura + faixa, faixa: faixa,
          recorte: { y: p.recorte.y, altura: p.recorte.altura }
        };
      });
    }, function(){
      throw new Error('Não foi possível preparar a imagem da tela "' + (tela.nome || '') + '".');
    });
  }

  // Uma tela por vez: limita o pico de memória com várias telas grandes.
  function gerarTodas(telas, plano, c, qualidade){
    var saida = [], bytes = 0;
    var cadeia = Promise.resolve();
    telas.forEach(function(t, i){
      cadeia = cadeia.then(function(){
        return gerarPartesTela(t, plano.planos[i], c, qualidade, i + 1);
      }).then(function(partes){
        partes.forEach(function(p){ bytes += p.base64.length; });
        saida[i] = { partes: partes };
      });
    });
    return cadeia.then(function(){
      return {
        telas: saida, totalPartes: plano.total, bytes: bytes, mb: bytes / 1048576,
        ajustado: plano.ajustado, qualidade: qualidade
      };
    });
  }

  // Gera as imagens de envio de todas as telas. Resolve com
  //   {telas: [{partes: [{parte, mime, base64, largura, altura, recorte}]}],
  //    totalPartes, bytes, mb, ajustado, qualidade}
  // e rejeita com mensagem clara se passar de maxTelas ou de maxMbImagens.
  function prepararEnvio(telas, cfg){
    var c = completar(cfg);
    if (!telas || !telas.length){
      return Promise.reject(new Error('Adicione pelo menos uma tela para analisar.'));
    }
    if (telas.length > c.maxTelas){
      return Promise.reject(new Error('Cada análise aceita até ' + c.maxTelas + ' telas. Remova ' +
        (telas.length - c.maxTelas) + ' para continuar.'));
    }
    var plano = planejarEnvio(telas, c);
    if (!plano.cabe){
      // Só acontece se maxTelas > maxPartes no config.py (cada tela precisa de 1 imagem).
      return Promise.reject(new Error('O limite é de ' + c.maxPartes + ' imagens por análise e há ' +
        telas.length + ' telas. Remova ' + (telas.length - c.maxPartes) + ' tela(s).'));
    }
    return gerarTodas(telas, plano, c, c.qualidadeJpeg).then(function(r){
      if (r.mb <= c.maxMbImagens) return r;
      var q = Math.min(QUALIDADE_REDUZIDA, c.qualidadeJpeg);
      if (q >= c.qualidadeJpeg) q = c.qualidadeJpeg * 0.8;
      return gerarTodas(telas, plano, c, q).then(function(r2){
        if (r2.mb <= c.maxMbImagens) return r2;
        throw new Error('As imagens somam ' + formatarMb(r2.mb) + ' MB e o limite por análise é de ' +
          c.maxMbImagens + ' MB. Remova uma tela ou use imagens menores.');
      });
    });
  }

  TA.plannerImg = {
    config: config,
    SOBREPOSICAO: SOBREPOSICAO,
    // puras
    escalaGuardada: escalaGuardada,
    partesDesejadas: partesDesejadas,
    planejarPartes: planejarPartes,
    distribuirOrcamento: distribuirOrcamento,
    tamanhoEnvio: tamanhoEnvio,
    tamanhoComFaixa: tamanhoComFaixa,
    textoFaixa: textoFaixa,
    planejarEnvio: planejarEnvio,
    formatarMb: formatarMb,
    // navegador
    arquivoAceito: arquivoAceito,
    carregarImagem: carregarImagem,
    carregarArquivo: carregarArquivo,
    carregarBase64: carregarBase64,
    previaDeCamadas: previaDeCamadas,
    prepararEnvio: prepararEnvio
  };
})();
