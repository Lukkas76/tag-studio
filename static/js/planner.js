/* Tag Planner — tela /planner: formulário (telas + contexto), análise,
   resultado (marcação visual + relatório + perguntas de acompanhamento) e
   histórico na sidebar.

   Módulos usados: TA.plannerStore (histórico no IndexedDB), TA.plannerImg
   (imagens no canvas) e TA.plannerMarcacao (caixas, PNG e JSON).

   Esta tela NÃO dispara eventos de GA4: não existe mapa homologado para o
   Tag Planner (SPEC §2). O container do GTM só é carregado pelos
   placeholders do HTML, e os IDs daqui não repetem os do chat do auditor
   (btnNovo, btnEnviar...) para nenhum gatilho de lá disparar por engano.

   Uma requisição por vez (estado.ocupado): análise e pergunta de
   acompanhamento esperam a anterior terminar. Enquanto isso dá para navegar
   pelo histórico; a resposta é gravada no registro certo de qualquer jeito. */
(function(){
  'use strict';
  var TA = window.TA;

  var EXEMPLOS_PERGUNTA = [
    'Quantos usuários iniciaram a contratação?',
    'Qual a taxa de conclusão do fluxo?',
    'Onde os usuários abandonam a jornada?',
    'Qual opção os clientes escolhem?'
  ];
  // Etapas mostradas enquanto o modelo trabalha. A resposta chega de uma vez
  // (sem progresso real do servidor): a partir do envio elas avançam pelo
  // tempo e param na última até a resposta chegar.
  var ETAPAS = [
    'Preparando as imagens',
    'Enviando as telas para o modelo',
    'Lendo as telas e os textos visíveis',
    'Interpretando a pergunta de negócio',
    'Definindo o KPI, o numerador e o denominador',
    'Classificando os elementos das telas',
    'Montando o plano mínimo de mensuração',
    'Calculando as coordenadas da marcação',
    'Revisando o relatório'
  ];
  var SEG_POR_ETAPA = 12;
  var SEG_DEMORANDO = 240;            // a partir daqui avisa que está demorando
  var MAX_HIST_ENVIO = 20;            // SPEC §8: até 20 mensagens, mantendo as 2 primeiras
  var LS_SIDEBAR = 'tagplanner_sidebar_aberta';
  var LS_TOKEN_FIGMA = 'tagplanner_figma_token';

  var ROTULO_ORIGEM = { upload: 'Upload', figma: 'Figma', colada: 'Colada' };
  var CAMPOS = {
    pergunta: 'campoPergunta', produto: 'campoProduto', jornada: 'campoJornada', tela: 'campoTela',
    tela_anterior: 'campoTelaAnterior', tela_seguinte: 'campoTelaSeguinte',
    mapa_metricas: 'campoMapa', observacoes: 'campoObs'
  };

  var SVG_SUBIR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 15 6-6 6 6"></path></svg>';
  var SVG_DESCER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"></path></svg>';
  var SVG_REMOVER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>';
  var SVG_LAPIS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"></path><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"></path></svg>';
  var SVG_LIXEIRA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"></path><path d="M8 6V4h8v2"></path><path d="M19 6l-1 14H6L5 6"></path></svg>';
  var SVG_ALERTA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path></svg>';
  var SVG_REPETIR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"></path><path d="M21 3v5h-5"></path></svg>';
  var SVG_COPIAR_DOC = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"></rect><path d="M5 15V5a2 2 0 0 1 2-2h10"></path></svg>';
  var SVG_MAIS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v14"></path><path d="M5 12h14"></path></svg>';
  var SVG_LIKE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10v11H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1zm0 0 5-8a2.2 2.2 0 0 1 3 2.2L14 8h5.2a2 2 0 0 1 2 2.3l-1.3 8a2 2 0 0 1-2 1.7H7z"></path></svg>';
  var SVG_DIS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 14V3h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1zm0 0-5 8a2.2 2.2 0 0 1-3-2.2L10 16H4.8a2 2 0 0 1-2-2.3l1.3-8a2 2 0 0 1 2-1.7H17z"></path></svg>';

  var STORE, IMG, MARC, cfg;

  var rascunho = null;
  var estado = {
    vista: 'form',
    atualId: null,          // análise aberta (resultado/erro/progresso)
    ocupado: false,         // há requisição em andamento (análise ou pergunta)
    preparando: false,      // gerando as imagens de envio (antes de criar o registro)
    processando: 0,         // imagens sendo lidas para o formulário
    andamento: null,        // {id, inicio, envio, etapaMin, resumo}
    relogio: null,
    visualizador: null,
    avisoSaida: false,
    coladas: 0
  };

  // ---- utilitários ----------------------------------------------------------
  function porId(id){ return document.getElementById(id); }
  function mk(tag, classe, texto){
    var e = document.createElement(tag);
    if (classe) e.className = classe;
    if (texto != null) e.textContent = texto;
    return e;
  }
  function gerarId(prefixo){
    return (prefixo || '') + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }
  function copiar(obj){ return obj == null ? obj : JSON.parse(JSON.stringify(obj)); }
  function plural(n, um, varios){ return n + ' ' + (n === 1 ? um : varios); }
  function nomePlataforma(){ return (TA.cfg.plataforma && TA.cfg.plataforma.nome) || 'Tag Studio'; }
  function paraLista(lista){ return Array.prototype.slice.call(lista || []); }
  function reduzMovimento(){
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e){ return false; }
  }
  function doisDigitos(n){ return (n < 10 ? '0' : '') + n; }
  function mmss(seg){ return doisDigitos(Math.floor(seg / 60)) + ':' + doisDigitos(seg % 60); }
  function dataCurta(ms){
    if (!ms) return '';
    var d = new Date(ms), hoje = new Date();
    var hh = doisDigitos(d.getHours()) + ':' + doisDigitos(d.getMinutes());
    if (d.toDateString() === hoje.toDateString()) return 'hoje, ' + hh;
    var ontem = new Date(hoje.getTime() - 86400000);
    if (d.toDateString() === ontem.toDateString()) return 'ontem, ' + hh;
    return doisDigitos(d.getDate()) + '/' + doisDigitos(d.getMonth() + 1) +
      (d.getFullYear() !== hoje.getFullYear() ? '/' + d.getFullYear() : '');
  }
  // Corta em `max` sem partir um emoji ao meio: meio emoji (surrogate solto)
  // não vira UTF-8 e quebraria o envio à IA.
  function cortarTexto(texto, max){
    var t = String(texto == null ? '' : texto);
    if (t.length <= max) return t;
    t = t.slice(0, max);
    return /[\ud800-\udbff]$/.test(t) ? t.slice(0, -1) : t;
  }
  function tituloDe(pergunta){
    var t = String(pergunta || '').replace(/\s+/g, ' ').trim();
    if (!t) return 'Análise sem título';
    return t.length > 60 ? cortarTexto(t, 58).replace(/\s+\S*$/, '') + '…' : t;
  }
  function mensagemErro(e){
    var msg = (e && e.message) || 'Erro desconhecido.';
    if (/failed to fetch|networkerror|load failed|network request failed/i.test(msg)){
      return 'Falha ao falar com o servidor (a janela preta do ' + nomePlataforma() + ' está aberta?). Detalhe: ' + msg;
    }
    return msg;
  }
  function erroDeResposta(res){
    var d = res && res.dados;
    var e = new Error((d && d.erro) || ('Erro HTTP ' + (res && res.status) + '.'));
    e.status = res && res.status;
    return e;
  }

  // ---- rascunho do formulário ------------------------------------------------
  function contextoVazio(){
    return { pergunta: '', produto: '', jornada: '', tela: '', canal: '', plataforma: '',
             tela_anterior: '', tela_seguinte: '', mapa_metricas: '', observacoes: '' };
  }
  // modo: null (nova) | 'duplicar' | 'editar' (refaz um registro com erro).
  function novoRascunho(){
    return { telas: [], modo: null, base: null, origemTitulo: '' };
  }
  function copiaTela(t){
    return {
      uid: gerarId('t'), nome: t.nome, origem: t.origem, dataUrl: t.dataUrl,
      largura: t.largura, altura: t.altura, semImagem: !!t.semImagem,
      figma: t.figma ? copiar(t.figma) : null
    };
  }
  function lerRadio(nome){
    var r = document.querySelector('input[name="' + nome + '"]:checked');
    return r ? r.value : '';
  }
  function marcarRadio(nome, valor){
    var lista = document.querySelectorAll('input[name="' + nome + '"]');
    var achou = false;
    paraLista(lista).forEach(function(r){ r.checked = (r.value === (valor || '')); if (r.checked) achou = true; });
    if (!achou) paraLista(lista).forEach(function(r){ r.checked = (r.value === ''); });
  }
  function lerContexto(){
    var c = contextoVazio();
    for (var k in CAMPOS){
      if (Object.prototype.hasOwnProperty.call(CAMPOS, k)) c[k] = porId(CAMPOS[k]).value.trim();
    }
    c.canal = lerRadio('canal');
    c.plataforma = lerRadio('plataforma');
    return c;
  }
  function preencherContexto(c){
    c = c || contextoVazio();
    for (var k in CAMPOS){
      if (Object.prototype.hasOwnProperty.call(CAMPOS, k)) porId(CAMPOS[k]).value = c[k] || '';
    }
    marcarRadio('canal', c.canal);
    marcarRadio('plataforma', c.plataforma);
  }
  function rascunhoTemConteudo(){
    var c = lerContexto();
    if (rascunho.telas.length) return true;
    for (var k in c){ if (Object.prototype.hasOwnProperty.call(c, k) && c[k]) return true; }
    return false;
  }

  // =====================================================================
  // Sidebar: histórico de análises
  // =====================================================================
  function statusVisual(r){
    if (estado.andamento && estado.andamento.id === r.id) return { classe: 'andamento', texto: 'em andamento' };
    if (r.status === 'erro') return { classe: 'erro', texto: 'falhou' };
    if (r.status === 'pendente') return { classe: 'pendente', texto: 'interrompida' };
    return null;
  }

  function renderSidebar(){
    var lista = porId('listaAnalises');
    lista.innerHTML = '';
    var resumos = STORE.listar();
    if (!resumos.length){
      lista.appendChild(mk('div', 'vazio', 'Nenhuma análise ainda.'));
      renderRecentes();
      return;
    }
    resumos.forEach(function(r){
      var row = mk('div', 'conv' + (r.id === estado.atualId ? ' ativa' : ''));
      row.title = r.titulo + (r.atualizadoEm ? ' — ' + dataCurta(r.atualizadoEm) : '');
      var bt = mk('button', 'titulo');
      bt.type = 'button';
      if (r.id === estado.atualId) bt.setAttribute('aria-current', 'true');
      var st = statusVisual(r);
      if (st){
        var ponto = mk('span', 'pl-conv-st ' + st.classe);
        ponto.setAttribute('aria-hidden', 'true');
        bt.appendChild(ponto);
      }
      bt.appendChild(mk('span', 'pl-conv-txt', r.titulo));
      if (st) bt.appendChild(mk('span', 'pl-sr', ' (' + st.texto + ')'));
      bt.addEventListener('click', function(){ abrirRegistro(r.id); });
      row.appendChild(bt);

      var acoes = mk('span', 'acoes');
      var bRen = mk('button');
      bRen.type = 'button';
      bRen.title = 'Renomear';
      bRen.setAttribute('aria-label', 'Renomear a análise “' + r.titulo + '”');
      bRen.innerHTML = SVG_LAPIS;
      bRen.addEventListener('click', function(ev){ ev.stopPropagation(); renomearRegistro(r.id); });
      var bDel = mk('button');
      bDel.type = 'button';
      bDel.title = 'Apagar';
      bDel.setAttribute('aria-label', 'Apagar a análise “' + r.titulo + '”');
      bDel.innerHTML = SVG_LIXEIRA;
      bDel.addEventListener('click', function(ev){ ev.stopPropagation(); apagarRegistro(r.id); });
      acoes.appendChild(bRen);
      acoes.appendChild(bDel);
      row.appendChild(acoes);
      lista.appendChild(row);
    });
    renderRecentes();
  }

  // Atalho no formulário: as 3 análises mais recentes (a sidebar começa
  // fechada no computador e o histórico ficaria escondido).
  function renderRecentes(){
    var box = porId('recentes');
    if (!box) return;
    var lista = STORE.listar().slice(0, 3);
    box.innerHTML = '';
    box.hidden = !lista.length;
    if (!lista.length) return;
    box.appendChild(mk('span', 'pl-recentes-rotulo', 'Recentes'));
    lista.forEach(function(r){
      var b = mk('button', 'pl-recente');
      b.type = 'button';
      var st = statusVisual(r);
      if (st){
        var ponto = mk('span', 'pl-conv-st ' + st.classe);
        ponto.setAttribute('aria-hidden', 'true');
        b.appendChild(ponto);
      }
      b.appendChild(mk('span', 'pl-recente-tit', r.titulo));
      b.appendChild(mk('small', null, dataCurta(r.atualizadoEm)));
      b.title = r.titulo + (st ? ' (' + st.texto + ')' : '');
      b.addEventListener('click', function(){ abrirRegistro(r.id); });
      box.appendChild(b);
    });
    var ver = mk('button', 'pl-btn-link', 'Ver histórico');
    ver.type = 'button';
    ver.addEventListener('click', abrirSidebar);
    box.appendChild(ver);
  }

  function renomearRegistro(id){
    var r = STORE.resumo(id);
    if (!r) return;
    var novo = prompt('Novo nome da análise:', r.titulo);
    if (novo == null) return;
    novo = novo.replace(/\s+/g, ' ').trim();
    if (!novo) return;
    STORE.obter(id).then(function(reg){
      if (!reg) return;
      reg.titulo = cortarTexto(novo, 80);
      reg.tituloManual = true;
      return STORE.salvar(reg).then(function(){
        renderSidebar();
        if (estado.atualId === id) porId('tituloPagina').textContent = reg.titulo;
      });
    });
  }

  function apagarRegistro(id){
    if (estado.andamento && estado.andamento.id === id){
      notificar('Essa análise ainda está em andamento. Aguarde terminar para apagar.');
      return;
    }
    var r = STORE.resumo(id);
    if (!r) return;
    if (!confirm('Apagar a análise "' + r.titulo + '"? Esta ação não pode ser desfeita.')) return;
    STORE.apagar(id).then(function(){
      if (estado.atualId === id) mostrarForm(false);
      else renderSidebar();
    });
  }

  // ---- abrir/fechar a sidebar (mesmo comportamento do chat) ----
  function salvarSidebar(){
    var aberta = porId('app').classList.contains('sb-aberta');
    porId('btnMenu').setAttribute('aria-expanded', aberta ? 'true' : 'false');
    try { localStorage.setItem(LS_SIDEBAR, aberta ? '1' : '0'); } catch (e){}
  }
  // No celular a sidebar é um painel por cima da tela: não reabre sozinha.
  function carregarSidebar(){
    try { if (!ehMobile() && localStorage.getItem(LS_SIDEBAR) === '1') porId('app').classList.add('sb-aberta'); } catch (e){}
    porId('btnMenu').setAttribute('aria-expanded', porId('app').classList.contains('sb-aberta') ? 'true' : 'false');
  }
  function abrirSidebar(){ porId('app').classList.add('sb-aberta'); salvarSidebar(); }
  function alternarSidebar(){ porId('app').classList.toggle('sb-aberta'); salvarSidebar(); }
  function fecharSidebar(){ porId('app').classList.remove('sb-aberta'); salvarSidebar(); }
  function ehMobile(){
    try { return window.matchMedia('(max-width:760px)').matches; } catch (e){ return false; }
  }
  function fecharSidebarSeMobile(){ if (ehMobile()) fecharSidebar(); }

  // =====================================================================
  // Vistas
  // =====================================================================
  function trocarVista(nome, focar){
    estado.vista = nome;
    ['form', 'progresso', 'erro', 'resultado'].forEach(function(v){
      var el = porId('vista' + v.charAt(0).toUpperCase() + v.slice(1));
      if (el) el.hidden = (v !== nome);
    });
    porId('conteudo').scrollTop = 0;
    if (focar){
      var alvo = document.querySelector('#vista' + nome.charAt(0).toUpperCase() + nome.slice(1) + ' [data-foco]');
      if (alvo){ try { alvo.focus({ preventScroll: true }); } catch (e){ alvo.focus(); } }
    }
  }

  function marcarHash(id){
    try {
      history.replaceState(null, '', id ? '#' + encodeURIComponent(id) : location.pathname + location.search);
    } catch (e){}
  }
  function idDoHash(){
    var h = (location.hash || '').replace(/^#/, '');
    try { h = decodeURIComponent(h); } catch (e){ return null; }
    return /^p[a-z0-9]{4,40}$/i.test(h) ? h : null;
  }

  function destruirVisualizador(){
    if (estado.visualizador){ estado.visualizador.destruir(); estado.visualizador = null; }
  }

  function mostrarForm(focar){
    estado.atualId = null;
    marcarHash(null);
    destruirVisualizador();
    porId('tituloPagina').textContent = 'Tag Planner';
    renderSidebar();
    renderBannerEdicao();
    atualizarEnvio();
    trocarVista('form', focar);
    fecharSidebarSeMobile();
  }

  function abrirRegistro(id){
    STORE.obter(id).then(function(reg){
      if (!reg){
        notificar('Essa análise não foi encontrada no histórico deste navegador.');
        mostrarForm(false);
        return;
      }
      estado.atualId = reg.id;
      marcarHash(reg.id);
      renderSidebar();
      fecharSidebarSeMobile();
      if (estado.andamento && estado.andamento.id === reg.id) mostrarProgresso(reg, true);
      else if (reg.status === 'ok') mostrarResultado(reg, true);
      else mostrarErro(reg, true);
    });
  }

  // ---- avisos rápidos (canto da tela) ----
  function notificar(texto, acao){
    var area = porId('plToasts');
    var t = mk('div', 'pl-toast');
    t.appendChild(mk('span', 'pl-toast-txt', texto));
    var removido = false;
    function remover(){ if (removido) return; removido = true; if (t.parentNode) t.parentNode.removeChild(t); }
    if (acao){
      var b = mk('button', 'pl-toast-acao', acao.rotulo);
      b.type = 'button';
      b.addEventListener('click', function(){ remover(); acao.fn(); });
      t.appendChild(b);
    }
    var x = mk('button', 'pl-toast-fechar');
    x.type = 'button';
    x.setAttribute('aria-label', 'Fechar aviso');
    x.innerHTML = SVG_REMOVER;
    x.addEventListener('click', remover);
    t.appendChild(x);
    area.appendChild(t);
    setTimeout(remover, acao ? 15000 : 7000);
  }

  // ---- aviso ao sair da página com requisição em andamento ----
  function aoSairDaPagina(ev){
    if (TA.saindo) return;          // "Sair" já perguntou (sessao.js)
    var msg = 'Há uma análise em andamento. Se sair agora, ela será interrompida.';
    ev.preventDefault();
    ev.returnValue = msg;
    return msg;
  }
  // Texto do aviso que o "Sair" mostra antes de derrubar a sessão (sessao.js).
  TA.trabalhoPendente = function(){
    return (estado.ocupado || estado.preparando)
      ? 'Há uma análise ou pergunta do Tag Planner em andamento e ela será interrompida.' : '';
  };
  function atualizarAvisoSaida(){
    var precisa = estado.ocupado || estado.preparando;
    if (precisa && !estado.avisoSaida){
      window.addEventListener('beforeunload', aoSairDaPagina);
      estado.avisoSaida = true;
    } else if (!precisa && estado.avisoSaida){
      window.removeEventListener('beforeunload', aoSairDaPagina);
      estado.avisoSaida = false;
    }
  }

  // =====================================================================
  // Formulário: telas
  // =====================================================================
  function mensagem(idEl, texto, tipo){
    var el = porId(idEl);
    el.textContent = texto || '';
    el.className = 'pl-msg' + (texto ? ' ' + (tipo || 'info') : '');
  }

  function trocarAbaTelas(qual, focar){
    var up = porId('abaUpload'), fg = porId('abaFigma');
    var figma = qual === 'figma';
    up.setAttribute('aria-selected', figma ? 'false' : 'true');
    fg.setAttribute('aria-selected', figma ? 'true' : 'false');
    up.tabIndex = figma ? -1 : 0;
    fg.tabIndex = figma ? 0 : -1;
    porId('painelUpload').hidden = figma;
    porId('painelFigma').hidden = !figma;
    if (focar) (figma ? fg : up).focus();
  }

  function vagasTelas(){
    return cfg.maxTelas - rascunho.telas.length - estado.processando;
  }

  function temArquivosArrastados(ev){
    var dt = ev.dataTransfer;
    if (!dt || !dt.types) return false;
    return paraLista(dt.types).indexOf('Files') !== -1;
  }

  function arquivosDaAreaDeTransferencia(cd){
    var arqs = [];
    if (!cd) return arqs;
    paraLista(cd.items).forEach(function(item){
      if (item.kind === 'file' && /^image\//i.test(item.type || '')){
        var f = item.getAsFile();
        if (f) arqs.push(f);
      }
    });
    if (!arqs.length){
      paraLista(cd.files).forEach(function(f){ if (/^image\//i.test(f.type || '')) arqs.push(f); });
    }
    return arqs;
  }

  function nomeDoArquivo(f, origem){
    if (origem === 'colada'){
      estado.coladas++;
      return 'Tela colada ' + estado.coladas;
    }
    var nome = String((f && f.name) || '').replace(/\.[a-z0-9]+$/i, '').trim();
    return nome || ('Tela ' + (rascunho.telas.length + 1));
  }

  // semImagem: frame do Figma cujo PNG foi barrado pela rede; dataUrl é a
  // prévia desenhada das camadas e a tela vai ao modelo só pelo inventário.
  function novaTela(v, origem, nome, figma){
    return {
      uid: gerarId('t'), nome: cortarTexto(nome || 'Tela', 80), origem: origem,
      dataUrl: v.dataUrl, largura: v.largura, altura: v.altura, semImagem: !!v.semImagem,
      figma: figma || null
    };
  }

  // Lê os arquivos (um por vez, para não estourar a memória com vários
  // prints grandes) e acrescenta ao fim da jornada.
  function adicionarArquivos(arquivos, origem){
    if (!arquivos.length || estado.vista !== 'form') return;
    var aceitos = [], recusados = [], avisos = [];
    arquivos.forEach(function(f){ (IMG.arquivoAceito(f) ? aceitos : recusados).push(f); });
    if (recusados.length){
      avisos.push(recusados.map(function(f){ return '“' + (f.name || 'arquivo') + '”'; }).join(', ') +
        (recusados.length === 1 ? ' não é' : ' não são') + ' imagem aceita (PNG, JPG, WEBP ou GIF).');
    }
    var vagas = vagasTelas();
    if (aceitos.length && vagas <= 0){
      avisos.push('Cada análise aceita até ' + cfg.maxTelas + ' telas. Remova uma para adicionar outra.');
      aceitos = [];
    } else if (aceitos.length > vagas){
      avisos.push('Só ' + (vagas === 1 ? 'cabe mais 1 tela' : 'cabem mais ' + vagas + ' telas') +
        ' (máximo de ' + cfg.maxTelas + ' por análise): ' +
        plural(aceitos.length - vagas, 'imagem ficou', 'imagens ficaram') + ' de fora.');
      aceitos = aceitos.slice(0, vagas);
    }
    mensagem('telasMsg', avisos.join(' '), 'erro');
    if (!aceitos.length) return;

    estado.processando += aceitos.length;
    atualizarProcessando();
    var falhas = [];
    var cadeia = Promise.resolve();
    aceitos.forEach(function(f){
      cadeia = cadeia.then(function(){
        return IMG.carregarArquivo(f).then(function(v){
          rascunho.telas.push(novaTela(v, origem, nomeDoArquivo(f, origem), null));
        }, function(e){
          falhas.push(e.message);
        });
      }).then(function(){
        estado.processando--;
        renderTelas();
        atualizarProcessando();
      });
    });
    cadeia.then(function(){
      if (falhas.length) mensagem('telasMsg', avisos.concat(falhas).join(' '), 'erro');
    });
  }

  function atualizarProcessando(){
    var el = porId('dropStatus');
    el.textContent = estado.processando
      ? 'Processando ' + plural(estado.processando, 'imagem', 'imagens') + '…' : '';
    el.hidden = !estado.processando;
    atualizarEnvio();
  }

  function moverTela(i, delta){
    var j = i + delta;
    if (j < 0 || j >= rascunho.telas.length) return;
    var t = rascunho.telas[i];
    rascunho.telas[i] = rascunho.telas[j];
    rascunho.telas[j] = t;
    renderTelas();
    // o foco segue a tela movida (navegação por teclado)
    var li = document.querySelector('#listaTelas [data-uid="' + t.uid + '"]');
    if (li){
      var b = li.querySelector(delta < 0 ? '.pl-mover-cima' : '.pl-mover-baixo');
      if (!b || b.disabled) b = li.querySelector(delta < 0 ? '.pl-mover-baixo' : '.pl-mover-cima');
      if (b && !b.disabled) b.focus();
    }
  }

  function removerTela(i){
    var t = rascunho.telas[i];
    if (!t) return;
    rascunho.telas.splice(i, 1);
    mensagem('telasMsg', '');
    renderTelas();
    var lis = document.querySelectorAll('#listaTelas .pl-tela');
    var alvo = lis[Math.min(i, lis.length - 1)];
    if (alvo) alvo.querySelector('.pl-tela-nome').focus();
    else porId('dropzone').focus();
  }

  function botaoIcone(classe, svg, fn){
    var b = mk('button', 'pl-icone-btn ' + classe);
    b.type = 'button';
    b.innerHTML = svg;
    b.addEventListener('click', fn);
    return b;
  }

  function rotularAcoesTela(li, nome){
    var rotulos = {
      'pl-mover-cima': 'Mover “' + nome + '” para cima',
      'pl-mover-baixo': 'Mover “' + nome + '” para baixo',
      'pl-remover': 'Remover “' + nome + '”'
    };
    for (var classe in rotulos){
      if (!Object.prototype.hasOwnProperty.call(rotulos, classe)) continue;
      var b = li.querySelector('.' + classe);
      if (b){ b.title = rotulos[classe]; b.setAttribute('aria-label', rotulos[classe]); }
    }
  }

  function renderTelas(){
    var ol = porId('listaTelas');
    ol.innerHTML = '';
    var telas = rascunho.telas;
    var plano = telas.length ? IMG.planejarEnvio(telas, cfg) : null;
    telas.forEach(function(t, i){
      var li = mk('li', 'pl-tela');
      li.setAttribute('data-uid', t.uid);

      var num = mk('span', 'pl-tela-num', String(i + 1));
      num.setAttribute('aria-hidden', 'true');
      li.appendChild(num);

      var thumb = mk('div', 'pl-tela-thumb');
      var img = document.createElement('img');
      img.src = t.dataUrl;
      img.alt = '';
      thumb.appendChild(img);
      li.appendChild(thumb);

      var info = mk('div', 'pl-tela-info');
      var lbl = mk('label', 'pl-sr', 'Nome da tela ' + (i + 1));
      lbl.htmlFor = 'nomeTela_' + t.uid;
      var inp = document.createElement('input');
      inp.type = 'text';
      inp.className = 'pl-tela-nome';
      inp.id = 'nomeTela_' + t.uid;
      inp.value = t.nome;
      inp.maxLength = 80;
      inp.autocomplete = 'off';
      inp.addEventListener('input', function(){ t.nome = inp.value; });
      inp.addEventListener('blur', function(){
        var v = inp.value.replace(/\s+/g, ' ').trim();
        if (!v) v = 'Tela ' + (rascunho.telas.indexOf(t) + 1);
        inp.value = t.nome = v;
        rotularAcoesTela(li, v);       // leitores de tela: os botões falam o nome novo
      });
      inp.addEventListener('keydown', function(ev){
        if (ev.key === 'Enter'){ ev.preventDefault(); inp.blur(); }
      });
      info.appendChild(lbl);
      info.appendChild(inp);

      var meta = mk('div', 'pl-tela-meta');
      meta.appendChild(mk('span', 'pl-origem pl-origem-' + t.origem, ROTULO_ORIGEM[t.origem] || t.origem));
      meta.appendChild(mk('span', null, t.largura + ' × ' + t.altura + ' px'));
      if (plano && plano.partes[i] > 1) meta.appendChild(mk('span', 'pl-tela-partes', 'vai em ' + plano.partes[i] + ' partes'));
      if (t.figma && t.figma.inventario && t.figma.inventario.length){
        meta.appendChild(mk('span', 'pl-tela-inv', plural(t.figma.inventario.length, 'camada', 'camadas')));
      }
      if (t.semImagem){
        var so = mk('span', 'pl-tela-semimg', 'só camadas');
        so.title = 'A imagem não pôde ser baixada do Figma: a prévia foi desenhada a partir das ' +
          'camadas e a análise usa os textos e posições delas.';
        meta.appendChild(so);
      }
      info.appendChild(meta);
      li.appendChild(info);

      var acoes = mk('div', 'pl-tela-acoes');
      var cima = botaoIcone('pl-mover-cima', SVG_SUBIR, function(){ moverTela(rascunho.telas.indexOf(t), -1); });
      var baixo = botaoIcone('pl-mover-baixo', SVG_DESCER, function(){ moverTela(rascunho.telas.indexOf(t), 1); });
      var rem = botaoIcone('pl-remover', SVG_REMOVER, function(){ removerTela(rascunho.telas.indexOf(t)); });
      cima.disabled = i === 0;
      baixo.disabled = i === telas.length - 1;
      acoes.appendChild(cima);
      acoes.appendChild(baixo);
      acoes.appendChild(rem);
      li.appendChild(acoes);
      rotularAcoesTela(li, t.nome || ('tela ' + (i + 1)));
      ol.appendChild(li);
    });

    porId('telasVazio').hidden = telas.length > 0;
    var cont = porId('contadorTelas');
    cont.textContent = telas.length + '/' + cfg.maxTelas;
    cont.classList.toggle('cheio', telas.length >= cfg.maxTelas);
    cont.title = telas.length + ' de ' + cfg.maxTelas + ' telas';
    var drop = porId('dropzone');
    var cheio = telas.length >= cfg.maxTelas;
    drop.classList.toggle('pl-desligado', cheio);
    drop.setAttribute('aria-disabled', cheio ? 'true' : 'false');

    var nota = porId('notaPartes');
    if (plano && plano.ajustado){
      nota.hidden = false;
      nota.textContent = 'Para caber no limite de ' + cfg.maxPartes + ' imagens por análise, as telas mais altas ' +
        'vão em menos partes (cada parte fica mais alta).';
    } else {
      nota.hidden = true;
      nota.textContent = '';
    }
    atualizarEnvio();
  }

  // =====================================================================
  // Formulário: Figma
  // =====================================================================
  function lerTokenFigma(){
    try { return localStorage.getItem(LS_TOKEN_FIGMA) || ''; } catch (e){ return ''; }
  }
  function salvarTokenFigma(token){
    try {
      if (token) localStorage.setItem(LS_TOKEN_FIGMA, token);
      else localStorage.removeItem(LS_TOKEN_FIGMA);
    } catch (e){}
    atualizarTokenUi();
  }
  function atualizarTokenUi(){
    var salvo = !!lerTokenFigma();
    porId('btnFigmaEsquecer').hidden = !salvo;
    porId('figmaTokenSalvo').hidden = !salvo;
  }

  function configurarFigma(){
    var area = porId('figmaTokenArea');
    var obrigatorio = !cfg.figmaConfigurado;
    area.classList.toggle('pl-token-obrigatorio', obrigatorio);
    porId('figmaTokenResumo').textContent = obrigatorio
      ? 'Seu token do Figma (necessário)'
      : 'Usar meu token do Figma (opcional)';
    porId('figmaTokenNecessario').hidden = !obrigatorio;
    porId('figmaToken').value = lerTokenFigma();
    if (obrigatorio || lerTokenFigma()) area.open = true;
    atualizarTokenUi();
  }

  // Liga/desliga o estado "buscando" (o botão pode já ter saído da página:
  // a lista de frames fecha ao adicionar; os campos voltam de qualquer jeito).
  function setBuscandoFigma(ligar, botao, texto){
    var b = porId(botao);
    if (b){
      b.disabled = ligar;
      if (ligar){
        b.setAttribute('data-rotulo', b.innerHTML);
        b.innerHTML = '<span class="pl-giro" aria-hidden="true"></span>' + texto;
      } else if (b.getAttribute('data-rotulo')){
        b.innerHTML = b.getAttribute('data-rotulo');
        b.removeAttribute('data-rotulo');
      }
    }
    porId('figmaUrl').disabled = ligar;
    porId('figmaToken').disabled = ligar;
  }

  function corpoFigma(url, ids){
    var corpo = { url: url };
    if (ids && ids.length) corpo.ids = ids;
    var token = porId('figmaToken').value.trim();
    if (token) corpo.token_figma = token;
    return corpo;
  }

  function tratarErroFigma(res){
    var msg = (res.dados && res.dados.erro) || ('Erro HTTP ' + res.status + ' ao buscar no Figma.');
    mensagem('figmaMsg', msg, 'erro');
    // 503 = servidor sem token e a pessoa não informou o dela; 400 de acesso
    // também se resolve com o token pessoal de quem enxerga o arquivo. Cota
    // esgotada (429) cita o token, mas trocar de token não ajuda: não abre.
    if (res.status === 503 || (res.status === 400 && /token/i.test(msg))){
      var area = porId('figmaTokenArea');
      area.open = true;
      porId('figmaToken').focus();
    }
  }

  function buscarFigma(){
    var url = porId('figmaUrl').value.trim();
    var token = porId('figmaToken').value.trim();
    mensagem('figmaMsg', '');
    if (!url){
      mensagem('figmaMsg', 'Cole a URL do arquivo ou do frame do Figma.', 'erro');
      porId('figmaUrl').focus();
      return;
    }
    // Link colado sem "https://" também vale (o servidor completa).
    if (!/^(https?:\/\/)?([\w-]+\.)*figma\.com\//i.test(url)){
      mensagem('figmaMsg', 'A URL precisa ser de um arquivo do Figma (figma.com/design/… ou figma.com/file/…).', 'erro');
      porId('figmaUrl').focus();
      return;
    }
    if (!cfg.figmaConfigurado && !token){
      mensagem('figmaMsg', 'Informe seu token do Figma: o servidor não tem um configurado.', 'erro');
      porId('figmaTokenArea').open = true;
      porId('figmaToken').focus();
      return;
    }
    if (vagasTelas() <= 0){
      mensagem('figmaMsg', 'Cada análise aceita até ' + cfg.maxTelas + ' telas. Remova uma para adicionar outra.', 'erro');
      return;
    }
    if (token) salvarTokenFigma(token);
    fecharOpcoesFigma();
    setBuscandoFigma(true, 'btnFigmaBuscar', 'Buscando…');
    TA.api.plannerFigma(corpoFigma(url)).then(function(res){
      if (!res.ok){ tratarErroFigma(res); return; }
      var d = res.dados || {};
      if (Array.isArray(d.frames)) return adicionarFramesFigma(d);
      mostrarOpcoesFigma(d, url);
    }).catch(function(e){
      mensagem('figmaMsg', mensagemErro(e), 'erro');
    }).then(function(){
      setBuscandoFigma(false, 'btnFigmaBuscar');
    });
  }

  function fecharOpcoesFigma(){
    var box = porId('figmaOpcoes');
    box.innerHTML = '';
    box.hidden = true;
  }

  function mostrarOpcoesFigma(d, url){
    var box = porId('figmaOpcoes');
    box.innerHTML = '';
    var opcoes = Array.isArray(d.opcoes) ? d.opcoes : [];
    var arquivo = d.arquivo || {};
    if (d.aviso) mensagem('figmaMsg', d.aviso, 'info');
    if (!opcoes.length){
      mensagem('figmaMsg', (d.aviso ? d.aviso + ' ' : '') + 'Nenhum frame encontrado nesse link. No Figma, clique ' +
        'com o botão direito no frame › Copy link to selection e cole aqui.', 'erro');
      box.hidden = true;
      return;
    }
    var vagas = Math.max(0, vagasTelas());
    var cab = mk('div', 'pl-fo-cab');
    var tit = mk('div', 'pl-fo-arquivo');
    tit.appendChild(mk('strong', null, arquivo.nome || 'Arquivo do Figma'));
    cab.appendChild(tit);
    cab.appendChild(mk('div', 'pl-fo-dica', 'Esse link não aponta um frame. Marque ' +
      (vagas === 1 ? 'o frame' : 'até ' + vagas + ' frames') + ' na ordem da jornada:'));
    box.appendChild(cab);

    var ul = mk('ul', 'pl-fo-lista');
    ul.setAttribute('aria-label', 'Frames do arquivo');
    var caixas = [];
    // Ordem em que a pessoa marca = ordem da jornada (é o que a dica pede).
    var ordem = [];
    var selos = {};
    opcoes.forEach(function(o, i){
      var li = mk('li');
      var lbl = mk('label', 'pl-fo-item');
      var chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.value = o.id;
      chk.id = 'figmaOpc' + i;
      var selo = mk('span', 'pl-fo-ordem');
      selo.setAttribute('aria-hidden', 'true');
      selos[o.id] = selo;
      var txt = mk('span', 'pl-fo-txt');
      txt.appendChild(mk('span', 'pl-fo-nome', o.nome || o.id));
      var meta = [];
      if (o.pagina) meta.push(o.pagina);
      if (o.tipo) meta.push(String(o.tipo).toLowerCase());
      if (o.largura && o.altura) meta.push(Math.round(o.largura) + ' × ' + Math.round(o.altura));
      txt.appendChild(mk('span', 'pl-fo-meta', meta.join(' · ')));
      lbl.appendChild(chk);
      lbl.appendChild(selo);
      lbl.appendChild(txt);
      li.appendChild(lbl);
      ul.appendChild(li);
      caixas.push(chk);
    });
    box.appendChild(ul);

    var rodape = mk('div', 'pl-fo-rodape');
    var cont = mk('span', 'pl-fo-cont');
    cont.setAttribute('aria-live', 'polite');
    var botoes = mk('div', 'pl-fo-botoes');
    var cancelar = mk('button', 'pl-btn-link', 'Cancelar');
    cancelar.type = 'button';
    var adicionar = mk('button', 'pl-btn pl-btn-pri pl-btn-peq', 'Adicionar selecionados');
    adicionar.type = 'button';
    adicionar.id = 'btnFigmaAdicionar';
    botoes.appendChild(cancelar);
    botoes.appendChild(adicionar);
    rodape.appendChild(cont);
    rodape.appendChild(botoes);
    box.appendChild(rodape);
    box.hidden = false;

    // Respeita as vagas: com o limite atingido, as demais caixas travam.
    function atualizarSelecao(){
      var marcadas = caixas.filter(function(c){ return c.checked; });
      var limite = Math.max(0, vagasTelas());
      caixas.forEach(function(c){ c.disabled = !c.checked && marcadas.length >= limite; });
      cont.textContent = marcadas.length + ' de ' + limite + ' selecionado' + (limite === 1 ? '' : 's');
      adicionar.disabled = !marcadas.length;
      for (var id in selos){
        if (!Object.prototype.hasOwnProperty.call(selos, id)) continue;
        var pos = ordem.indexOf(id);
        selos[id].textContent = pos >= 0 ? String(pos + 1) : '';
        selos[id].classList.toggle('ativo', pos >= 0);
      }
    }
    caixas.forEach(function(c){
      c.addEventListener('change', function(){
        var pos = ordem.indexOf(c.value);
        if (c.checked && pos < 0) ordem.push(c.value);
        if (!c.checked && pos >= 0) ordem.splice(pos, 1);
        atualizarSelecao();
      });
    });
    atualizarSelecao();
    cancelar.addEventListener('click', function(){ fecharOpcoesFigma(); mensagem('figmaMsg', ''); porId('figmaUrl').focus(); });
    adicionar.addEventListener('click', function(){
      // Na ordem em que foram marcados (o servidor devolve nessa ordem)
      var ids = ordem.filter(function(id){
        return caixas.some(function(c){ return c.checked && c.value === id; });
      });
      if (!ids.length) return;
      mensagem('figmaMsg', '');
      setBuscandoFigma(true, 'btnFigmaAdicionar', 'Exportando frames…');
      caixas.forEach(function(c){ c.disabled = true; });
      cancelar.disabled = true;
      TA.api.plannerFigma(corpoFigma(url, ids)).then(function(res){
        if (!res.ok){ tratarErroFigma(res); return; }
        var dd = res.dados || {};
        if (!Array.isArray(dd.frames)){
          mensagem('figmaMsg', 'O Figma não devolveu as imagens dos frames escolhidos. Tente de novo.', 'erro');
          return;
        }
        return adicionarFramesFigma(dd);
      }).catch(function(e){
        mensagem('figmaMsg', mensagemErro(e), 'erro');
      }).then(function(){
        setBuscandoFigma(false, 'btnFigmaAdicionar');
        if (!document.body.contains(adicionar)) return;     // a lista já fechou
        cancelar.disabled = false;
        atualizarSelecao();
      });
    });
    caixas[0].focus();
  }

  function adicionarFramesFigma(d){
    var frames = Array.isArray(d.frames) ? d.frames : [];
    var arquivo = d.arquivo || {};
    if (!frames.length){
      mensagem('figmaMsg', 'O Figma não devolveu nenhuma imagem para esse link.', 'erro');
      return Promise.resolve();
    }
    var vagas = Math.max(0, vagasTelas());
    var usar = frames.slice(0, vagas);
    var sobra = frames.length - usar.length;
    if (!usar.length){
      mensagem('figmaMsg', 'Cada análise aceita até ' + cfg.maxTelas + ' telas. Remova uma para adicionar outra.', 'erro');
      return Promise.resolve();
    }
    estado.processando += usar.length;
    atualizarProcessando();
    var ok = 0, truncado = false, falhas = [], soCamadas = 0;
    var cadeia = Promise.resolve();
    usar.forEach(function(f){
      cadeia = cadeia.then(function(){
        // PNG barrado pela rede (sem_imagem): prévia desenhada das camadas.
        var semImagem = !!f.sem_imagem || !f.base64;
        if (semImagem) soCamadas++;
        var carga = semImagem
          ? IMG.previaDeCamadas(Array.isArray(f.inventario) ? f.inventario : [], f.largura, f.altura)
          : IMG.carregarBase64(f.mime || 'image/png', f.base64 || '');
        return carga.then(function(v){
          rascunho.telas.push(novaTela(v, 'figma', f.nome || ('Frame ' + f.id), {
            file_key: arquivo.key || '',
            node_id: String(f.id || ''),
            inventario: Array.isArray(f.inventario) ? f.inventario : []
          }));
          ok++;
          if (f.inventario_truncado) truncado = true;
        }, function(e){ falhas.push('“' + (f.nome || f.id) + '”: ' + e.message); });
      }).then(function(){
        estado.processando--;
        renderTelas();
        atualizarProcessando();
      });
    });
    return cadeia.then(function(){
      fecharOpcoesFigma();
      var partes = [];
      if (ok){
        partes.push(plural(ok, 'frame adicionado', 'frames adicionados') +
          (arquivo.nome ? ' de “' + arquivo.nome + '”' : '') + ', com as camadas do arquivo.');
      }
      if (truncado) partes.push('Algum frame tem camadas demais: o inventário enviado ao modelo foi resumido.');
      if (sobra > 0) partes.push(plural(sobra, 'frame ficou', 'frames ficaram') + ' de fora pelo limite de ' + cfg.maxTelas + ' telas.');
      // Aviso do servidor (ex.: imagem barrada pelo proxy -> "só camadas")
      if (soCamadas && d.aviso) partes.push(d.aviso);
      if (falhas.length) partes.push('Falharam: ' + falhas.join(' '));
      mensagem('figmaMsg', partes.join(' '), falhas.length || !ok ? 'erro' : (soCamadas ? 'info' : 'ok'));
      if (ok) porId('figmaUrl').value = '';
    });
  }

  // =====================================================================
  // Formulário: contexto e envio
  // =====================================================================
  function atualizarEnvio(){
    var btn = porId('btnAnalisar'), st = porId('envioStatus');
    if (!btn || !rascunho) return;
    var ctx = lerContexto();
    var faltas = [];
    if (!rascunho.telas.length) faltas.push('adicione pelo menos 1 tela');
    if (!ctx.pergunta) faltas.push('escreva a pergunta de negócio');
    btn.disabled = faltas.length > 0 || estado.ocupado || estado.preparando || estado.processando > 0;
    if (estado.preparando) st.textContent = 'Preparando as imagens…';
    else if (estado.ocupado) st.textContent = 'Aguarde: há uma análise ou pergunta em andamento.';
    else if (estado.processando) st.textContent = 'Processando as imagens…';
    else if (faltas.length) st.textContent = 'Para analisar, ' + faltas.join(' e ') + '.';
    else {
      var plano = IMG.planejarEnvio(rascunho.telas, cfg);
      st.textContent = plural(rascunho.telas.length, 'tela', 'telas') + ' · ' +
        plural(plano.total, 'imagem', 'imagens') + ' para o modelo (máximo ' + cfg.maxPartes + ').';
    }
    porId('btnLimpar').hidden = !rascunhoTemConteudo();
  }

  function renderBannerEdicao(){
    var b = porId('bannerEdicao');
    var txt = porId('bannerEdicaoTxt');
    if (rascunho.modo === 'duplicar'){
      txt.textContent = 'Cópia de “' + rascunho.origemTitulo + '”. A análise original continua no histórico.';
      b.hidden = false;
    } else if (rascunho.modo === 'editar'){
      txt.textContent = 'Editando “' + rascunho.origemTitulo + '”, que não foi concluída. Ao analisar, ela é substituída.';
      b.hidden = false;
    } else {
      b.hidden = true;
      txt.textContent = '';
    }
  }

  function limparFormulario(confirmar){
    if (confirmar && rascunhoTemConteudo() &&
        !confirm('Limpar as telas e o contexto preenchidos?')) return;
    rascunho = novoRascunho();
    preencherContexto(contextoVazio());
    fecharOpcoesFigma();
    mensagem('telasMsg', '');
    mensagem('figmaMsg', '');
    mensagem('envioMsg', '');
    porId('figmaUrl').value = '';
    renderTelas();
    renderBannerEdicao();
    atualizarEnvio();
  }

  function carregarNoFormulario(reg, modo){
    rascunho = novoRascunho();
    rascunho.telas = (reg.telas || []).map(copiaTela);
    rascunho.modo = modo;
    rascunho.origemTitulo = reg.titulo || '';
    if (modo === 'editar'){
      rascunho.base = { id: reg.id, criadoEm: reg.criadoEm, titulo: reg.titulo, tituloManual: !!reg.tituloManual };
    }
    preencherContexto(reg.contexto);
    fecharOpcoesFigma();
    mensagem('telasMsg', '');
    mensagem('figmaMsg', '');
    mensagem('envioMsg', '');
    renderTelas();
    mostrarForm(true);
  }

  function duplicarRegistro(reg){
    if (rascunhoTemConteudo() &&
        !confirm('O formulário tem uma análise em preparo. Substituir pela cópia desta análise?')) return;
    carregarNoFormulario(reg, 'duplicar');
  }

  function editarRegistro(reg){
    if (rascunhoTemConteudo() &&
        !confirm('O formulário tem uma análise em preparo. Substituir pelas telas e pelo contexto desta?')) return;
    carregarNoFormulario(reg, 'editar');
  }

  function resumoEnvio(reg, envio){
    var partes = [plural(reg.telas.length, 'tela', 'telas')];
    if (envio){
      partes.push(plural(envio.totalPartes, 'imagem', 'imagens'));
      partes.push(IMG.formatarMb(envio.mb) + ' MB');
    }
    return partes.join(' · ');
  }

  // Grava no registro a geometria das partes enviadas: é ela que leva a
  // marcação da parte de volta para a tela inteira, inclusive ao reabrir.
  function aplicarGeometria(reg, envio){
    reg.telas.forEach(function(t, i){
      t.partes = envio.telas[i].partes.map(function(p){
        return { parte: p.parte, largura: p.largura, altura: p.altura,
                 recorte: { y: p.recorte.y, altura: p.recorte.altura } };
      });
    });
  }

  function analisar(){
    if (estado.ocupado || estado.preparando || estado.processando) return;
    var ctx = lerContexto();
    if (!rascunho.telas.length || !ctx.pergunta){
      atualizarEnvio();
      if (!ctx.pergunta) porId('campoPergunta').focus();
      return;
    }
    mensagem('envioMsg', '');
    var telas = rascunho.telas.map(function(t){
      var c = copiaTela(t);
      c.nome = (t.nome || '').replace(/\s+/g, ' ').trim() || ('Tela ' + (rascunho.telas.indexOf(t) + 1));
      delete c.uid;
      return c;
    });
    estado.preparando = true;
    var btn = porId('btnAnalisar');
    var rotulo = btn.innerHTML;
    btn.innerHTML = '<span class="pl-giro" aria-hidden="true"></span>Preparando…';
    atualizarEnvio();
    atualizarAvisoSaida();

    IMG.prepararEnvio(telas, cfg).then(function(envio){
      estado.preparando = false;
      btn.innerHTML = rotulo;
      var agora = Date.now();
      var base = rascunho.base;
      // Rede de segurança: a análise em "Editar" pode ter sido concluída por
      // outro caminho; resultado bom nunca é sobrescrito (vira análise nova).
      var resumoBase = base ? STORE.resumo(base.id) : null;
      if (resumoBase && resumoBase.status === 'ok') base = null;
      var reg = {
        id: base ? base.id : gerarId('p'),
        criadoEm: base ? (base.criadoEm || agora) : agora,
        atualizadoEm: agora,
        titulo: (base && base.tituloManual) ? base.titulo : tituloDe(ctx.pergunta),
        tituloManual: !!(base && base.tituloManual),
        status: 'pendente',
        contexto: ctx,
        telas: telas,
        mensagens: [],
        marcacao: null,
        marcacaoErro: null,
        aviso: null,
        erro: null
      };
      aplicarGeometria(reg, envio);
      // O formulário fica livre para a próxima; se falhar, "Editar" devolve tudo.
      limparFormulario(false);
      estado.atualId = reg.id;
      marcarHash(reg.id);
      STORE.salvar(reg);
      executarAnalise(reg, envio);
    }, function(e){
      estado.preparando = false;
      btn.innerHTML = rotulo;
      mensagem('envioMsg', mensagemErro(e), 'erro');
      atualizarEnvio();
      atualizarAvisoSaida();
    });
  }

  function corpoAnalise(reg, envio){
    return {
      session_id: reg.id,
      modo: 'analise',
      contexto: reg.contexto,
      telas: reg.telas.map(function(t, i){
        return {
          indice: i + 1,
          nome: t.nome,
          origem: t.origem,
          largura: t.largura,
          altura: t.altura,
          figma: t.figma ? { file_key: t.figma.file_key, node_id: t.figma.node_id,
                             inventario: t.figma.inventario || [] } : null,
          partes: envio.telas[i].partes
        };
      })
    };
  }

  // Envia a análise (registro já gravado como 'pendente'). Sem `envio`
  // ("Tentar de novo"), refaz as imagens a partir do histórico.
  function executarAnalise(reg, envio){
    estado.ocupado = true;
    estado.andamento = { id: reg.id, inicio: Date.now(), envio: null, etapaMin: 0, resumo: resumoEnvio(reg, envio) };
    atualizarAvisoSaida();
    atualizarEnvio();
    atualizarComposer();
    renderSidebar();
    if (estado.atualId === reg.id) mostrarProgresso(reg, true);
    iniciarRelogio();

    var preparo = envio ? Promise.resolve(envio) : IMG.prepararEnvio(reg.telas, cfg).then(function(e){
      aplicarGeometria(reg, e);
      STORE.salvar(reg);
      if (estado.andamento) estado.andamento.resumo = resumoEnvio(reg, e);
      return e;
    });
    preparo.then(function(e){
      if (estado.andamento){ estado.andamento.envio = Date.now(); estado.andamento.etapaMin = 1; }
      atualizarProgresso();
      return TA.api.plannerAnalisar(corpoAnalise(reg, e));
    }).then(function(res){
      if (!res.ok) throw erroDeResposta(res);
      concluirAnalise(reg, res.dados || {});
    }).catch(function(e){
      falhaAnalise(reg, e);
    }).then(function(){
      estado.ocupado = false;
      estado.andamento = null;
      pararRelogio();
      atualizarAvisoSaida();
      atualizarEnvio();
      atualizarComposer();
      renderSidebar();
    });
  }

  function concluirAnalise(reg, d){
    // A análise que está aberta no formulário em "Editar" acabou de dar certo
    // ("Tentar de novo"): o rascunho vira cópia, para não sobrescrever um
    // resultado bom ao clicar em Analisar.
    if (rascunho && rascunho.modo === 'editar' && rascunho.base && rascunho.base.id === reg.id){
      rascunho.modo = 'duplicar';
      rascunho.base = null;
      renderBannerEdicao();
    }
    reg.status = 'ok';
    reg.erro = null;
    reg.erroStatus = 0;
    reg.atualizadoEm = Date.now();
    reg.mensagens = [
      { id: gerarId('m'), role: 'usuario', display: reg.contexto.pergunta,
        content: d.user_content || reg.contexto.pergunta },
      { id: gerarId('m'), role: 'agente', display: d.resposta || '(sem conteúdo)',
        content: d.resposta_modelo || d.resposta || '', feedback: null, feedbackComentario: '' }
    ];
    reg.marcacao = (d.marcacao && typeof d.marcacao === 'object') ? d.marcacao : null;
    reg.marcacaoErro = d.marcacao_erro || null;
    reg.aviso = d.aviso || null;
    STORE.salvar(reg);
    // Erro de desenho aqui não pode virar "análise falhou": o resultado já está salvo.
    try {
      if (estado.atualId === reg.id) mostrarResultado(reg, true);
      else notificar('Análise concluída: “' + reg.titulo + '”.', { rotulo: 'Ver', fn: function(){ abrirRegistro(reg.id); } });
    } catch (e){
      if (window.console && console.error) console.error('[tag planner] falha ao exibir o resultado:', e);
    }
  }

  function falhaAnalise(reg, e){
    reg.status = 'erro';
    reg.erro = mensagemErro(e);
    reg.erroStatus = (e && e.status) || 0;
    reg.atualizadoEm = Date.now();
    STORE.salvar(reg);
    if (estado.atualId === reg.id) mostrarErro(reg, true);
    else notificar('A análise “' + reg.titulo + '” falhou.', { rotulo: 'Ver', fn: function(){ abrirRegistro(reg.id); } });
  }

  // =====================================================================
  // Vista: progresso
  // =====================================================================
  function mostrarProgresso(reg, focar){
    destruirVisualizador();
    porId('tituloPagina').textContent = reg.titulo;
    var v = porId('vistaProgresso');
    v.innerHTML = '';
    var miolo = mk('div', 'pl-miolo');
    var card = mk('div', 'pl-progresso pl-cartao');
    var icone = mk('div', 'pl-prog-icone');
    icone.innerHTML = '<img src="/static/planner-icone.svg" alt=""><span class="pl-prog-anel"></span>';
    card.appendChild(icone);
    var h = mk('h1', 'pl-prog-titulo', 'Analisando as telas');
    h.id = 'progTitulo';
    h.tabIndex = -1;
    h.setAttribute('data-foco', '');
    card.appendChild(h);
    card.appendChild(mk('p', 'pl-prog-pergunta', '“' + reg.contexto.pergunta + '”'));
    var etapa = mk('p', 'pl-prog-etapa');
    etapa.id = 'progEtapa';
    etapa.setAttribute('role', 'status');
    etapa.setAttribute('aria-live', 'polite');
    card.appendChild(etapa);
    var relogio = mk('div', 'pl-prog-relogio', '00:00');
    relogio.id = 'progRelogio';
    relogio.setAttribute('aria-hidden', 'true');
    card.appendChild(relogio);
    card.appendChild(mk('div', 'pl-prog-barra'));
    var nota = mk('p', 'pl-prog-nota', 'Isso pode levar alguns minutos — mantenha esta aba aberta. ' +
      'Enquanto isso, dá para consultar o histórico ou preparar a próxima análise.');
    nota.id = 'progNota';
    card.appendChild(nota);
    var resumo = mk('p', 'pl-prog-resumo');
    resumo.id = 'progResumo';
    card.appendChild(resumo);
    var acoes = mk('div', 'pl-prog-acoes');
    var nova = mk('button', 'pl-btn pl-btn-sec', 'Preparar outra análise');
    nova.type = 'button';
    nova.addEventListener('click', function(){ mostrarForm(true); });
    acoes.appendChild(nova);
    card.appendChild(acoes);
    miolo.appendChild(card);
    v.appendChild(miolo);
    atualizarProgresso();
    trocarVista('progresso', focar);
  }

  function atualizarProgresso(){
    var a = estado.andamento;
    if (!a) return;
    var seg = Math.floor((Date.now() - a.inicio) / 1000);
    var rel = porId('progRelogio');
    if (rel) rel.textContent = mmss(seg);
    var etapa = porId('progEtapa');
    if (etapa){
      var idx = a.etapaMin;
      if (a.envio) idx = Math.max(idx, 1 + Math.floor((Date.now() - a.envio) / 1000 / SEG_POR_ETAPA));
      idx = Math.min(ETAPAS.length - 1, idx);
      var texto = ETAPAS[idx] + '…';
      if (etapa.textContent !== texto) etapa.textContent = texto;   // só anuncia quando muda
    }
    var res = porId('progResumo');
    if (res && res.textContent !== a.resumo) res.textContent = a.resumo;
    var nota = porId('progNota');
    if (nota && seg >= SEG_DEMORANDO && !nota.getAttribute('data-demorando')){
      nota.setAttribute('data-demorando', '1');
      nota.textContent = 'Está levando mais que o normal, mas segue em andamento. Telas grandes e o ' +
        'raciocínio do modelo podem passar de 5 minutos — mantenha esta aba aberta.';
    }
  }

  function iniciarRelogio(){
    pararRelogio();
    estado.relogio = setInterval(atualizarProgresso, 1000);
  }
  function pararRelogio(){
    if (estado.relogio){ clearInterval(estado.relogio); estado.relogio = null; }
  }

  // =====================================================================
  // Vista: erro / análise interrompida
  // =====================================================================
  function mostrarErro(reg, focar){
    destruirVisualizador();
    porId('tituloPagina').textContent = reg.titulo;
    var interrompida = reg.status === 'pendente';
    var v = porId('vistaErro');
    v.innerHTML = '';
    var miolo = mk('div', 'pl-miolo');
    var card = mk('div', 'pl-erro-cartao pl-cartao');
    var icone = mk('div', 'pl-erro-icone' + (interrompida ? ' interrompida' : ''));
    icone.innerHTML = SVG_ALERTA;
    card.appendChild(icone);
    var h = mk('h1', 'pl-erro-titulo', interrompida ? 'Análise interrompida' : 'Não foi possível concluir a análise');
    h.tabIndex = -1;
    h.setAttribute('data-foco', '');
    card.appendChild(h);
    var p = mk('p', 'pl-erro-texto', interrompida
      ? 'A página foi fechada ou recarregada antes de a resposta chegar. As telas e o contexto ficaram guardados neste navegador: dá para tentar de novo.'
      : (reg.erro || 'Erro desconhecido.'));
    if (!interrompida) p.setAttribute('role', 'alert');
    card.appendChild(p);

    // o que estava sendo analisado
    var resumo = mk('div', 'pl-erro-resumo');
    resumo.appendChild(mk('div', 'pl-erro-pergunta', '“' + ((reg.contexto && reg.contexto.pergunta) || '') + '”'));
    var miniaturas = mk('div', 'pl-erro-telas');
    (reg.telas || []).forEach(function(t, i){
      var fig = mk('figure', 'pl-erro-tela');
      var img = document.createElement('img');
      img.src = t.dataUrl;
      img.alt = '';
      fig.appendChild(img);
      fig.appendChild(mk('figcaption', null, (i + 1) + '. ' + (t.nome || 'Tela')));
      miniaturas.appendChild(fig);
    });
    resumo.appendChild(miniaturas);
    card.appendChild(resumo);

    var acoes = mk('div', 'pl-erro-acoes');
    var tentar = mk('button', 'pl-btn pl-btn-pri');
    tentar.type = 'button';
    tentar.id = 'btnTentarDeNovo';
    tentar.innerHTML = SVG_REPETIR + '<span>Tentar de novo</span>';
    tentar.addEventListener('click', function(){ tentarDeNovo(reg); });
    var editar = mk('button', 'pl-btn pl-btn-sec');
    editar.type = 'button';
    editar.id = 'btnEditarAnalise';
    editar.innerHTML = SVG_LAPIS + '<span>Editar</span>';
    editar.addEventListener('click', function(){ editarRegistro(reg); });
    acoes.appendChild(tentar);
    acoes.appendChild(editar);
    card.appendChild(acoes);
    miolo.appendChild(card);
    v.appendChild(miolo);
    trocarVista('erro', focar);
    atualizarComposer();
  }

  function tentarDeNovo(reg){
    if (estado.ocupado || estado.preparando){
      notificar('Aguarde a análise em andamento terminar.');
      return;
    }
    reg.status = 'pendente';
    reg.erro = null;
    reg.atualizadoEm = Date.now();
    estado.atualId = reg.id;
    STORE.salvar(reg);
    executarAnalise(reg, null);
  }

  // =====================================================================
  // Vista: resultado
  // =====================================================================
  function chip(rotulo, valor){
    var c = mk('span', 'pl-chip');
    c.appendChild(mk('b', null, rotulo + ':'));
    c.appendChild(document.createTextNode(' ' + valor));
    c.title = rotulo + ': ' + valor;
    return c;
  }

  function chipsContexto(reg){
    var c = reg.contexto || {};
    var box = mk('div', 'pl-chips');
    if (c.produto) box.appendChild(chip('Produto', c.produto));
    if (c.jornada) box.appendChild(chip('Jornada', c.jornada));
    if (c.tela) box.appendChild(chip('Tela', c.tela));
    box.appendChild(chip('Canal', c.canal || 'não informado'));
    if (c.plataforma) box.appendChild(chip('Plataforma', c.plataforma));
    if (c.tela_anterior) box.appendChild(chip('Tela anterior', c.tela_anterior));
    if (c.tela_seguinte) box.appendChild(chip('Próxima tela', c.tela_seguinte));
    if (c.mapa_metricas) box.appendChild(chip('Mapa de métricas', 'informado'));
    box.appendChild(chip('Telas', String((reg.telas || []).length)));
    return box;
  }

  function caixaAviso(texto){
    var a = mk('div', 'pl-aviso');
    a.setAttribute('role', 'note');
    var ic = mk('span', 'pl-aviso-icone');
    ic.innerHTML = SVG_ALERTA;
    a.appendChild(ic);
    a.appendChild(mk('div', 'pl-aviso-txt', texto));
    return a;
  }

  function mostrarResultado(reg, focar){
    destruirVisualizador();
    porId('tituloPagina').textContent = reg.titulo;
    var v = porId('vistaResultado');
    v.innerHTML = '';
    var miolo = mk('div', 'pl-miolo pl-miolo-largo');
    // Grade: no computador a marcação fica numa coluna fixa à esquerda e o
    // cabeçalho + relatório à direita; empilhado (celular), a pergunta vem
    // primeiro, depois a marcação e o relatório (ordem pelas áreas do CSS).
    var grade = mk('div', 'pl-res-grid');

    var cab = mk('header', 'pl-res-cab pl-cartao');
    cab.appendChild(mk('div', 'pl-res-rotulo', 'Pergunta de negócio'));
    var h1 = mk('h1', 'pl-res-pergunta', (reg.contexto && reg.contexto.pergunta) || reg.titulo);
    h1.tabIndex = -1;
    h1.setAttribute('data-foco', '');
    cab.appendChild(h1);
    cab.appendChild(chipsContexto(reg));
    var rodape = mk('div', 'pl-res-rodape');
    var acoes = mk('div', 'pl-res-acoes');
    var bNova = mk('button', 'pl-btn pl-btn-sec pl-btn-peq');
    bNova.type = 'button';
    bNova.id = 'btnResNova';
    bNova.innerHTML = SVG_MAIS + '<span>Nova análise</span>';
    bNova.addEventListener('click', function(){ mostrarForm(true); });
    var bDup = mk('button', 'pl-btn pl-btn-sec pl-btn-peq');
    bDup.type = 'button';
    bDup.id = 'btnResDuplicar';
    bDup.innerHTML = SVG_COPIAR_DOC + '<span>Duplicar e editar</span>';
    bDup.addEventListener('click', function(){ duplicarRegistro(reg); });
    acoes.appendChild(bNova);
    acoes.appendChild(bDup);
    rodape.appendChild(acoes);
    rodape.appendChild(mk('span', 'pl-res-meta', 'Analisada ' + dataCurta(reg.atualizadoEm || reg.criadoEm)));
    cab.appendChild(rodape);
    grade.appendChild(cab);

    var colV = mk('div', 'pl-res-viewer');
    estado.visualizador = MARC.criarVisualizador({ registro: reg, plataforma: nomePlataforma() });
    colV.appendChild(estado.visualizador.el);
    grade.appendChild(colV);

    var colR = mk('div', 'pl-res-relatorio');
    if (reg.aviso) colR.appendChild(caixaAviso(reg.aviso));
    var thread = mk('div', 'pl-thread');
    thread.id = 'thread';
    thread.setAttribute('aria-live', 'polite');
    renderThread(reg, thread);
    colR.appendChild(thread);
    colR.appendChild(criarComposer(reg));
    grade.appendChild(colR);

    miolo.appendChild(grade);
    v.appendChild(miolo);
    trocarVista('resultado', focar);
    estado.visualizador.ajustar();
    atualizarComposer();
  }

  function renderThread(reg, thread){
    thread.innerHTML = '';
    var primeiraAgente = true;
    var msgs = reg.mensagens || [];
    msgs.forEach(function(m, i){
      // a 1ª mensagem do usuário é a pergunta de negócio (já está no cabeçalho)
      if (i === 0 && m.role === 'usuario') return;
      if (m.role === 'usuario') thread.appendChild(criarBolhaUsuario(m.display));
      else if (m.role === 'agente'){
        thread.appendChild(criarCartaoAgente(reg, m, primeiraAgente));
        primeiraAgente = false;
      }
    });
    // Pergunta de acompanhamento ainda esperando a IA: continua à vista ao
    // reabrir a análise (a resposta entra logo abaixo quando chegar).
    var p = estado.perguntaPendente;
    if (p && p.id === reg.id){
      if (p.pensando) p.pensando.encerrar();      // o da tela anterior
      p.bolha = criarBolhaUsuario(p.texto);
      p.pensando = criarPensando(p.inicio);
      thread.appendChild(p.bolha);
      thread.appendChild(p.pensando);
    }
  }

  function criarBolhaUsuario(texto){
    var b = mk('div', 'pl-bolha');
    b.appendChild(mk('span', 'pl-sr', 'Sua pergunta: '));
    b.appendChild(document.createTextNode(texto));
    return b;
  }

  // Liga a tabela do relatório à marcação: o ID (T1...) vira um botão que
  // destaca a caixa, e a decisão ganha a cor da classificação.
  function enriquecerTabelas(md){
    var celulas = md.querySelectorAll('td');
    for (var i = 0; i < celulas.length; i++){
      var td = celulas[i];
      var t = (td.textContent || '').trim();
      if (/^T\d{1,3}$/i.test(t) && estado.visualizador && estado.visualizador.tem(t.toUpperCase())){
        var id = t.toUpperCase();
        td.innerHTML = '';
        var b = mk('button', 'pl-ref-t', id);
        b.type = 'button';
        b.title = 'Mostrar ' + id + ' na marcação visual';
        b.setAttribute('data-id', id);
        td.appendChild(b);
        continue;
      }
      var norm = t.normalize ? t.normalize('NFD').replace(/[̀-ͯ]/g, '') : t;
      norm = norm.toUpperCase().replace(/\*/g, '').trim();
      var classe = null;
      if (norm === 'OBRIGATORIO') classe = 'pl-c-obr';
      else if (norm === 'OPCIONAL/DIAGNOSTICO' || norm === 'OPCIONAL' || norm === 'DIAGNOSTICO') classe = 'pl-c-opc';
      else if (norm === 'NAO TAGUEAR') classe = 'pl-c-nao';
      else if (norm === 'NECESSITA CONTEXTO') classe = 'pl-c-ctx';
      if (classe){
        td.innerHTML = '';
        td.appendChild(mk('span', 'pl-dec ' + classe, t.replace(/\*/g, '')));
      }
    }
  }

  function criarCartaoAgente(reg, msg, principal){
    var card = mk('article', 'pl-resposta pl-cartao' + (principal ? ' pl-resposta-principal' : ''));
    var topo = mk('div', 'pl-resposta-topo');
    topo.appendChild(mk(principal ? 'h2' : 'div', 'pl-resposta-titulo', principal ? 'Relatório' : 'Resposta'));
    if (msg.atualizouMarcacao) topo.appendChild(mk('span', 'pl-resposta-selo', 'atualizou a marcação'));
    var copiarBtn = mk('button', 'btn-copiar', '⧉ Copiar');
    copiarBtn.type = 'button';
    copiarBtn.title = 'Copiar o texto (markdown)';
    copiarBtn.addEventListener('click', function(){
      var ok = false;
      try { ok = TA.copiarTexto(msg.display); } catch (e){}
      copiarBtn.textContent = ok ? '✓ Copiado' : '✕ Não copiou';
      copiarBtn.classList.toggle('ok', ok);
      setTimeout(function(){ copiarBtn.textContent = '⧉ Copiar'; copiarBtn.classList.remove('ok'); }, 1800);
    });
    topo.appendChild(copiarBtn);
    card.appendChild(topo);
    if (msg.aviso) card.appendChild(caixaAviso(msg.aviso));
    var md = mk('div', 'md');
    md.innerHTML = TA.renderMarkdown(msg.display, { filtrarOk: false });
    enriquecerTabelas(md);
    md.addEventListener('click', function(ev){
      var alvo = ev.target.closest ? ev.target.closest('.pl-ref-t') : null;
      if (alvo && estado.visualizador) estado.visualizador.destacar(alvo.getAttribute('data-id'));
    });
    card.appendChild(md);
    card.appendChild(criarFeedback(reg, msg, principal));
    return card;
  }

  // 👍/👎 da resposta (mesma lógica do chat): o voto fica na mensagem e vai
  // ao servidor com ferramenta 'planner' para o painel admin separar a
  // satisfação por ferramenta. Clicar de novo no mesmo voto não gera evento.
  function criarFeedback(reg, msg, principal){
    var wrap = mk('div', 'pl-feedback');
    var barra = mk('div', 'feedback');
    var pergunta = mk('span', 'feedback-txt', principal ? 'Esta análise ajudou?' : 'Esta resposta ajudou?');
    var bLike = mk('button', 'fb-btn fb-like');
    bLike.type = 'button';
    bLike.title = 'Ajudou';
    bLike.setAttribute('aria-label', 'Ajudou');
    bLike.innerHTML = SVG_LIKE;
    var bDis = mk('button', 'fb-btn fb-dis');
    bDis.type = 'button';
    bDis.title = 'Não ajudou';
    bDis.setAttribute('aria-label', 'Não ajudou');
    bDis.innerHTML = SVG_DIS;

    var caixa = mk('div', 'fb-motivo');
    caixa.style.display = 'none';
    var inp = document.createElement('input');
    inp.type = 'text';
    inp.maxLength = 500;
    inp.placeholder = 'Opcional: o que faltou ou saiu errado?';
    inp.setAttribute('aria-label', 'Motivo (opcional)');
    var bEnv = mk('button', 'fb-enviar', 'Enviar');
    bEnv.type = 'button';
    caixa.appendChild(inp);
    caixa.appendChild(bEnv);

    function pintar(){
      var v = msg.feedback;
      bLike.classList.toggle('ativo', v === 'like');
      bDis.classList.toggle('ativo', v === 'dislike');
      bLike.setAttribute('aria-pressed', v === 'like' ? 'true' : 'false');
      bDis.setAttribute('aria-pressed', v === 'dislike' ? 'true' : 'false');
      if (v) pergunta.textContent = 'Obrigado pelo retorno!';
    }
    function enviar(voto, comentario){
      return TA.api.feedback({
        voto: voto, session_id: reg.id, msg_id: msg.id || '',
        comentario: comentario || '', ferramenta: 'planner'
      });
    }
    function votar(voto){
      var jaEra = msg.feedback;
      msg.feedback = voto;
      STORE.salvar(reg);
      pintar();
      caixa.style.display = voto === 'dislike' ? 'flex' : 'none';
      if (voto === 'dislike') inp.focus();
      if (jaEra === voto) return;
      enviar(voto, msg.feedbackComentario || '').catch(function(){ /* não atrapalha o uso */ });
    }
    bLike.addEventListener('click', function(){ votar('like'); });
    bDis.addEventListener('click', function(){ votar('dislike'); });
    function enviarMotivo(){
      if (bEnv.disabled) return;      // Enter repetido: um envio por vez
      msg.feedbackComentario = inp.value.trim();
      STORE.salvar(reg);
      bEnv.disabled = true;
      enviar('dislike', msg.feedbackComentario).then(function(){
        caixa.innerHTML = '';
        caixa.appendChild(mk('span', 'feedback-txt', 'Enviado. Obrigado!'));
      }).catch(function(){ bEnv.disabled = false; });
    }
    bEnv.addEventListener('click', enviarMotivo);
    inp.addEventListener('keydown', function(ev){
      if (ev.key === 'Enter'){ ev.preventDefault(); enviarMotivo(); }
    });

    barra.appendChild(pergunta);
    barra.appendChild(bLike);
    barra.appendChild(bDis);
    wrap.appendChild(barra);
    wrap.appendChild(caixa);
    pintar();
    if (msg.feedback === 'dislike'){
      if (msg.feedbackComentario){
        inp.value = msg.feedbackComentario;
      }
      caixa.style.display = 'flex';
    }
    return wrap;
  }

  // ---- perguntas de acompanhamento ----
  function criarComposer(reg){
    var form = mk('form', 'pl-composer pl-cartao');
    form.noValidate = true;
    form.setAttribute('data-registro', reg.id);
    var lbl = mk('label', 'pl-composer-rotulo', 'Pergunte sobre esta análise');
    lbl.htmlFor = 'perguntaTexto';
    form.appendChild(lbl);
    var linha = mk('div', 'pl-composer-linha');
    var ta = document.createElement('textarea');
    ta.id = 'perguntaTexto';
    ta.className = 'pl-input';
    ta.rows = 2;
    ta.maxLength = 2000;
    ta.placeholder = 'Ex.: E se o canal for WebView? · Precisa mesmo taguear o T3?';
    var bt = mk('button', 'pl-btn pl-btn-pri', 'Perguntar');
    bt.type = 'submit';
    bt.id = 'btnPerguntar';
    linha.appendChild(ta);
    linha.appendChild(bt);
    form.appendChild(linha);
    var dica = mk('p', 'pl-ajuda', 'Enter envia · Shift+Enter quebra linha. As imagens não são reenviadas: a resposta parte desta análise.');
    dica.id = 'composerDica';
    form.appendChild(dica);
    var erro = mk('div', 'pl-msg');
    erro.id = 'composerMsg';
    erro.setAttribute('role', 'alert');
    form.appendChild(erro);
    ta.setAttribute('aria-describedby', 'composerDica');
    form.addEventListener('submit', function(ev){ ev.preventDefault(); perguntar(reg); });
    ta.addEventListener('keydown', function(ev){
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing){
        ev.preventDefault();
        perguntar(reg);
      }
    });
    ta.addEventListener('input', function(){
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight + 2, 200) + 'px';
    });
    return form;
  }

  // Uma requisição por vez: trava a caixa de pergunta e o "Tentar de novo"
  // enquanto outra análise/pergunta estiver em andamento.
  function atualizarComposer(){
    var travado = estado.ocupado || estado.preparando;
    var dica = 'Aguarde a análise ou a pergunta em andamento terminar.';
    var ta = porId('perguntaTexto'), bt = porId('btnPerguntar');
    if (ta && bt){
      ta.disabled = travado;
      bt.disabled = travado;
      if (travado) bt.title = dica; else bt.removeAttribute('title');
    }
    var tentar = porId('btnTentarDeNovo');
    if (tentar){
      tentar.disabled = travado;
      if (travado) tentar.title = dica; else tentar.removeAttribute('title');
    }
  }

  function historicoModelo(reg){
    var msgs = (reg.mensagens || []).filter(function(m){
      return (m.role === 'usuario' || m.role === 'agente') && m.content;
    });
    if (msgs.length > MAX_HIST_ENVIO){
      msgs = msgs.slice(0, 2).concat(msgs.slice(msgs.length - (MAX_HIST_ENVIO - 2)));
    }
    return msgs.map(function(m){
      return { role: m.role === 'usuario' ? 'user' : 'assistant', content: m.content };
    });
  }

  // `inicio`: quando a pergunta saiu (o cronômetro continua do mesmo ponto se
  // a tela for redesenhada no meio da espera).
  function criarPensando(inicio){
    var p = mk('div', 'pl-pensando');
    p.setAttribute('role', 'status');
    var pontos = mk('span', 'digitando');
    pontos.innerHTML = '<span></span><span></span><span></span>';
    p.appendChild(pontos);
    var txt = mk('span', 'pl-pensando-txt', 'Pensando…');
    p.appendChild(txt);
    inicio = inicio || Date.now();
    p._timer = setInterval(function(){
      txt.textContent = 'Pensando… ' + mmss(Math.floor((Date.now() - inicio) / 1000));
    }, 1000);
    p.encerrar = function(){ clearInterval(p._timer); if (p.parentNode) p.parentNode.removeChild(p); };
    return p;
  }

  // Tira a pergunta pendente do estado, encerra o "Pensando…" e devolve os
  // elementos ATUAIS da tela ({bolha, pensando}): se a pessoa saiu e voltou
  // para a análise no meio da espera, eles foram redesenhados.
  function tirarPendente(){
    var p = estado.perguntaPendente || {};
    estado.perguntaPendente = null;
    if (p.pensando) p.pensando.encerrar();
    return p;
  }

  function rolarPara(el){
    if (!el || !el.scrollIntoView) return;
    try { el.scrollIntoView({ block: 'nearest', behavior: reduzMovimento() ? 'auto' : 'smooth' }); }
    catch (e){ el.scrollIntoView(false); }
  }

  function perguntar(reg){
    var ta = porId('perguntaTexto');
    if (!ta || estado.ocupado || estado.preparando) return;
    var texto = ta.value.trim();
    if (!texto) return;
    if (reg.status !== 'ok') return;
    mensagem('composerMsg', '');
    estado.ocupado = true;
    atualizarAvisoSaida();
    atualizarComposer();
    atualizarEnvio();

    var thread = porId('thread');
    var inicio = Date.now();
    var bolha = criarBolhaUsuario(texto);
    var pensando = criarPensando(inicio);
    thread.appendChild(bolha);
    thread.appendChild(pensando);
    ta.value = '';
    ta.style.height = 'auto';
    rolarPara(pensando);
    // Guardada no estado: se a pessoa sair e voltar para esta análise durante
    // a espera, renderThread desenha a pergunta e o "Pensando…" de novo.
    estado.perguntaPendente = { id: reg.id, texto: texto, inicio: inicio, bolha: bolha, pensando: pensando };

    var corpo = { session_id: reg.id, modo: 'pergunta', texto: texto, historico: historicoModelo(reg) };
    TA.api.plannerAnalisar(corpo).then(function(res){
      if (!res.ok) throw erroDeResposta(res);
      var ui = tirarPendente();
      // Apagada enquanto a resposta vinha: não grava nem avisa (ela não volta).
      if (STORE.foiApagado(reg.id)) return;
      var d = res.dados || {};
      var mU = { id: gerarId('m'), role: 'usuario', display: texto, content: d.user_content || texto };
      var mA = { id: gerarId('m'), role: 'agente', display: d.resposta || '(sem conteúdo)',
                 content: d.resposta_modelo || d.resposta || '', feedback: null, feedbackComentario: '',
                 aviso: d.aviso || null };
      // A pergunta só traz bloco novo quando a marcação muda: aí ela passa a
      // valer (a anterior é substituída). Sem bloco, a atual continua.
      var nova = (d.marcacao && Array.isArray(d.marcacao.elementos)) ? d.marcacao : null;
      if (nova){
        reg.marcacao = nova;
        reg.marcacaoErro = null;
        mA.atualizouMarcacao = true;
      } else if (d.marcacao_erro){
        // Veio um bloco de coordenadas inválido: a marcação continua a mesma,
        // e a pessoa precisa saber (o texto pode dizer que foi refeita).
        mA.aviso = (mA.aviso ? mA.aviso + ' ' : '') + 'A marcação não foi atualizada: ' + d.marcacao_erro;
        if (!reg.marcacao) reg.marcacaoErro = d.marcacao_erro;
      }
      reg.mensagens.push(mU, mA);
      reg.atualizadoEm = Date.now();
      STORE.salvar(reg);
      if (estado.atualId === reg.id && ui.bolha && document.body.contains(ui.bolha)){
        // a marcação nova vem antes do cartão: os IDs novos da tabela já
        // nascem clicáveis
        if (nova && estado.visualizador){
          estado.visualizador.atualizar(reg, { nota: 'Marcação atualizada pela última resposta.' });
        } else if (!nova && !reg.marcacao && d.marcacao_erro && estado.visualizador){
          estado.visualizador.atualizar(reg, {});     // aviso da marcação com o motivo novo
        }
        var cartao = criarCartaoAgente(reg, mA, false);
        ui.bolha.parentNode.appendChild(cartao);      // a conversa ATUAL da tela
        rolarPara(cartao);
      } else if (estado.atualId === reg.id && estado.vista === 'resultado'){
        // Rede de segurança: redesenha sem pular para o topo da página.
        var conteudo = porId('conteudo'), y = conteudo.scrollTop;
        mostrarResultado(reg, false);
        conteudo.scrollTop = y;
      } else {
        notificar('Resposta recebida em “' + reg.titulo + '”.', { rotulo: 'Ver', fn: function(){ abrirRegistro(reg.id); } });
      }
    }).catch(function(e){
      var ui = tirarPendente();
      if (ui.bolha && ui.bolha.parentNode) ui.bolha.parentNode.removeChild(ui.bolha);
      var t = porId('perguntaTexto');
      if (t && estado.atualId === reg.id){
        if (!t.value) t.value = texto;      // devolve a pergunta para tentar de novo
        mensagem('composerMsg', 'Não foi possível responder: ' + mensagemErro(e), 'erro');
      } else {
        notificar('A pergunta em “' + reg.titulo + '” falhou: ' + mensagemErro(e));
      }
    }).then(function(){
      estado.ocupado = false;
      atualizarAvisoSaida();
      atualizarComposer();
      atualizarEnvio();
      renderSidebar();
      var t = porId('perguntaTexto');
      if (t && estado.vista === 'resultado' && estado.atualId === reg.id){
        try { t.focus({ preventScroll: true }); } catch (x){ t.focus(); }
      }
    });
  }

  // =====================================================================
  // Ligações e início
  // =====================================================================
  function ligarFormulario(){
    var up = porId('abaUpload'), fg = porId('abaFigma');
    up.addEventListener('click', function(){ trocarAbaTelas('upload', false); });
    fg.addEventListener('click', function(){ trocarAbaTelas('figma', false); });
    [up, fg].forEach(function(aba){
      aba.addEventListener('keydown', function(ev){
        if (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft' || ev.key === 'Home' || ev.key === 'End'){
          ev.preventDefault();
          var irFigma = (ev.key === 'End') || (ev.key !== 'Home' && aba === up);
          trocarAbaTelas(irFigma ? 'figma' : 'upload', true);
        }
      });
    });

    var drop = porId('dropzone'), input = porId('inputArquivo');
    function abrirSeletor(){
      if (vagasTelas() <= 0){
        mensagem('telasMsg', 'Cada análise aceita até ' + cfg.maxTelas + ' telas. Remova uma para adicionar outra.', 'erro');
        return;
      }
      input.click();
    }
    drop.addEventListener('click', abrirSeletor);
    drop.addEventListener('keydown', function(ev){
      if (ev.key === 'Enter' || ev.key === ' '){ ev.preventDefault(); abrirSeletor(); }
    });
    input.addEventListener('change', function(){
      var arqs = paraLista(input.files);
      input.value = '';
      adicionarArquivos(arqs, 'upload');
    });
    ['dragenter', 'dragover'].forEach(function(tipo){
      drop.addEventListener(tipo, function(ev){
        if (!temArquivosArrastados(ev)) return;
        ev.preventDefault();
        ev.dataTransfer.dropEffect = 'copy';
        drop.classList.add('pl-arrastando');
      });
    });
    drop.addEventListener('dragleave', function(ev){
      if (ev.relatedTarget && drop.contains(ev.relatedTarget)) return;
      drop.classList.remove('pl-arrastando');
    });
    drop.addEventListener('drop', function(ev){
      ev.preventDefault();
      ev.stopPropagation();
      drop.classList.remove('pl-arrastando');
      adicionarArquivos(paraLista(ev.dataTransfer && ev.dataTransfer.files), 'upload');
    });
    // Soltar a imagem fora da área não pode abrir o arquivo no lugar da página
    // (perderia o formulário): no formulário ela entra como tela.
    window.addEventListener('dragover', function(ev){ if (temArquivosArrastados(ev)) ev.preventDefault(); });
    window.addEventListener('drop', function(ev){
      if (!temArquivosArrastados(ev)) return;
      ev.preventDefault();
      drop.classList.remove('pl-arrastando');
      if (estado.vista === 'form') adicionarArquivos(paraLista(ev.dataTransfer.files), 'upload');
    });
    // Ctrl+V com imagem na área de transferência (print da tela)
    document.addEventListener('paste', function(ev){
      if (estado.vista !== 'form') return;
      // Colando num campo de texto com texto na área de transferência (Excel e
      // OneNote mandam texto E imagem juntos): vale o texto, não vira tela.
      var alvo = ev.target;
      var editavel = alvo && (alvo.isContentEditable ||
        /^(INPUT|TEXTAREA)$/.test(alvo.tagName || ''));
      var temTexto = false;
      try { temTexto = !!(ev.clipboardData && ev.clipboardData.getData('text/plain')); } catch (e){}
      if (editavel && temTexto) return;
      var arqs = arquivosDaAreaDeTransferencia(ev.clipboardData);
      if (!arqs.length) return;          // só texto: segue o colar normal do campo
      ev.preventDefault();
      adicionarArquivos(arqs, 'colada');
    });

    // contexto
    for (var k in CAMPOS){
      if (Object.prototype.hasOwnProperty.call(CAMPOS, k)) porId(CAMPOS[k]).addEventListener('input', atualizarEnvio);
    }
    paraLista(document.querySelectorAll('input[name="canal"], input[name="plataforma"]')).forEach(function(r){
      r.addEventListener('change', atualizarEnvio);
    });
    var exemplos = porId('exemplosPergunta');
    EXEMPLOS_PERGUNTA.forEach(function(t){
      var b = mk('button', 'pl-chip-ex', t);
      b.type = 'button';
      b.addEventListener('click', function(){
        var campo = porId('campoPergunta');
        campo.value = t;
        campo.focus();
        try { campo.setSelectionRange(t.length, t.length); } catch (e){}
        atualizarEnvio();
      });
      exemplos.appendChild(b);
    });

    porId('btnAnalisar').addEventListener('click', analisar);
    porId('btnLimpar').addEventListener('click', function(){ limparFormulario(true); });
    porId('btnBannerDescartar').addEventListener('click', function(){ limparFormulario(true); });
  }

  function ligarFigma(){
    configurarFigma();
    porId('btnFigmaBuscar').addEventListener('click', buscarFigma);
    porId('figmaUrl').addEventListener('keydown', function(ev){
      if (ev.key === 'Enter'){ ev.preventDefault(); buscarFigma(); }
    });
    porId('figmaToken').addEventListener('change', function(){
      var v = porId('figmaToken').value.trim();
      if (v) salvarTokenFigma(v);
    });
    porId('btnFigmaEsquecer').addEventListener('click', function(){
      porId('figmaToken').value = '';
      salvarTokenFigma('');
      mensagem('figmaMsg', 'Token do Figma apagado deste navegador.', 'ok');
      porId('figmaToken').focus();
    });
  }

  function ligarShell(){
    carregarSidebar();
    porId('btnMenu').addEventListener('click', alternarSidebar);
    porId('overlay').addEventListener('click', fecharSidebar);
    porId('btnNovaAnalise').addEventListener('click', function(){ mostrarForm(true); });
    document.addEventListener('keydown', function(ev){
      if (ev.key === 'Escape' && ehMobile() && porId('app').classList.contains('sb-aberta')) fecharSidebar();
    });
  }

  function iniciar(){
    STORE = TA.plannerStore;
    IMG = TA.plannerImg;
    MARC = TA.plannerMarcacao;
    cfg = IMG.config();
    rascunho = novoRascunho();

    porId('limiteTelas').textContent = String(cfg.maxTelas);
    ligarShell();
    ligarFormulario();
    ligarFigma();
    renderTelas();
    renderBannerEdicao();
    trocarVista('form', false);

    STORE.aoFalhar(function(){
      notificar('O navegador não deixou guardar o histórico (modo privado ou sem espaço). ' +
        'As análises desta aba funcionam, mas somem ao recarregar.');
    });
    STORE.iniciar().then(function(){
      renderSidebar();
      var id = idDoHash();
      if (id && STORE.resumo(id)) abrirRegistro(id);
      else if (id) marcarHash(null);
    });
  }

  TA.planner = { iniciar: iniciar };

  // Boot: só mostra a tela depois de confirmar a sessão (sem sessão, o
  // sessao.js manda para o login com ?volta=/planner).
  TA.sessao.exigir().then(function(usuario){
    TA.sessao.renderMenu(usuario);
    porId('app').style.display = 'flex';
    // Tira o "validando" (style.css): some o spinner de conferência da sessão
    document.documentElement.classList.remove('validando');
    iniciar();
  }, function(){
    // Falha inesperada ao conferir a sessão: o login confere de novo (mesmo
    // tratamento do auditor.js), em vez de deixar a página no spinner.
    location.replace('/?volta=' + encodeURIComponent('/planner'));
  });
})();
