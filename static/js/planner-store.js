/* Tag Planner — histórico de análises no navegador (IndexedDB).

   Por que IndexedDB e não localStorage (como o chat do auditor): cada
   análise guarda as imagens das telas (centenas de KB a alguns MB) e o
   localStorage acaba nos 5 MB. Banco "tagplanner":
     - store "analises" (keyPath id): o registro completo
       {id, titulo, criadoEm, atualizadoEm, status: 'ok'|'erro'|'pendente',
        contexto, telas:[{nome, origem, dataUrl, largura, altura, partes, figma}],
        mensagens:[...], marcacao, ...}
     - store "resumos" (keyPath id): só {id, titulo, datas, status}, para
       desenhar a lista da sidebar sem carregar as imagens de todas as
       análises a cada abertura da página.
   As duas são gravadas na MESMA transação (nunca ficam fora de sincronia).

   Sem IndexedDB (navegação privada em alguns navegadores, política da
   máquina, cota cheia) tudo continua funcionando em memória: o histórico só
   não sobrevive a um recarregar. Nada aqui lança exceção para quem chama:
   toda falha vira "segue em memória" + aviso opcional (aoFalhar). */
(function(){
  'use strict';
  var TA = window.TA;

  var NOME_DB = 'tagplanner';
  var VERSAO_DB = 1;
  var STORE = 'analises';
  var STORE_RESUMOS = 'resumos';
  // Alguns navegadores deixam o open() pendurado sem responder (perfil
  // bloqueado): depois disso desiste e segue em memória.
  var TEMPO_ABERTURA_MS = 4000;

  var db = null;          // conexão aberta; null = só memória
  var memoria = {};       // id -> registro completo (cache e fallback)
  var resumos = {};       // id -> resumo (lista da sidebar)
  // Apagadas nesta página: uma resposta que chegar depois (pergunta pendente,
  // "Tentar de novo") não pode gravar a análise de volta no histórico.
  var apagados = {};
  var ouvinteFalha = null;
  var jaAvisou = false;

  // Dono = e-mail da conta logada. O navegador pode ser usado por mais de uma
  // conta: cada uma vê só as próprias análises (registro sem dono = antigo,
  // aparece para todos).
  function donoAtual(){
    var u = (TA.usuarioSalvo && TA.usuarioSalvo()) || {};
    return String(u.email || '').trim().toLowerCase();
  }
  function ehDaConta(r){
    return !!r && (!r.dono || r.dono === donoAtual());
  }

  function resumoDe(r){
    return {
      id: r.id,
      dono: r.dono || '',
      titulo: r.titulo || 'Análise sem título',
      criadoEm: r.criadoEm || 0,
      atualizadoEm: r.atualizadoEm || r.criadoEm || 0,
      status: r.status || 'ok'
    };
  }

  function avisarFalha(erro){
    try { if (window.console && console.warn) console.warn('[tag planner] histórico:', erro); } catch (e) {}
    if (jaAvisou || !ouvinteFalha) return;
    jaAvisou = true;
    try { ouvinteFalha(erro); } catch (e) {}
  }

  function abrirDb(){
    return new Promise(function(ok, falha){
      var terminou = false;
      function fim(fn, valor){ if (terminou) return; terminou = true; clearTimeout(relogio); fn(valor); }
      var relogio = setTimeout(function(){ fim(falha, new Error('IndexedDB não respondeu.')); }, TEMPO_ABERTURA_MS);
      var req;
      try {
        if (!window.indexedDB) throw new Error('IndexedDB indisponível neste navegador.');
        req = window.indexedDB.open(NOME_DB, VERSAO_DB);
      } catch (e){ fim(falha, e); return; }
      req.onupgradeneeded = function(){
        try {
          var banco = req.result;
          if (!banco.objectStoreNames.contains(STORE)) banco.createObjectStore(STORE, { keyPath: 'id' });
          if (!banco.objectStoreNames.contains(STORE_RESUMOS)) banco.createObjectStore(STORE_RESUMOS, { keyPath: 'id' });
        } catch (e){ /* a abertura falha logo em seguida e cai no onerror */ }
      };
      req.onsuccess = function(){
        var banco = req.result;
        if (terminou){ try { banco.close(); } catch (e) {} return; }
        fim(ok, banco);
      };
      req.onerror = function(){ fim(falha, req.error || new Error('Falha ao abrir o IndexedDB.')); };
      req.onblocked = function(){ fim(falha, new Error('IndexedDB bloqueado por outra aba.')); };
    });
  }

  function lerResumosDb(){
    return new Promise(function(ok, falha){
      try {
        var tx = db.transaction([STORE_RESUMOS], 'readonly');
        var loja = tx.objectStore(STORE_RESUMOS);
        if (loja.getAll){
          var req = loja.getAll();
          req.onsuccess = function(){ ok(req.result || []); };
          req.onerror = function(){ falha(req.error); };
          return;
        }
        var lista = [];
        var cursor = loja.openCursor();
        cursor.onsuccess = function(){
          var c = cursor.result;
          if (c){ lista.push(c.value); c['continue'](); } else ok(lista);
        };
        cursor.onerror = function(){ falha(cursor.error); };
      } catch (e){ falha(e); }
    });
  }

  // Abre o banco e carrega a lista. Resolve SEMPRE com {persistente: bool}.
  function iniciar(){
    return abrirDb().then(function(conexao){
      db = conexao;
      // Outra aba atualizou a versão do banco: solta a conexão para não travá-la.
      db.onversionchange = function(){ try { db.close(); } catch (e) {} db = null; };
      return lerResumosDb();
    }).catch(function(e){
      if (db){ try { db.close(); } catch (x) {} }
      db = null;
      avisarFalha(e);
      return [];
    }).then(function(lista){
      (lista || []).forEach(function(r){ if (r && r.id) resumos[r.id] = r; });
      return { persistente: !!db };
    });
  }

  // Lista da sidebar (síncrona: os resumos ficam em memória), mais recente
  // primeiro, só com as análises da conta logada.
  function listar(){
    var lista = [];
    for (var id in resumos){
      if (Object.prototype.hasOwnProperty.call(resumos, id) && ehDaConta(resumos[id])) lista.push(resumos[id]);
    }
    return lista.sort(function(a, b){
      return (b.atualizadoEm || 0) - (a.atualizadoEm || 0) || (b.criadoEm || 0) - (a.criadoEm || 0);
    });
  }

  function resumo(id){ return ehDaConta(resumos[id]) ? resumos[id] : null; }

  // Registro completo (do cache ou do banco). Resolve com null se não existir
  // (ou se for de outra conta).
  function obter(id){
    if (memoria[id]) return Promise.resolve(ehDaConta(memoria[id]) ? memoria[id] : null);
    if (!db) return Promise.resolve(null);
    return new Promise(function(ok){
      try {
        var req = db.transaction([STORE], 'readonly').objectStore(STORE).get(id);
        req.onsuccess = function(){
          var r = req.result || null;
          if (r) memoria[id] = r;
          ok(ehDaConta(r) ? r : null);
        };
        req.onerror = function(){ avisarFalha(req.error); ok(null); };
      } catch (e){ avisarFalha(e); ok(null); }
    });
  }

  // Grava (insere ou substitui). Resolve com true se ficou no IndexedDB,
  // false se ficou só em memória.
  function salvar(registro){
    if (!registro || !registro.id || apagados[registro.id]) return Promise.resolve(false);
    if (!registro.dono) registro.dono = donoAtual();
    memoria[registro.id] = registro;
    resumos[registro.id] = resumoDe(registro);
    if (!db) return Promise.resolve(false);
    return new Promise(function(ok){
      var respondeu = false;
      function fim(v){ if (!respondeu){ respondeu = true; ok(v); } }
      try {
        var tx = db.transaction([STORE, STORE_RESUMOS], 'readwrite');
        tx.oncomplete = function(){ fim(true); };
        tx.onerror = function(){ avisarFalha(tx.error); fim(false); };
        tx.onabort = function(){ avisarFalha(tx.error || new Error('Gravação cancelada.')); fim(false); };
        tx.objectStore(STORE).put(registro);
        tx.objectStore(STORE_RESUMOS).put(resumoDe(registro));
      } catch (e){ avisarFalha(e); fim(false); }
    });
  }

  function apagar(id){
    apagados[id] = true;
    delete memoria[id];
    delete resumos[id];
    if (!db) return Promise.resolve(false);
    return new Promise(function(ok){
      try {
        var tx = db.transaction([STORE, STORE_RESUMOS], 'readwrite');
        tx.oncomplete = function(){ ok(true); };
        tx.onerror = tx.onabort = function(){ avisarFalha(tx.error); ok(false); };
        tx.objectStore(STORE)['delete'](id);
        tx.objectStore(STORE_RESUMOS)['delete'](id);
      } catch (e){ avisarFalha(e); ok(false); }
    });
  }

  TA.plannerStore = {
    iniciar: iniciar,
    listar: listar,
    resumo: resumo,
    obter: obter,
    salvar: salvar,
    apagar: apagar,
    foiApagado: function(id){ return !!apagados[id]; },
    persistente: function(){ return !!db; },
    // fn(erro) é chamada UMA vez, na primeira falha (para avisar a pessoa).
    aoFalhar: function(fn){ ouvinteFalha = fn; }
  };
})();
