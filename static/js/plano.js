/* Tratamento do plano .json no navegador, ANTES do envio:
   - limparB64: remove imagens/base64 (não auditáveis, estouram o contexto)
   - enxugarPlano: mantém só eventos/parâmetros/canal (descarta DOM, capturas)
   - dividirPlano: quebra planos gigantes em partes menores que o limite */
(function(){
  'use strict';
  var TA = window.TA;
  var LIMITE_VALOR = TA.cfg.limiteValor || 200;
  var LIMITE_OBS = TA.cfg.limiteObs || 500;

  function pareceB64(s){
    if (typeof s !== 'string' || s.length < 500) return false;
    var ini = s.slice(0, 100);
    if (ini.indexOf('data:') === 0 && ini.indexOf(';base64,') !== -1) return true;
    return /^[A-Za-z0-9+\/=\-_\r\n]+$/.test(s.slice(0, 2000));
  }
  function limparB64(v, stats){
    if (Array.isArray(v)){
      var arr = [];
      for (var i = 0; i < v.length; i++) arr.push(limparB64(v[i], stats));
      return arr;
    }
    if (v && typeof v === 'object'){
      var o = {};
      for (var k in v){
        if (!Object.prototype.hasOwnProperty.call(v, k)) continue;
        var kl = k.toLowerCase();
        if (kl.indexOf('b64') !== -1 || kl.indexOf('base64') !== -1){
          stats.campos++;
          stats.chars += (typeof v[k] === 'string') ? v[k].length : JSON.stringify(v[k]).length;
          continue;
        }
        o[k] = limparB64(v[k], stats);
      }
      return o;
    }
    if (typeof v === 'string' && pareceB64(v)){
      stats.campos++; stats.chars += v.length;
      return '[imagem/base64 removido: ' + v.length + ' caracteres]';
    }
    return v;
  }

  var TOKENS_LIXO = ["screenshot","printscreen","screencapture","thumbnail","evidenc",
    "innerhtml","outerhtml","innertext","outertext","cssselector","queryselector",
    "uniqueselector","xpath","csspath","boundingrect","boundingbox","boundingclientrect",
    "clientrect","computedstyle","classname","classlist","useragent","sessionrecording",
    "base64","dataurl","data_url","timestamp","epoch","createdat","updatedat","capturedat"];
  var CHAVES_LIXO = {};
  ["dom","domtree","dom_tree","domsnapshot","dom_snapshot","domnode","html","rawhtml",
   "raw_html","markup","node","nodes","selector","selectors","css","css_path","xpath",
   "jspath","dompath","styles","style","inlinestyle","classe","classes","rect","rects",
   "geometry","coordinates","coordenadas","coords","coord","bbox","scroll","scrollx",
   "scrolly","viewport","position","posicao","offset","b64","timestamp","timestamps",
   "createdat","created_at","updatedat","updated_at","capturedat","captured_at","epoch",
   "datetime","status_macro","statusmacro","conf_macro"
   ].forEach(function(k){ CHAVES_LIXO[k] = 1; });

  function htmlParaTexto(s){
    var t = String(s).replace(/<[^>]+>/g, ' ')
      .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&apos;/g, "'");
    return t.replace(/\s+/g, ' ').trim();
  }
  function coletaStrings(v, out){
    if (Array.isArray(v)){ for (var i = 0; i < v.length; i++) coletaStrings(v[i], out); }
    else if (v && typeof v === 'object'){ for (var k in v){ if (Object.prototype.hasOwnProperty.call(v, k)) coletaStrings(v[k], out); } }
    else if (typeof v === 'string'){ out.push(v); }
  }
  function colapsaDup(s){
    var p = s.split(' '), n = p.length;
    if (n >= 2 && n % 2 === 0){
      var meio = n / 2, igual = true;
      for (var i = 0; i < meio; i++){ if (p[i] !== p[meio + i]){ igual = false; break; } }
      if (igual) return p.slice(0, meio).join(' ');
    }
    return s;
  }

  function observacoes(item){
    var brutos = []; coletaStrings(item, brutos);
    var texto = htmlParaTexto(brutos.join(' '));
    var segs = [], seen = {};
    texto.split(/observa[cç][õo]es\s*:?\s*/i).forEach(function(p){
      p = colapsaDup(p.replace(/^[\s|]+|[\s|]+$/g, ''));
      if (p && !seen[p]){ seen[p] = 1; segs.push(p); }
    });
    if (!segs.length) return '';
    var junto = segs.join(' | ');
    if (junto.length > LIMITE_OBS) junto = junto.slice(0, LIMITE_OBS) + '...[+' + (junto.length - LIMITE_OBS) + ' caracteres]';
    return junto;
  }
  function normLixo(k){
    return String(k).normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim().toLowerCase();
  }
  function ehLixo(k){
    var n = normLixo(k);
    if (CHAVES_LIXO[n]) return true;
    for (var i = 0; i < TOKENS_LIXO.length; i++){ if (n.indexOf(TOKENS_LIXO[i]) !== -1) return true; }
    return false;
  }
  function capValor(v){
    if (typeof v === 'string' && v.length > LIMITE_VALOR && !/ caracteres\]$/.test(v)){
      return v.slice(0, LIMITE_VALOR) + '...[+' + (v.length - LIMITE_VALOR) + ' caracteres]';
    }
    return v;
  }
  function vazioEnx(r){
    return (r && typeof r === 'object' && !Array.isArray(r) && Object.keys(r).length === 0)
        || (Array.isArray(r) && r.length === 0);
  }
  function _enx(v){
    if (Array.isArray(v)){
      var arr = [];
      for (var i = 0; i < v.length; i++){ var r = _enx(v[i]); if (!vazioEnx(r)) arr.push(r); }
      return arr;
    }
    if (v && typeof v === 'object'){
      var o = {};
      for (var k in v){
        if (!Object.prototype.hasOwnProperty.call(v, k)) continue;
        var nch = normLixo(k);
        if (nch === 'rich_texts' || nch === 'richtexts'){
          var obs = observacoes(v[k]);
          if (obs) o['observacoes'] = obs;
          continue;
        }
        if (ehLixo(k)) continue;
        var r2 = _enx(v[k]);
        if (vazioEnx(r2)) continue;
        o[k] = r2;
      }
      return o;
    }
    return capValor(v);
  }

  function capRec(v){
    if (Array.isArray(v)){ var a = []; for (var i = 0; i < v.length; i++) a.push(capRec(v[i])); return a; }
    if (v && typeof v === 'object'){ var o = {}; for (var k in v){ if (Object.prototype.hasOwnProperty.call(v, k)) o[k] = capRec(v[k]); } return o; }
    return capValor(v);
  }
  function enxugarPlano(obj){
    var r = _enx(obj);
    if (vazioEnx(r) && !vazioEnx(obj)) return { obj: capRec(obj), aplicado: false };
    return { obj: r, aplicado: true };
  }

  function _acharMaiorArray(obj){
    var melhor = null;
    (function visitar(v, caminho){
      if (Array.isArray(v)){
        var tam = JSON.stringify(v).length;
        if (!melhor || tam > melhor.tamanho) melhor = { caminho: caminho.slice(), tamanho: tam };
        for (var i = 0; i < v.length; i++) visitar(v[i], caminho.concat(i));
      } else if (v && typeof v === 'object'){
        for (var k in v){ if (Object.prototype.hasOwnProperty.call(v, k)) visitar(v[k], caminho.concat(k)); }
      }
    })(obj, []);
    return melhor;
  }
  function _pegar(obj, caminho){ var v = obj; for (var i = 0; i < caminho.length; i++) v = v[caminho[i]]; return v; }
  function _definir(obj, caminho, valor){
    var v = obj; for (var i = 0; i < caminho.length - 1; i++) v = v[caminho[i]];
    v[caminho[caminho.length - 1]] = valor;
  }
  function _explodir(obj, limite, prof){
    if (JSON.stringify(obj).length <= limite) return [obj];
    if (prof > 6) return [obj];
    var alvo = _acharMaiorArray(obj);
    if (!alvo) return [obj];
    var itens = _pegar(obj, alvo.caminho);
    if (!Array.isArray(itens) || itens.length === 0) return [obj];

    var raizEhArray = (alvo.caminho.length === 0);
    var molde = null, tamBase, montar;
    if (raizEhArray){
      tamBase = 2;
      montar = function(lote){ return lote; };
    } else {
      molde = JSON.parse(JSON.stringify(obj));
      _definir(molde, alvo.caminho, []);
      tamBase = JSON.stringify(molde).length;
      montar = function(lote){
        var copia = JSON.parse(JSON.stringify(molde));
        _definir(copia, alvo.caminho, lote);
        return copia;
      };
    }

    var partes = [], lote = [], tam = tamBase;
    for (var i = 0; i < itens.length; i++){
      var t = JSON.stringify(itens[i]).length + 1;
      if (tamBase + t > limite){
        if (lote.length){ partes.push(montar(lote)); lote = []; tam = tamBase; }
        var orcamento = Math.max(800, limite - tamBase - 2);
        var subs = _explodir(itens[i], orcamento, prof + 1);
        for (var j = 0; j < subs.length; j++) partes.push(montar([subs[j]]));
      } else {
        if (lote.length && tam + t > limite){ partes.push(montar(lote)); lote = []; tam = tamBase; }
        lote.push(itens[i]); tam += t;
      }
    }
    if (lote.length) partes.push(montar(lote));
    return partes.length ? partes : [obj];
  }
  function dividirPlano(conteudo, limite){
    var obj;
    try { obj = JSON.parse(conteudo); } catch(e){ return null; }
    var partes = _explodir(obj, limite, 0);
    if (!partes || partes.length < 2) return null;
    return partes.map(function(p){ return JSON.stringify(p); });
  }

  TA.plano = {
    limparB64: limparB64,
    enxugarPlano: enxugarPlano,
    dividirPlano: dividirPlano
  };
})();
