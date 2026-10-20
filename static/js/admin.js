/* Painel administrativo da plataforma (Auditor + Planner).
   Consome GET /api/admin/atividade (restrito aos e-mails de EMAILS_ADMIN no
   tagauditor/config.py). O mês é filtrado no servidor; o usuário (topo) e os
   filtros da trilha são locais.

   Visões (rota pelo #hash, dá para mandar o link):
     #geral    executiva: as duas ferramentas lado a lado
     #auditor  só o Auditor (auditoria, Confluence e feedback do Auditor)
     #planner  só o Planner (análises, perguntas, Figma e feedback do Planner)
     #eventos  trilha de eventos com filtros e paginação

   Os números de todas as visões saem da mesma apuração (agregar), com as
   mesmas regras do /api/resumo: auditoria concluída = status ≠ erro;
   análise = planner_analise ok no modo "analise". Gráficos sem bibliotecas
   (a rede do banco não garante CDN): HTML/SVG desenhados aqui. */
(function(){
  'use strict';

  var CFG = window.TAG_CONFIG || {};
  // Nome da plataforma (NOME_PLATAFORMA no config.py). O HTML já chega com o
  // nome trocado pelo servidor; a API também o devolve.
  var nomePlataforma = (CFG.plataforma && CFG.plataforma.nome) || 'Tag Studio';
  // ?volta= faz o login devolver a pessoa para o painel depois de entrar
  var URL_LOGIN = '/?volta=/admin';
  var LIMITE_EVENTOS = 5000;     // teto aceito por /api/admin/atividade

  function $(id){ return document.getElementById(id); }
  var principal = $('principal');
  var aviso = $('aviso');
  var notaLimite = $('notaLimite');
  var filtrosTopo = $('filtrosTopo');
  var vista = $('vista');
  var vistaEventos = $('vistaEventos');
  var vistaTitulo = $('vistaTitulo');
  var vistaSub = $('vistaSub');
  var nav = $('nav');
  var selMes = $('selMes');
  var selEmail = $('selEmail');
  var selFerramenta = $('selFerramenta');
  var selAcao = $('selAcao');
  var selStatus = $('selStatus');
  var busca = $('busca');
  var corpoTab = $('corpoTab');
  var vazio = $('vazio');
  var pagerEventos = $('pagerEventos');
  var eventosInfo = $('eventosInfo');
  var btnAtualizar = $('btnAtualizar');
  var btnBackup = $('btnBackup');
  var btnExportar = $('btnExportar');
  var tip = $('tip');
  var leitor = $('leitor');

  var eventos = [];        // eventos do mês carregado (mais recentes primeiro)
  var textosBusca = [];    // texto pesquisável de cada evento (mesmo índice)
  var votoAtual = Object.create(null);   // 'sessão|mensagem' -> evento do voto vigente
  var carregou = false;
  var baselineManualMin = 0;             // minutos por mapa no processo 100% manual
  var baselineFerramentaMin = 0;         // minutos por mapa usando o Auditor
  var baselinePlannerManualMin = 0;      // minutos por análise sem o Planner
  var baselinePlannerFerramentaMin = 0;  // minutos por análise com o Planner
  var moeda = 'R$';
  var cotacaoDolar = 0;                     // R$ por US$ 1
  var precoEntradaUsd = 0, precoSaidaUsd = 0;  // US$ por 1 milhão de tokens

  var VISTAS = {
    geral:   { titulo: 'Visão geral', sub: 'Uso, qualidade e custo do Auditor e do Planner' },
    auditor: { titulo: 'Auditor', sub: 'Auditorias de planos de tagueamento' },
    planner: { titulo: 'Planner', sub: 'Análises de telas e planos de mensuração' },
    eventos: { titulo: 'Trilha de eventos', sub: 'Tudo o que foi registrado no mês, evento a evento' }
  };
  var vistaAtual = 'geral';

  // Estado das tabelas (página, tamanho e ordenação) — sobrevive à troca de visão
  var tabelas = {
    eventos:          { pag: 1, tam: 25 },
    'usu-geral':      { pag: 1, tam: 10, ordem: 'uso', dir: -1 },
    'usu-auditor':    { pag: 1, tam: 10, ordem: 'uso', dir: -1 },
    'usu-planner':    { pag: 1, tam: 10, ordem: 'uso', dir: -1 }
  };
  var modoTabela = {};     // id do gráfico -> true quando mostra a tabela no lugar

  var ROTULOS = {
    auditoria: 'Auditoria',
    confluence: 'Confluence',
    feedback: 'Feedback',
    planner_analise: 'Análise (Planner)',
    planner_figma: 'Figma (Planner)',
    planner_esclarecimento: 'Dúvidas (Planner)',
    login: 'Login',
    login_falha: 'Falha de login',
    logout: 'Logout',
    servidor_iniciado: 'Servidor iniciado',
    admin_logs: 'Painel admin',
    admin_negado: 'Admin negado',
    admin_backup: 'Download do projeto',
    admin_export: 'Exportou CSV',
    // Conferência BigQuery (tela removida): só aparecem nos logs de meses antigos
    bq_conectar: 'BigQuery: conectar',
    bq_conectado: 'BigQuery: conectado',
    bq_desconectado: 'BigQuery: desconectado',
    bq_negado: 'BigQuery: negado',
    bq_projetos: 'BigQuery: projetos',
    bq_tabelas: 'BigQuery: tabelas',
    bq_analise: 'BigQuery: análise',
    bq_conferencia: 'BigQuery: conferência',
    bq_diagnostico: 'BigQuery: diagnóstico'
  };
  var FERRAMENTAS = {
    auditor: { nome: 'Auditor', cls: 's-aud' },
    planner: { nome: 'Planner', cls: 's-plan' },
    plataforma: { nome: 'Plataforma', cls: 's-plat' }
  };
  var ORIGENS = { upload: 'Upload de imagem', figma: 'Figma', colada: 'Colada (Ctrl+V)', misto: 'Misto' };
  // Colunas próprias da trilha; `ferramenta` vira o chip calculado (abaixo)
  var CAMPOS_FIXOS = { quando: 1, acao: 1, email: 1, nome: 1, ferramenta: 1 };
  // Chips que o analista procura primeiro vêm na frente; o resto segue a
  // ordem em que o servidor gravou o evento.
  var ORDEM_CHIPS = ['status', 'modo', 'voto', 'comentario', 'erro'];
  var CHAVES_NUM = /^(tokens_|caracteres)/;   // números que às vezes chegam como texto
  var CHAVES_CODIGO = { porta: 1 };           // números que são código (sem separador de milhar)
  // Valores-código do log exibidos com acento (o log e o CSV seguem sem)
  var CHAVES_ENUM = { status: 1, modo: 1, marcacao: 1 };
  var VALORES_EXIBIDOS = { opcoes: 'opções', analise: 'análise', invalida: 'inválida' };
  var DIAS_SEMANA = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb'];

  // ---- Utilitários -----------------------------------------------------------
  function esc(s){
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  // Consulta às tabelas de rótulos: um valor vindo do log não pode acertar
  // propriedades herdadas do objeto (ex.: "constructor").
  function proprio(obj, chave){
    return Object.prototype.hasOwnProperty.call(obj, chave) ? obj[chave] : undefined;
  }
  // Minúsculas e sem acento: a busca por "analise" acha "análise" e vice-versa
  function semAcento(s){
    s = String(s == null ? '' : s).toLowerCase();
    return s.normalize ? s.normalize('NFD').replace(/[̀-ͯ]/g, '') : s;
  }
  function token(){
    try { return localStorage.getItem('tag_auditor_token'); }
    catch (e) { return null; }
  }
  function slugPlataforma(){
    return semAcento(nomePlataforma).replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'plataforma';
  }

  function fmtNum(n, casas){
    var v = Number(n) || 0;
    try { return v.toLocaleString('pt-BR', { maximumFractionDigits: casas == null ? 2 : casas }); }
    catch (e) { return String(v); }
  }
  function plural(n, um, varios){
    return fmtNum(n, 0) + ' ' + (n === 1 ? um : varios);
  }
  // Números grandes compactos para os KPIs de tokens: 1,2 mi · 845 mil
  function fmtCompacto(n){
    n = Number(n) || 0;
    if (n >= 1e6) return fmtNum(n / 1e6, 1) + ' mi';
    if (n >= 1e4) return fmtNum(n / 1e3, 0) + ' mil';
    return fmtNum(n, 0);
  }
  // Duração "humana" para estimativas: 45 s, 15 min, 2h 30min
  function fmtDuracao(segundos){
    var s = Math.max(0, Math.round(Number(segundos) || 0));
    if (s < 60) return s + ' s';
    var min = Math.round(s / 60), h = Math.floor(min / 60), m = min % 60;
    if (h >= 1) return fmtNum(h, 0) + 'h' + (m ? ' ' + m + 'min' : '');
    return m + ' min';
  }
  // Duração medida (duracao_s do log): 4,8 s · 1 min 12 s
  function fmtSegundos(segundos){
    var s = Math.max(0, Number(segundos) || 0);
    if (s < 60) return fmtNum(Math.round(s * 10) / 10, 1) + ' s';
    var m = Math.floor(s / 60), r = Math.round(s - m * 60);
    if (r === 60){ m++; r = 0; }
    return m + ' min' + (r ? ' ' + r + ' s' : '');
  }
  function fmtBytes(b){
    b = Math.max(0, Number(b) || 0);
    if (b < 1024) return fmtNum(b, 0) + ' B';
    if (b < 1024 * 1024) return fmtNum(b / 1024, 1) + ' KB';
    return fmtNum(b / (1024 * 1024), 1) + ' MB';
  }
  function fmtDinheiro(v){
    try { return moeda + ' ' + Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
    catch (e) { return moeda + ' ' + Number(v || 0).toFixed(2); }
  }
  function fmtUsd(v){
    try { return 'US$ ' + Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
    catch (e) { return 'US$ ' + Number(v || 0).toFixed(2); }
  }
  function fmtPct(v){ return Math.round(v * 100) + '%'; }
  function fmtMes(mes){
    var nomes = ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'];
    var m = /^(\d{4})-(\d{2})$/.exec(mes);
    return m ? (nomes[parseInt(m[2], 10) - 1] + '/' + m[1]) : mes;
  }
  // "2026-07-22T14:03:11" -> "22/07/2026" + hora em tom mais suave
  function fmtQuandoHTML(iso){
    var m = /^(\d{4})-(\d{2})-(\d{2})T(.+)$/.exec(String(iso || ''));
    if (!m) return esc(String(iso || ''));
    return m[3] + '/' + m[2] + '/' + m[1] + ' <span class="hora">' + esc(m[4]) + '</span>';
  }
  // "2026-10-06T09:15:00" -> "06/10 09:15"
  function fmtDiaHora(iso){
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2})/.exec(String(iso || ''));
    return m ? m[3] + '/' + m[2] + ' ' + m[4] : String(iso || '');
  }
  // 'AAAA-MM-DD' -> Date ao meio-dia UTC: somar 24h nunca pula nem repete dia
  // (UTC não tem horário de verão) e o dia da semana não depende do fuso.
  function dataDoDia(dia){
    return new Date(Date.UTC(+dia.slice(0, 4), +dia.slice(5, 7) - 1, +dia.slice(8, 10), 12));
  }
  function fmtDiaCurto(dia){   // 'sex, 16/10' (o ano já está no filtro de mês)
    return DIAS_SEMANA[dataDoDia(dia).getUTCDay()] + ', ' + dia.slice(8, 10) + '/' + dia.slice(5, 7);
  }
  function fmtDiaLongo(dia){ return fmtDiaCurto(dia) + '/' + dia.slice(0, 4); }
  // Todos os dias de `inicio` a `fim` (inclusive). Dia sem uso aparece vazio
  // em vez de sumir: o eixo do tempo não fica comprimido.
  function diasEntre(inicio, fim){
    var dias = [], d = dataDoDia(inicio), ultimo = dataDoDia(fim).getTime();
    while (d.getTime() <= ultimo && dias.length < 400){
      dias.push(d.toISOString().slice(0, 10));
      d = new Date(d.getTime() + 864e5);
    }
    return dias;
  }
  // Escala "redonda" para o eixo: 7 -> 8, 23 -> 25, 140 -> 150
  function escalaRedonda(max){
    if (max <= 4) return Math.max(1, Math.ceil(max));
    var p = Math.pow(10, Math.floor(Math.log(max) / Math.LN10)), f = max / p;
    var n = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
    var r = n * p;
    return r % 2 ? r + 1 : r;   // par: a linha do meio cai num inteiro
  }
  function tom(v, bom, alerta){ return v >= bom ? 'bom' : v >= alerta ? 'alerta' : 'ruim'; }

  // ---- Classificação dos eventos -------------------------------------------
  // Ferramenta de cada evento: o que começa com planner_ ou traz
  // ferramenta=planner é do Planner; auditoria, Confluence e feedback sem
  // ferramenta (os votos antigos não têm o campo) são do Auditor; o resto
  // (login, logout, admin_*, servidor, bq_* antigos) é da plataforma.
  function ferramentaDe(ev){
    var acao = String(ev.acao || '');
    if (acao.indexOf('planner_') === 0 || ev.ferramenta === 'planner') return 'planner';
    if (acao === 'auditoria' || acao === 'confluence' || acao === 'feedback') return 'auditor';
    return 'plataforma';
  }
  function rotuloAcao(acao){
    return proprio(ROTULOS, acao) || (acao ? String(acao) : '—');
  }
  function ehVoto(ev){
    return ev.acao === 'feedback' && (ev.voto === 'like' || ev.voto === 'dislike');
  }
  function chaveVoto(ev){
    return (ev.sessao || '') + '|' + (ev.msg_id || '');
  }
  // Voto LÍQUIDO: cada resposta (sessão|mensagem) conta 1 vez e o voto MAIS
  // RECENTE vence — trocar de voto ou reenviar com comentário não soma de
  // novo. O voto vigente é apurado no mês inteiro (eventos vem do mais novo
  // para o mais antigo: a 1ª ocorrência da chave é o voto atual) e só depois
  // cruzado com os filtros; assim filtrar não ressuscita um voto trocado.
  function indexarVotos(){
    votoAtual = Object.create(null);
    eventos.forEach(function(ev){
      if (!ehVoto(ev)) return;
      var k = chaveVoto(ev);
      if (votoAtual[k] === undefined) votoAtual[k] = ev;
    });
  }
  function ehVotoAtual(ev){
    return ehVoto(ev) && votoAtual[chaveVoto(ev)] === ev;
  }
  // A busca também acha pelo rótulo da ação e pelo nome da ferramenta
  // ("planner", "falha de login"), não só pelo JSON cru do log.
  function prepararBusca(){
    textosBusca = eventos.map(function(ev){
      return semAcento(JSON.stringify(ev) + ' ' + rotuloAcao(ev.acao) + ' ' +
                       FERRAMENTAS[ferramentaDe(ev)].nome);
    });
  }
  function classeAcao(ev){
    if (ev.status === 'erro' || ev.acao === 'login_falha' || ev.acao === 'admin_negado') return 'erro';
    if (ev.status === 'ok' || ev.acao === 'login') return 'ok';
    return '';
  }

  // ---- Apuração -------------------------------------------------------------
  function zeros(n){ var a = []; for (var i = 0; i < n; i++) a.push(0); return a; }
  function novaFerramenta(){
    return { ok: 0, erro: 0, dur: 0, nDur: 0, durs: [], tokE: 0, tokS: 0, temTok: false, est: false,
             like: 0, dislike: 0, horas: zeros(24), fb: [], erros: [] };
  }
  function novoUsuario(ev){
    return { email: ev.email, nome: ev.nome || '', aud: 0, audErro: 0, plan: 0, perg: 0, planErro: 0,
             durAud: 0, nDurAud: 0, durPlan: 0, nDurPlan: 0, tokAud: 0, tokPlan: 0,
             likeAud: 0, disAud: 0, likePlan: 0, disPlan: 0, ultAud: '', ultPlan: '', dias: {} };
  }
  function dur(ev){ var d = Number(ev.duracao_s); return isFinite(d) && d >= 0 ? d : null; }

  // Uma passada pela lista (já filtrada por usuário) apura tudo o que as
  // visões desenham. `lista` vem do mais novo para o mais antigo.
  function agregar(lista){
    var A = {
      aud: novaFerramenta(), plan: novaFerramenta(),
      anexo: 0, texto: 0, confOk: 0, confOpcoes: 0, confErro: 0,
      perg: 0, telas: 0, obrig: 0, opc: 0, elem: 0, semImagem: 0, canais: {}, origens: {},
      figmaOk: 0, figmaOpcoes: 0, figmaErro: 0,
      logins: 0, loginFalhas: 0, porDia: {}, usuarios: {}
    };
    lista.forEach(function(ev){
      var acao = ev.acao, ferr = ferramentaDe(ev);
      var F = ferr === 'planner' ? A.plan : ferr === 'auditor' ? A.aud : null;
      var u = null;
      if (ev.email){
        u = A.usuarios[ev.email] || (A.usuarios[ev.email] = novoUsuario(ev));
        if (!u.nome && ev.nome) u.nome = ev.nome;
      }
      var quando = String(ev.quando || ''), dia = quando.slice(0, 10);
      var hora = parseInt(quando.slice(11, 13), 10);
      var diaOk = /^\d{4}-\d{2}-\d{2}$/.test(dia);

      if (F && (ev.tokens_entrada != null || ev.tokens_saida != null)){
        var te = Number(ev.tokens_entrada) || 0, ts = Number(ev.tokens_saida) || 0;
        F.temTok = true; F.tokE += te; F.tokS += ts;
        if (ev.tokens_fonte === 'estimativa') F.est = true;
        if (u){ if (ferr === 'planner') u.tokPlan += te + ts; else u.tokAud += te + ts; }
      }

      function uso(chaveDia){
        if (diaOk){
          var d = A.porDia[dia] || (A.porDia[dia] = { aud: 0, plan: 0 });
          d[chaveDia]++;
          if (u) u.dias[dia] = 1;
        }
        if (hora >= 0 && hora < 24) F.horas[hora]++;
        var t = dur(ev);
        if (t != null){ F.dur += t; F.nDur++; F.durs.push(t); }
      }

      if (acao === 'auditoria'){
        if (u && quando > u.ultAud) u.ultAud = quando;
        if (ev.status === 'erro'){ A.aud.erro++; A.aud.erros.push(ev); if (u) u.audErro++; }
        else {
          A.aud.ok++;
          if (ev.com_anexo) A.anexo++; else A.texto++;
          uso('aud');
          if (u){ u.aud++; var t1 = dur(ev); if (t1 != null){ u.durAud += t1; u.nDurAud++; } }
        }
      } else if (acao === 'confluence'){
        if (ev.status === 'erro'){ A.confErro++; A.aud.erros.push(ev); }
        else if (ev.status === 'opcoes') A.confOpcoes++;
        else A.confOk++;
      } else if (acao === 'planner_analise'){
        if (u && quando > u.ultPlan) u.ultPlan = quando;
        if (ev.status === 'erro'){ A.plan.erro++; A.plan.erros.push(ev); if (u) u.planErro++; }
        else if (ev.status === 'ok' && ev.modo === 'analise'){
          A.plan.ok++;
          A.telas += Number(ev.telas) || 0;
          A.obrig += Number(ev.obrigatorios) || 0;
          A.opc += Number(ev.opcionais) || 0;
          A.elem += Number(ev.elementos) || 0;
          if (ev.sem_imagem) A.semImagem++;
          var canal = String(ev.canal || '') || 'Não informado';
          A.canais[canal] = (A.canais[canal] || 0) + 1;
          var orig = proprio(ORIGENS, ev.origem) || (ev.origem ? String(ev.origem) : 'Não informado');
          A.origens[orig] = (A.origens[orig] || 0) + 1;
          uso('plan');
          if (u){ u.plan++; var t2 = dur(ev); if (t2 != null){ u.durPlan += t2; u.nDurPlan++; } }
        } else if (ev.status === 'ok' && ev.modo === 'pergunta'){
          A.perg++;
          if (u) u.perg++;
        }
      } else if (acao === 'planner_figma'){
        if (ev.status === 'erro'){ A.figmaErro++; A.plan.erros.push(ev); }
        else if (ev.status === 'opcoes') A.figmaOpcoes++;
        else A.figmaOk++;
      } else if (acao === 'login') A.logins++;
      else if (acao === 'login_falha') A.loginFalhas++;

      if (ehVotoAtual(ev) && F){
        var like = ev.voto === 'like';
        if (like) F.like++; else F.dislike++;
        F.fb.push(ev);
        if (u){
          if (ferr === 'planner'){ if (like) u.likePlan++; else u.disPlan++; }
          else { if (like) u.likeAud++; else u.disAud++; }
        }
      }
    });
    return A;
  }

  // Custo = tokens/1 milhão × preço (US$) × cotação — entrada e saída diferem
  function temPreco(){ return (precoEntradaUsd > 0 || precoSaidaUsd > 0) && cotacaoDolar > 0; }
  function custoUsd(tokE, tokS){ return (tokE / 1e6) * precoEntradaUsd + (tokS / 1e6) * precoSaidaUsd; }
  // Tempo economizado (estimativa do time, config.py), em minutos
  function poupAud(){ return Math.max(0, baselineManualMin - baselineFerramentaMin); }
  function poupPlan(){ return Math.max(0, baselinePlannerManualMin - baselinePlannerFerramentaMin); }

  function usuariosCom(A, filtro){
    return Object.keys(A.usuarios).map(function(e){ return A.usuarios[e]; }).filter(filtro);
  }
  function recorrentes(lista){
    return lista.filter(function(u){ return Object.keys(u.dias).length >= 2; }).length;
  }

  // ---- Peças de tela (HTML) -------------------------------------------------
  // KPI: {rot, num, det, tom, cor ('aud'|'plan'), medidor (0..1), dica, id}
  function kpisHTML(lista){
    return '<section class="kpis" aria-label="Indicadores">' + lista.map(function(k){
      var cor = k.cor === 'aud' ? ' style="background:var(--cor-auditor)"'
        : k.cor === 'plan' ? ' style="background:var(--cor-planner)"' : '';
      return '<div class="kpi"' + (k.dica ? ' title="' + esc(k.dica) + '"' : '') + '>'
        + '<div class="rot"><span class="pt"' + cor + '></span>' + esc(k.rot) + '</div>'
        + '<div class="num' + (k.tom ? ' ' + k.tom : '') + '">' + esc(k.num) + '</div>'
        + (k.medidor != null
          ? '<div class="medidor" aria-hidden="true"><i class="' + (k.tom || '') + '" style="width:' +
            Math.round(Math.max(0, Math.min(1, k.medidor)) * 100) + '%"></i></div>' : '')
        + '<div class="det">' + (k.det || '') + '</div>'
        + '</div>';
    }).join('') + '</section>';
  }
  function cardHTML(o){
    // o: {titulo, info, acao (HTML), corpo, id}
    return '<section class="card"' + (o.id ? ' id="' + o.id + '"' : '') + ' aria-label="' + esc(o.titulo) + '">'
      + '<div class="card-cab"><div class="tit"><h2>' + esc(o.titulo) + '</h2>'
      + (o.info ? '<div class="info">' + o.info + '</div>' : '') + '</div>'
      + (o.acao || '') + '</div>'
      + o.corpo + '</section>';
  }
  function legendaHTML(series){
    if (series.length < 2) return '';
    return '<div class="legenda">' + series.map(function(s){
      return '<span class="item"><span class="sw ' + s.cls + '"></span>' + esc(s.nome) + '</span>';
    }).join('') + '</div>';
  }
  function vazioHTML(txt){ return '<div class="vazio-card">' + esc(txt) + '</div>'; }

  // Tooltip: data-tip = linhas separadas por \n; a 1ª é o título; uma linha
  // "@s-aud|texto" ganha a marca de cor da série; "--" vira separador.
  function tipAttr(linhas){ return ' data-tip="' + esc(linhas.join('\n')) + '"'; }

  // ---- Gráfico de colunas (empilhado) ----------------------------------------
  // colunas: [{rot, titulo, chave, fds, v:{k:n}}]; series: [{k, nome, cls, um, varios}]
  function colunasHTML(id, colunas, series, opts){
    var H = opts.altura || 170, Hb = H - 18;
    var max = 0;
    colunas.forEach(function(c){
      c.total = series.reduce(function(s, se){ return s + (c.v[se.k] || 0); }, 0);
      if (c.total > max) max = c.total;
    });
    var escala = escalaRedonda(max);
    var ticks = [0, escala];
    if (escala >= 2 && escala % 2 === 0) ticks.splice(1, 0, escala / 2);
    function topo(t){ return Math.round(H - (t / escala) * Hb); }
    var eixo = '<div class="cc-eixo" style="height:' + H + 'px" aria-hidden="true">' + ticks.map(function(t){
      return '<span style="top:' + topo(t) + 'px">' + fmtNum(t, 1) + '</span>';
    }).join('') + '</div>';
    var grade = '<div class="cc-grade" style="height:' + H + 'px" aria-hidden="true">' + ticks.map(function(t){
      return '<i' + (t === 0 ? ' class="base"' : '') + ' style="top:' + topo(t) + 'px"></i>';
    }).join('') + '</div>';

    var picoMarcado = false;
    var cols = colunas.map(function(c, i){
      var segs = series.filter(function(se){ return c.v[se.k] > 0; });
      var h = c.total ? Math.max(3 + 2 * (segs.length - 1), Math.round(c.total / escala * Hb)) : 0;
      var util = h - 2 * (segs.length - 1), usado = 0;
      // de cima para baixo: a última série fica na base
      var pilha = segs.slice().reverse().map(function(se, j, arr){
        var hs = j === arr.length - 1 ? Math.max(2, util - usado) : Math.max(2, Math.round(util * c.v[se.k] / c.total));
        usado += hs;
        return '<div class="cc-seg ' + se.cls + '" style="height:' + hs + 'px"></div>';
      }).join('');
      var pico = c.total > 0 && c.total === max && !picoMarcado;
      if (pico) picoMarcado = true;
      var linhas = [c.titulo];
      series.forEach(function(se){
        linhas.push((series.length > 1 ? '@' + se.cls + '|' : '') + fmtNum(c.v[se.k] || 0, 0) + ' ' +
                    ((c.v[se.k] || 0) === 1 ? se.um : se.varios));
      });
      if (series.length > 1){ linhas.push('--'); linhas.push(fmtNum(c.total, 0) + ' no total'); }
      return '<div class="cc-col" data-i="' + i + '"' + tipAttr(linhas) + '>'
        + '<div class="cc-area" style="height:' + H + 'px">'
        + (pico ? '<span class="cc-val">' + fmtNum(c.total, 0) + '</span>' : '')
        + (h ? '<div class="cc-pilha" style="height:' + h + 'px">' + pilha + '</div>' : '')
        + '</div>'
        + '<span class="cc-rot' + (c.chave ? ' chave' : '') + (c.fds ? ' fds' : '') + '">' + esc(c.rot) + '</span>'
        + '</div>';
    }).join('');
    return legendaHTML(series)
      + '<div class="cc" id="' + id + '" tabindex="0" role="group" data-n="' + colunas.length + '"'
      + ' aria-label="' + esc(opts.aria + '. Use as setas para ler cada coluna.') + '">'
      + eixo + grade + '<div class="cc-cols">' + cols + '</div></div>';
  }
  // Tabela equivalente ao gráfico (botão "Ver tabela")
  function tabelaColunasHTML(colunas, series, rotuloCol){
    var tot = {}, linhas = '';
    series.forEach(function(se){ tot[se.k] = 0; });
    colunas.forEach(function(c){
      if (!c.total) return;
      linhas += '<tr><td>' + esc(c.titulo) + '</td>' + series.map(function(se){
        tot[se.k] += c.v[se.k] || 0;
        return '<td>' + fmtNum(c.v[se.k] || 0, 0) + '</td>';
      }).join('') + (series.length > 1 ? '<td>' + fmtNum(c.total, 0) + '</td>' : '') + '</tr>';
    });
    var soma = series.reduce(function(s, se){ return s + tot[se.k]; }, 0);
    return '<div class="cc-tabela"><table class="tab-grafico">'
      + '<thead><tr><th scope="col">' + esc(rotuloCol) + '</th>' + series.map(function(se){
        return '<th scope="col"><span class="sw ' + se.cls + '" style="width:8px;height:8px;margin-right:5px"></span>' + esc(se.nome) + '</th>';
      }).join('') + (series.length > 1 ? '<th scope="col">Total</th>' : '') + '</tr></thead>'
      + '<tbody>' + linhas + '</tbody>'
      + '<tfoot><tr><th scope="row">Total</th>' + series.map(function(se){
        return '<td>' + fmtNum(tot[se.k], 0) + '</td>';
      }).join('') + (series.length > 1 ? '<td>' + fmtNum(soma, 0) + '</td>' : '') + '</tr></tfoot></table></div>';
  }
  function cardColunas(o){
    // o: {id, titulo, info, colunas, series, aria, rotuloCol, vazio}
    var temDados = o.colunas.some(function(c){
      return o.series.some(function(se){ return c.v[se.k] > 0; });
    });
    var corpo;
    if (!temDados) corpo = vazioHTML(o.vazio);
    else if (modoTabela[o.id]){
      o.colunas.forEach(function(c){
        c.total = o.series.reduce(function(s, se){ return s + (c.v[se.k] || 0); }, 0);
      });
      corpo = tabelaColunasHTML(o.colunas, o.series, o.rotuloCol);
    } else corpo = colunasHTML(o.id, o.colunas, o.series, { aria: o.aria, altura: o.altura });
    var acao = temDados ? '<button type="button" class="btn-mini" data-alternar="' + o.id + '" data-foco="alt:' + o.id + '">' +
      (modoTabela[o.id] ? 'Ver gráfico' : 'Ver tabela') + '</button>' : '';
    return cardHTML({ titulo: o.titulo, info: o.info, acao: acao, corpo: corpo });
  }

  // Colunas por dia, do 1º ao último dia com uso
  function colunasPorDia(A, chaves){
    var dias = Object.keys(A.porDia).filter(function(d){
      return chaves.some(function(k){ return A.porDia[d][k] > 0; });
    }).sort();
    if (!dias.length) return [];
    return diasEntre(dias[0], dias[dias.length - 1]).map(function(dia){
      var d = A.porDia[dia] || {}, n = +dia.slice(8, 10), sem = dataDoDia(dia).getUTCDay();
      var v = {};
      chaves.forEach(function(k){ v[k] = d[k] || 0; });
      return { rot: String(n), titulo: fmtDiaLongo(dia), chave: n === 1 || n % 5 === 0,
               fds: sem === 0 || sem === 6, v: v };
    });
  }
  // Colunas por hora do dia: o horário comercial sempre aparece inteiro
  function colunasPorHora(horas, k){
    var ini = 8, fim = 19;
    horas.forEach(function(n, h){ if (n){ if (h < ini) ini = h; if (h > fim) fim = h; } });
    var cols = [];
    for (var h = ini; h <= fim; h++){
      var v = {}; v[k] = horas[h];
      cols.push({ rot: h + 'h', titulo: (h < 10 ? '0' : '') + h + ':00 – ' + (h < 10 ? '0' : '') + h + ':59',
                  chave: h % 3 === 0, v: v });
    }
    return cols;
  }

  // ---- Barras horizontais ------------------------------------------------------
  // itens: [{rot, sub, v:{k:n}, extra}]; series: [{k, nome, cls}]
  function barrasHTML(itens, series, opts){
    opts = opts || {};
    if (!itens.length) return vazioHTML(opts.vazio || 'Sem dados no período.');
    var max = 0;
    itens.forEach(function(it){
      it.total = series.reduce(function(s, se){ return s + (it.v[se.k] || 0); }, 0);
      if (it.total > max) max = it.total;
    });
    var soma = itens.reduce(function(s, it){ return s + it.total; }, 0);
    return legendaHTML(series) + '<div class="hb">' + itens.map(function(it){
      var linhas = [it.rot];
      series.forEach(function(se){
        linhas.push((series.length > 1 ? '@' + se.cls + '|' : '') + fmtNum(it.v[se.k] || 0, 0) + ' ' +
                    (se.um ? ((it.v[se.k] || 0) === 1 ? se.um : se.varios) : se.nome.toLowerCase()));
      });
      if (opts.pct && soma) linhas.push(fmtPct(it.total / soma) + ' do total');
      var segs = series.map(function(se){
        var n = it.v[se.k] || 0;
        return n ? '<i class="' + se.cls + '" style="width:' + (n / max * 100).toFixed(2) + '%"></i>' : '';
      }).join('');
      return '<div class="hb-lin"' + tipAttr(linhas) + '>'
        + '<span class="hb-rot"><span class="n">' + esc(it.rot) + '</span>'
        + (it.sub ? '<span class="s">' + esc(it.sub) + '</span>' : '') + '</span>'
        + '<span class="hb-trilho" aria-hidden="true">' + segs + '</span>'
        + '<span class="hb-val">' + fmtNum(it.total, 0)
        + (opts.pct && soma ? '<small>' + fmtPct(it.total / soma) + '</small>' : '') + '</span>'
        + '</div>';
    }).join('') + '</div>';
  }
  function itensDeContagem(obj, ordem){
    var chaves = Object.keys(obj);
    if (ordem) chaves.sort(function(a, b){
      var ia = ordem.indexOf(a), ib = ordem.indexOf(b);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || obj[b] - obj[a];
    });
    else chaves.sort(function(a, b){ return obj[b] - obj[a]; });
    return chaves.map(function(k){ return { rot: k, v: { n: obj[k] } }; });
  }

  // ---- Donut -----------------------------------------------------------------
  // itens: [{nome, v, cls, sub}]
  function donutHTML(itens, centroRot){
    var total = itens.reduce(function(s, it){ return s + it.v; }, 0);
    var R = 50, C = 2 * Math.PI * R, desloc = 0;
    var ativos = itens.filter(function(it){ return it.v > 0; });
    var gap = ativos.length > 1 ? 2.5 : 0;
    var segs = ativos.map(function(it){
      var len = Math.max(0.5, it.v / total * C - gap);
      // var() não vale em atributo de apresentação do SVG: a cor vai no style
      var c = '<circle class="seg" r="' + R + '" cx="60" cy="60" style="stroke:var(--' +
        (it.cls === 's-plan' ? 'cor-planner' : it.cls === 's-aud' ? 'cor-auditor' : 'cor-plataforma') + ')"' +
        ' stroke-dasharray="' + len.toFixed(2) + ' ' + (C - len).toFixed(2) + '" stroke-dashoffset="' + (-desloc).toFixed(2) + '"' +
        tipAttr([it.nome, fmtNum(it.v, 0) + ' (' + fmtPct(it.v / total) + ')'].concat(it.sub ? [it.sub] : [])) + '></circle>';
      desloc += it.v / total * C;
      return c;
    }).join('');
    var leg = itens.map(function(it){
      return '<div class="lin"><span class="sw ' + it.cls + '"></span><span class="n">' + esc(it.nome) + '</span>'
        + '<span class="v">' + fmtNum(it.v, 0) + (total ? ' <small style="color:var(--ink3);font-weight:600">' + fmtPct(it.v / total) + '</small>' : '') + '</span>'
        + (it.sub ? '<span class="s">' + esc(it.sub) + '</span>' : '') + '</div>';
    }).join('');
    return '<div class="donut-box"><div class="donut" role="img" aria-label="' +
      esc(itens.map(function(it){ return it.nome + ': ' + fmtNum(it.v, 0); }).join(', ')) + '">'
      + '<svg viewBox="0 0 120 120"><circle class="trilho" r="' + R + '" cx="60" cy="60"></circle>' + segs + '</svg>'
      + '<div class="centro"><b>' + fmtNum(total, 0) + '</b><span>' + esc(centroRot) + '</span></div></div>'
      + '<div class="donut-leg">' + leg + '</div></div>';
  }

  // ---- Satisfação por ferramenta -------------------------------------------------
  function satisfacaoHTML(lista){
    var temVoto = lista.some(function(s){ return s.like + s.dislike > 0; });
    if (!temVoto) return vazioHTML('Sem feedbacks no período.');
    return '<div class="sat">' + lista.map(function(s){
      var votos = s.like + s.dislike, pct = votos ? s.like / votos : 0;
      return '<div class="sat-lin"' + tipAttr([s.nome, '@s-ok|' + s.like + ' 👍 gostou', '@s-erro|' + s.dislike + ' 👎 não gostou']) + '>'
        + '<div class="cab"><span class="n"><span class="sw ' + s.cls + '"></span>' + esc(s.nome) + '</span>'
        + '<span class="pct ' + (votos ? tom(pct, 0.7, 0.4) : '') + '">' + (votos ? fmtPct(pct) : '—') + '</span></div>'
        + '<div class="hb-trilho" aria-hidden="true">'
        + (s.like ? '<i class="s-ok" style="width:' + (pct * 100).toFixed(2) + '%"></i>' : '')
        + (s.dislike ? '<i class="s-erro" style="width:' + ((1 - pct) * 100).toFixed(2) + '%"></i>' : '')
        + '</div>'
        + '<div class="rod">👍 ' + s.like + ' · 👎 ' + s.dislike + (votos ? '' : ' · sem votos') + '</div></div>';
    }).join('') + '</div>';
  }

  // ---- Listas: feedbacks e erros ----------------------------------------------------
  function feedbacksHTML(itens, comFerramenta){
    if (!itens.length) return vazioHTML('Sem feedbacks no período.');
    return '<div class="lista">' + itens.slice(0, 6).map(function(ev){
      var like = ev.voto === 'like', ferr = ferramentaDe(ev);
      var coment = String(ev.comentario || '').trim();
      return '<div class="li">'
        + '<span class="li-ico ' + (like ? 'like' : 'dislike') + '" role="img" aria-label="' + (like ? 'Gostou' : 'Não gostou') + '">' + (like ? '👍' : '👎') + '</span>'
        + '<div class="li-corpo"><div class="li-cab"><span class="who" title="' + esc(ev.email || '') + '">' + esc(ev.nome || ev.email || '—') + '</span>'
        + (comFerramenta ? '<span class="tag-ferr ' + ferr + '">' + esc(FERRAMENTAS[ferr].nome) + '</span>' : '')
        + '<span class="qd">' + esc(fmtDiaHora(ev.quando)) + '</span></div>'
        + '<div class="li-txt' + (coment ? '' : ' mudo') + '"' + (coment ? ' title="' + esc(coment) + '"' : '') + '>' +
          esc(coment || 'Sem comentário') + '</div></div></div>';
    }).join('') + '</div>';
  }
  function errosHTML(itens){
    if (!itens.length) return vazioHTML('Nenhum erro no período.');
    return '<div class="lista">' + itens.slice(0, 6).map(function(ev){
      var msg = String(ev.erro || ev.motivo || '').trim();
      return '<div class="li"><span class="li-ico erro" aria-hidden="true">!</span>'
        + '<div class="li-corpo"><div class="li-cab"><span class="who" title="' + esc(ev.email || '') + '">' + esc(ev.nome || ev.email || '—') + '</span>'
        + '<span class="acao erro">' + esc(rotuloAcao(ev.acao)) + '</span>'
        + '<span class="qd">' + esc(fmtDiaHora(ev.quando)) + '</span></div>'
        + '<div class="li-txt' + (msg ? '' : ' mudo') + '"' + (msg ? ' title="' + esc(msg) + '"' : '') + '>' + esc(msg || 'Sem mensagem de erro') + '</div></div></div>';
    }).join('') + '</div>';
  }
  function linkTrilha(filtro, texto){
    return '<button type="button" class="link-mini" data-trilha="' + esc(JSON.stringify(filtro)) + '">' + esc(texto) + ' &rarr;</button>';
  }

  // ---- Paginação ------------------------------------------------------------------
  function paginas(atual, total){
    // 1 … 4 5 6 … 20 (sempre a 1ª, a última e duas vizinhas da atual)
    var set = {}, lista = [];
    [1, total, atual - 1, atual, atual + 1].forEach(function(p){ if (p >= 1 && p <= total) set[p] = 1; });
    if (atual <= 3) [2, 3, 4].forEach(function(p){ if (p <= total) set[p] = 1; });
    if (atual >= total - 2) [total - 1, total - 2, total - 3].forEach(function(p){ if (p >= 1) set[p] = 1; });
    var nums = Object.keys(set).map(Number).sort(function(a, b){ return a - b; });
    nums.forEach(function(p, i){
      if (i && p - nums[i - 1] > 1) lista.push(0);
      lista.push(p);
    });
    return lista;
  }
  function pagerHTML(id, total, st, tamanhos, unidade){
    var nPags = Math.max(1, Math.ceil(total / st.tam));
    if (st.pag > nPags) st.pag = nPags;
    var ini = total ? (st.pag - 1) * st.tam + 1 : 0, fim = Math.min(total, st.pag * st.tam);
    var botoes = '<button type="button" data-pager="' + id + '" data-pag="' + (st.pag - 1) + '" data-foco="pg:' + id + ':ant"' +
      (st.pag <= 1 ? ' disabled' : '') + ' aria-label="Página anterior">&lsaquo;</button>';
    paginas(st.pag, nPags).forEach(function(p){
      if (!p){ botoes += '<span class="gap" aria-hidden="true">…</span>'; return; }
      botoes += '<button type="button" data-pager="' + id + '" data-pag="' + p + '" data-foco="pg:' + id + ':' + p + '"' +
        (p === st.pag ? ' aria-current="page"' : '') + ' aria-label="Página ' + p + '">' + p + '</button>';
    });
    botoes += '<button type="button" data-pager="' + id + '" data-pag="' + (st.pag + 1) + '" data-foco="pg:' + id + ':prox"' +
      (st.pag >= nPags ? ' disabled' : '') + ' aria-label="Próxima página">&rsaquo;</button>';
    return '<nav class="pager" aria-label="Paginação">'
      + '<span class="total">Mostrando <b>' + fmtNum(ini, 0) + '–' + fmtNum(fim, 0) + '</b> de <b>' + fmtNum(total, 0) + '</b> ' + esc(unidade) + '</span>'
      + '<label>Por página <select data-pager-tam="' + id + '" data-foco="tam:' + id + '">' + tamanhos.map(function(t){
        return '<option value="' + t + '"' + (t === st.tam ? ' selected' : '') + '>' + t + '</option>';
      }).join('') + '</select></label>'
      + (nPags > 1 ? '<span class="pags">' + botoes + '</span>' : '')
      + '</nav>';
  }

  // ---- Tabela de usuários (ordenável + paginada) -----------------------------------
  // cols: [{k, titulo, num, valor(u) -> número p/ ordenar, html(u)}]
  function tabelaUsuariosHTML(id, titulo, linhas, cols){
    var st = tabelas[id];
    var col = cols.filter(function(c){ return c.k === st.ordem; })[0] || cols[1];
    linhas = linhas.slice().sort(function(a, b){
      var va = col.valor(a), vb = col.valor(b);
      var r = typeof va === 'string' ? String(va).localeCompare(String(vb), 'pt-BR') : (va - vb);
      return r * st.dir || String(a.nome || a.email).localeCompare(String(b.nome || b.email), 'pt-BR');
    });
    var pager = pagerHTML(id, linhas.length, st, [10, 25, 50], linhas.length === 1 ? 'usuário' : 'usuários');
    var pagina = linhas.slice((st.pag - 1) * st.tam, st.pag * st.tam);
    var cab = cols.map(function(c){
      var ativo = c.k === col.k;
      return '<th scope="col"' + (c.num ? ' class="n"' : '') +
        (ativo ? ' aria-sort="' + (st.dir < 0 ? 'descending' : 'ascending') + '"' : '') + '>'
        + '<button type="button" data-ordenar="' + id + ':' + c.k + '" data-foco="ord:' + id + ':' + c.k + '">' + esc(c.titulo)
        + '<span class="seta" aria-hidden="true">' + (ativo ? (st.dir < 0 ? '▼' : '▲') : '▼') + '</span></button></th>';
    }).join('');
    var corpo = pagina.map(function(u){
      return '<tr>' + cols.map(function(c){
        return '<td' + (c.num ? ' class="n"' : c.k === 'nome' ? ' class="usuario"' : '') + '>' + c.html(u) + '</td>';
      }).join('') + '</tr>';
    }).join('');
    return '<section class="tab-box" aria-label="' + esc(titulo) + '">'
      + '<div class="card-cab"><div class="tit"><h2>' + esc(titulo) + '</h2>'
      + '<div class="info">Clique no cabeçalho para ordenar</div></div></div>'
      + (linhas.length
        ? '<div class="tab-scroll"><table class="tab-usuarios"><thead><tr>' + cab + '</tr></thead><tbody>' + corpo + '</tbody></table></div>' + pager
        : '<div class="tab-vazio">Nenhum usuário com uso no período.</div>')
      + '</section>';
  }
  function colUsuario(){
    return { k: 'nome', titulo: 'Usuário', valor: function(u){ return semAcento(u.nome || u.email); },
      html: function(u){ return '<div class="nome">' + esc(u.nome || '—') + '</div><div class="email">' + esc(u.email) + '</div>'; } };
  }
  function colNum(k, titulo, f, fmt){
    return { k: k, titulo: titulo, num: true, valor: f,
      html: function(u){ var v = f(u); return fmt ? fmt(v, u) : fmtNum(v, 0); } };
  }
  function fmtTaxa(ok, erro){
    var t = ok + erro;
    if (!t) return '<span style="color:var(--ink3)">—</span>';
    var p = ok / t;
    return '<span class="pct-' + tom(p, 0.9, 0.7) + '">' + fmtPct(p) + '</span>';
  }
  function fmtSat(like, dis){
    var t = like + dis;
    if (!t) return '<span style="color:var(--ink3)">—</span>';
    var p = like / t;
    return '<span class="pct-' + tom(p, 0.7, 0.4) + '" title="👍 ' + like + ' · 👎 ' + dis + '">' + fmtPct(p) + '</span>' +
      ' <span style="color:var(--ink3);font-size:11.5px">(' + t + ')</span>';
  }
  function fmtUlt(iso){ return iso ? esc(fmtDiaHora(iso)) : '<span style="color:var(--ink3)">—</span>'; }
  function maxStr(a, b){ return a > b ? a : b; }

  // ---- Visão geral ---------------------------------------------------------------------
  function htmlGeral(A){
    var aud = A.aud, plan = A.plan;
    var uso = aud.ok + plan.ok;
    var ativos = usuariosCom(A, function(u){ return u.aud + u.plan + u.perg > 0; });
    var nAud = usuariosCom(A, function(u){ return u.aud > 0; }).length;
    var nPlan = usuariosCom(A, function(u){ return u.plan + u.perg > 0; }).length;
    var likes = aud.like + plan.like, votos = likes + aud.dislike + plan.dislike;
    var sat = votos ? likes / votos : 0;
    var okTot = aud.ok + plan.ok + A.perg, errTot = aud.erro + plan.erro;
    var taxa = okTot + errTot ? okTot / (okTot + errTot) : 0;

    var minAud = aud.ok * poupAud(), minPlan = plan.ok * poupPlan();
    var tokE = aud.tokE + plan.tokE, tokS = aud.tokS + plan.tokS, temTok = aud.temTok || plan.temTok;
    var est = (aud.est || plan.est) ? '~' : '';
    var usd = custoUsd(tokE, tokS);

    var kpis = kpisHTML([
      { rot: 'Uso total', num: fmtNum(uso, 0),
        det: '<b>' + fmtNum(aud.ok, 0) + '</b> auditorias · <b>' + fmtNum(plan.ok, 0) + '</b> análises',
        dica: 'Auditorias concluídas + análises concluídas no Planner (perguntas de acompanhamento não contam).' },
      { rot: 'Usuários ativos', num: fmtNum(ativos.length, 0),
        det: '<b>' + fmtNum(recorrentes(ativos), 0) + '</b> recorrentes (2+ dias)',
        dica: 'Pessoas com ao menos uma auditoria, análise ou pergunta no período.\nAuditor: ' + nAud + ' · Planner: ' + nPlan },
      { rot: 'Satisfação', num: votos ? fmtPct(sat) : '—', tom: votos ? tom(sat, 0.7, 0.4) : 'mudo',
        medidor: votos ? sat : null, det: votos ? '👍 ' + likes + ' · 👎 ' + (votos - likes) : 'sem votos no período',
        dica: 'Voto líquido: cada resposta conta uma vez e vale o voto mais recente.' },
      { rot: 'Tempo economizado', num: (minAud + minPlan) > 0 ? '~' + fmtDuracao((minAud + minPlan) * 60) : '—',
        tom: (minAud + minPlan) > 0 ? '' : 'mudo',
        det: (minAud + minPlan) > 0 ? 'Auditor ' + fmtDuracao(minAud * 60) + ' · Planner ' + fmtDuracao(minPlan * 60) : 'sem estimativa configurada',
        dica: 'Estimativa do time (tagauditor/config.py):\nAuditor: ' + fmtDuracao(baselineManualMin * 60) + ' → ' +
          fmtDuracao(baselineFerramentaMin * 60) + ' por mapa\nPlanner: ' + fmtDuracao(baselinePlannerManualMin * 60) + ' → ' +
          fmtDuracao(baselinePlannerFerramentaMin * 60) + ' por análise' },
      { rot: 'Custo estimado', num: (temTok && temPreco()) ? est + fmtDinheiro(usd * cotacaoDolar) : '—',
        tom: (temTok && temPreco()) ? '' : 'mudo',
        det: !temPreco() ? 'configure o preço no config.py' : temTok ? fmtUsd(usd) + ' · ' + fmtCompacto(tokE + tokS) + ' tokens' : 'sem tokens no período',
        dica: 'Tokens de entrada: ' + fmtNum(tokE, 0) + '\nTokens de saída: ' + fmtNum(tokS, 0) +
          (est ? '\n~ = parte dos tokens é estimativa (a API não devolveu o uso)' : '') },
      { rot: 'Taxa de sucesso', num: okTot + errTot ? fmtPct(taxa) : '—', tom: okTot + errTot ? tom(taxa, 0.9, 0.7) : 'mudo',
        medidor: okTot + errTot ? taxa : null,
        det: '<b>' + fmtNum(errTot, 0) + '</b> ' + (errTot === 1 ? 'erro' : 'erros') + ' · ' + plural(A.logins, 'login', 'logins'),
        dica: 'Auditorias, análises e perguntas concluídas sobre o total de tentativas.' }
    ]);

    var series = [
      { k: 'aud', nome: 'Auditor', cls: 's-aud', um: 'auditoria', varios: 'auditorias' },
      { k: 'plan', nome: 'Planner', cls: 's-plan', um: 'análise', varios: 'análises' }
    ];
    var cols = colunasPorDia(A, ['aud', 'plan']);
    var diasComUso = cols.filter(function(c){ return c.v.aud + c.v.plan > 0; }).length;
    var porDia = cardColunas({
      id: 'gDiaGeral', titulo: 'Uso por dia', colunas: cols, series: series, rotuloCol: 'Dia',
      info: diasComUso ? plural(uso, 'uso', 'usos') + ' em ' + plural(diasComUso, 'dia', 'dias') + ' · média de ' +
        fmtNum(uso / diasComUso, 1) + ' por dia com uso' : '',
      aria: 'Uso por dia: auditorias e análises', vazio: 'Sem auditorias nem análises com esses filtros.',
      altura: 210
    });
    var divisao = cardHTML({ titulo: 'Divisão por ferramenta', info: 'Usos concluídos no período',
      corpo: uso ? donutHTML([
        { nome: 'Auditor', v: aud.ok, cls: 's-aud', sub: plural(nAud, 'usuário', 'usuários') },
        { nome: 'Planner', v: plan.ok, cls: 's-plan', sub: plural(nPlan, 'usuário', 'usuários') + (A.perg ? ' · ' + plural(A.perg, 'pergunta', 'perguntas') : '') }
      ], 'usos') : vazioHTML('Sem uso no período.') });

    var top = ativos.slice().sort(function(a, b){
      return (b.aud + b.plan) - (a.aud + a.plan) || b.perg - a.perg;
    }).slice(0, 7).map(function(u){
      return { rot: u.nome || u.email, sub: u.email, v: { aud: u.aud, plan: u.plan } };
    }).filter(function(it){ return it.v.aud + it.v.plan > 0; });
    var cTop = cardHTML({ titulo: 'Usuários mais ativos', info: 'Auditorias + análises concluídas',
      corpo: barrasHTML(top, series, { vazio: 'Sem uso no período.' }) });
    var cSat = cardHTML({ titulo: 'Satisfação por ferramenta', info: 'Voto líquido (👍 / 👎)',
      corpo: satisfacaoHTML([
        { nome: 'Auditor', cls: 's-aud', like: aud.like, dislike: aud.dislike },
        { nome: 'Planner', cls: 's-plan', like: plan.like, dislike: plan.dislike }
      ]) });
    var fbs = aud.fb.concat(plan.fb).sort(function(a, b){ return a.quando < b.quando ? 1 : a.quando > b.quando ? -1 : 0; });
    var cFb = cardHTML({ titulo: 'Feedbacks recentes', acao: fbs.length ? linkTrilha({ acao: 'feedback' }, 'Ver todos') : '',
      corpo: feedbacksHTML(fbs, true) });

    var usuarios = usuariosCom(A, function(u){ return u.aud + u.plan + u.perg + u.audErro + u.planErro > 0; });
    var tab = tabelaUsuariosHTML('usu-geral', 'Usuários', usuarios, [
      colUsuario(),
      colNum('uso', 'Uso total', function(u){ return u.aud + u.plan; }),
      colNum('aud', 'Auditorias', function(u){ return u.aud; }),
      colNum('plan', 'Análises', function(u){ return u.plan; }),
      colNum('erros', 'Erros', function(u){ return u.audErro + u.planErro; }),
      colNum('tok', 'Tokens', function(u){ return u.tokAud + u.tokPlan; }, function(v){ return fmtCompacto(v); }),
      colNum('sat', 'Satisfação', function(u){ var t = u.likeAud + u.disAud + u.likePlan + u.disPlan; return t ? (u.likeAud + u.likePlan) / t : -1; },
        function(v, u){ return fmtSat(u.likeAud + u.likePlan, u.disAud + u.disPlan); }),
      colNum('ult', 'Última atividade', function(u){ return maxStr(u.ultAud, u.ultPlan); }, function(v){ return fmtUlt(v); })
    ]);

    return kpis
      + '<div class="grade g-2-1">' + porDia + divisao + '</div>'
      + '<div class="grade g-3">' + cTop + cSat + cFb + '</div>'
      + tab;
  }

  // ---- Visão por ferramenta (Auditor / Planner) ------------------------------------------
  function faixasDuracao(durs){
    var faixas = [['Até 15 s', 15], ['15 a 30 s', 30], ['30 s a 1 min', 60], ['1 a 2 min', 120], ['Mais de 2 min', Infinity]];
    var cont = faixas.map(function(){ return 0; });
    durs.forEach(function(d){
      for (var i = 0; i < faixas.length; i++){ if (d <= faixas[i][1]){ cont[i]++; break; } }
    });
    return faixas.map(function(f, i){ return { rot: f[0], v: { n: cont[i] } }; })
      .filter(function(it, i, arr){
        // corta as faixas vazias das pontas (mantém as do meio: a escala segue contínua)
        var primeiro = -1, ultimo = -1;
        arr.forEach(function(x, j){ if (x.v.n){ if (primeiro < 0) primeiro = j; ultimo = j; } });
        return primeiro >= 0 && i >= primeiro && i <= ultimo;
      });
  }
  function mediana(lista){
    if (!lista.length) return 0;
    var s = lista.slice().sort(function(a, b){ return a - b; }), m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }

  function htmlFerramenta(A, qual){
    var ehAud = qual === 'auditor';
    var F = ehAud ? A.aud : A.plan, cls = ehAud ? 's-aud' : 's-plan', cor = ehAud ? 'aud' : 'plan';
    var nome = ehAud ? 'Auditor' : 'Planner';
    var um = ehAud ? 'auditoria' : 'análise', varios = ehAud ? 'auditorias' : 'análises';
    var okTot = F.ok + (ehAud ? 0 : A.perg), taxa = okTot + F.erro ? okTot / (okTot + F.erro) : 0;
    var ativos = usuariosCom(A, ehAud ? function(u){ return u.aud > 0; } : function(u){ return u.plan + u.perg > 0; });
    var votos = F.like + F.dislike, sat = votos ? F.like / votos : 0;
    var minutos = F.ok * (ehAud ? poupAud() : poupPlan());
    var est = F.est ? '~' : '', usd = custoUsd(F.tokE, F.tokS);
    var media = F.nDur ? F.dur / F.nDur : 0;

    var kpis = kpisHTML([
      { rot: ehAud ? 'Auditorias concluídas' : 'Análises concluídas', cor: cor, num: fmtNum(F.ok, 0),
        det: (ehAud ? '' : '<span class="l"><b>' + fmtNum(A.perg, 0) + '</b> ' + (A.perg === 1 ? 'pergunta' : 'perguntas') + '</span>') +
          '<span class="l">' + (minutos ? '~' + fmtDuracao(minutos * 60) + ' economizadas' : 'sem estimativa de tempo') + '</span>',
        dica: ehAud ? 'Auditorias com status diferente de erro.' : 'Análises concluídas (modo análise). Perguntas de acompanhamento contam à parte.' },
      { rot: 'Usuários', cor: cor, num: fmtNum(ativos.length, 0),
        det: '<b>' + fmtNum(recorrentes(ativos), 0) + '</b> recorrentes (2+ dias)' },
      { rot: 'Taxa de sucesso', cor: cor, num: okTot + F.erro ? fmtPct(taxa) : '—', tom: okTot + F.erro ? tom(taxa, 0.9, 0.7) : 'mudo',
        medidor: okTot + F.erro ? taxa : null,
        det: '<b>' + fmtNum(F.erro, 0) + '</b> ' + (F.erro === 1 ? 'erro' : 'erros') + ' em ' + plural(okTot + F.erro, 'tentativa', 'tentativas') },
      { rot: 'Tempo de resposta', cor: cor, num: F.nDur ? fmtSegundos(media) : '—', tom: F.nDur ? '' : 'mudo',
        det: F.nDur ? 'média · mediana ' + fmtSegundos(mediana(F.durs)) : 'sem medições',
        dica: 'Duração das ' + varios + ' concluídas (do envio à resposta da IA).' },
      { rot: 'Satisfação', cor: cor, num: votos ? fmtPct(sat) : '—', tom: votos ? tom(sat, 0.7, 0.4) : 'mudo',
        medidor: votos ? sat : null, det: votos ? '👍 ' + F.like + ' · 👎 ' + F.dislike : 'sem votos no período',
        dica: 'Voto líquido: cada resposta conta uma vez e vale o voto mais recente.' },
      { rot: 'Custo estimado', cor: cor, num: (F.temTok && temPreco()) ? est + fmtDinheiro(usd * cotacaoDolar) : '—',
        tom: (F.temTok && temPreco()) ? '' : 'mudo',
        det: !temPreco() ? 'configure o preço no config.py' : F.temTok
          ? fmtCompacto(F.tokE) + ' entrada · ' + fmtCompacto(F.tokS) + ' saída' : 'sem tokens no período',
        dica: fmtUsd(usd) + '\nTokens de entrada: ' + fmtNum(F.tokE, 0) + '\nTokens de saída: ' + fmtNum(F.tokS, 0) +
          (F.ok ? '\nPor ' + um + ': ' + fmtDinheiro(usd * cotacaoDolar / F.ok) : '') }
    ]);

    var k = ehAud ? 'aud' : 'plan';
    var serie = [{ k: k, nome: ehAud ? 'Auditorias' : 'Análises', cls: cls, um: um, varios: varios }];
    var cols = colunasPorDia(A, [k]);
    var diasComUso = cols.filter(function(c){ return c.v[k] > 0; }).length;
    var porDia = cardColunas({
      id: 'gDia' + nome, titulo: (ehAud ? 'Auditorias' : 'Análises') + ' por dia', colunas: cols, series: serie, rotuloCol: 'Dia',
      info: diasComUso ? plural(F.ok, um, varios) + ' em ' + plural(diasComUso, 'dia', 'dias') + ' · média de ' +
        fmtNum(F.ok / diasComUso, 1) + ' por dia com uso' : '',
      aria: varios.charAt(0).toUpperCase() + varios.slice(1) + ' por dia', vazio: 'Sem ' + varios + ' com esses filtros.',
      altura: 210
    });

    var lado, cOrigem = '';
    var serieN = [{ k: 'n', nome: 'Total', cls: cls, um: ehAud ? 'auditoria' : 'análise', varios: varios }];
    if (ehAud){
      var entrada = [];
      if (A.anexo) entrada.push({ rot: 'Plano anexado (.json)', v: { n: A.anexo } });
      if (A.texto) entrada.push({ rot: 'Só mensagem', sub: 'pergunta ou plano colado no texto', v: { n: A.texto } });
      lado = cardHTML({ titulo: 'Como o plano chega', info: 'Auditorias concluídas por tipo de entrada',
        corpo: barrasHTML(entrada, serieN, { pct: true, vazio: 'Sem auditorias no período.' })
          + '<div class="sub-tit mt">Busca no Confluence</div>'
          + '<div class="mini" style="margin-bottom:0">'
          + '<div><b>' + fmtNum(A.confOk, 0) + '</b><span>Planos obtidos</span></div>'
          + '<div><b>' + fmtNum(A.confOpcoes, 0) + '</b><span>Escolha de anexo</span></div>'
          + '<div><b>' + fmtNum(A.confErro, 0) + '</b><span>Erros</span></div></div>' });
    } else {
      var canais = itensDeContagem(A.canais, ['Web', 'WebView', 'App nativo', 'Não informado']);
      var origens = itensDeContagem(A.origens);
      lado = cardHTML({ titulo: 'Resultado das análises', info: 'Análises concluídas no período',
        corpo: '<div class="mini">'
          + '<div' + tipAttr(['Telas analisadas', A.semImagem ? plural(A.semImagem, 'análise só pelas camadas do Figma', 'análises só pelas camadas do Figma') : 'todas com imagem']) +
            '><b>' + fmtNum(A.telas, 0) + '</b><span>Telas</span></div>'
          + '<div><b>' + fmtNum(A.obrig, 0) + '</b><span>Obrigatórios</span></div>'
          + '<div><b>' + fmtNum(A.opc, 0) + '</b><span>Opcionais</span></div></div>'
          + '<div class="sub-tit">Canal</div>' + barrasHTML(canais, serieN, { pct: true, vazio: 'Sem análises no período.' }) });
      cOrigem = cardHTML({ titulo: 'Origem das telas', info: 'De onde vieram as telas das análises',
        corpo: barrasHTML(origens, serieN, { pct: true, vazio: 'Sem análises no período.' })
          + '<div class="sub-tit mt">Busca no Figma</div>'
          + '<div class="mini" style="margin-bottom:0">'
          + '<div><b>' + fmtNum(A.figmaOk, 0) + '</b><span>Telas obtidas</span></div>'
          + '<div><b>' + fmtNum(A.figmaOpcoes, 0) + '</b><span>Escolha de frame</span></div>'
          + '<div><b>' + fmtNum(A.figmaErro, 0) + '</b><span>Erros</span></div></div>' });
    }

    var top = ativos.slice().sort(function(a, b){
      return ehAud ? b.aud - a.aud : (b.plan - a.plan || b.perg - a.perg);
    }).slice(0, 7).map(function(u){
      return { rot: u.nome || u.email, sub: u.email, v: { n: ehAud ? u.aud : u.plan } };
    }).filter(function(it){ return it.v.n > 0; });
    var cTop = cardHTML({ titulo: 'Usuários mais ativos', info: (ehAud ? 'Auditorias' : 'Análises') + ' concluídas',
      corpo: barrasHTML(top, serieN, { vazio: 'Sem uso no período.' }) });
    var cHora = cardColunas({
      id: 'gHora' + nome, titulo: 'Horário de uso', info: 'Por hora do dia (horário do servidor)',
      colunas: colunasPorHora(F.horas, k), series: serie, rotuloCol: 'Hora', altura: 150,
      aria: varios.charAt(0).toUpperCase() + varios.slice(1) + ' por hora do dia', vazio: 'Sem uso no período.'
    });
    var cDur = cardHTML({ titulo: 'Tempo de resposta', info: 'Quantas ' + varios + ' por faixa de duração',
      corpo: barrasHTML(faixasDuracao(F.durs), serieN, { pct: true, vazio: 'Sem medições no período.' }) });

    var fbs = F.fb;
    var cFb = cardHTML({ titulo: 'Feedbacks recentes', info: votos ? plural(votos, 'voto', 'votos') + ' no período' : '',
      acao: fbs.length ? linkTrilha({ ferramenta: qual, acao: 'feedback' }, 'Ver todos') : '', corpo: feedbacksHTML(fbs, false) });
    var cErr = cardHTML({ titulo: 'Erros recentes', info: F.erros.length ? plural(F.erros.length, 'erro', 'erros') + ' no período' : '',
      acao: F.erros.length ? linkTrilha({ ferramenta: qual, status: 'erro' }, 'Ver todos') : '', corpo: errosHTML(F.erros) });

    var cols2 = [colUsuario(),
      colNum('uso', ehAud ? 'Auditorias' : 'Análises', function(u){ return ehAud ? u.aud : u.plan; })];
    if (!ehAud) cols2.push(colNum('perg', 'Perguntas', function(u){ return u.perg; }));
    cols2.push(
      colNum('erros', 'Erros', function(u){ return ehAud ? u.audErro : u.planErro; }),
      colNum('taxa', 'Sucesso', function(u){
        var ok = ehAud ? u.aud : u.plan + u.perg, er = ehAud ? u.audErro : u.planErro;
        return ok + er ? ok / (ok + er) : -1;
      }, function(v, u){ return ehAud ? fmtTaxa(u.aud, u.audErro) : fmtTaxa(u.plan + u.perg, u.planErro); }),
      colNum('dur', 'Tempo médio', function(u){
        var n = ehAud ? u.nDurAud : u.nDurPlan; return n ? (ehAud ? u.durAud : u.durPlan) / n : -1;
      }, function(v){ return v >= 0 ? fmtSegundos(v) : '<span style="color:var(--ink3)">—</span>'; }),
      colNum('tok', 'Tokens', function(u){ return ehAud ? u.tokAud : u.tokPlan; }, function(v){ return fmtCompacto(v); }),
      colNum('sat', 'Satisfação', function(u){
        var l = ehAud ? u.likeAud : u.likePlan, d = ehAud ? u.disAud : u.disPlan; return l + d ? l / (l + d) : -1;
      }, function(v, u){ return ehAud ? fmtSat(u.likeAud, u.disAud) : fmtSat(u.likePlan, u.disPlan); }),
      colNum('ult', 'Última atividade', function(u){ return ehAud ? u.ultAud : u.ultPlan; }, function(v){ return fmtUlt(v); })
    );
    var usuarios = usuariosCom(A, ehAud ? function(u){ return u.aud + u.audErro > 0; }
                                        : function(u){ return u.plan + u.perg + u.planErro > 0; });
    var tab = tabelaUsuariosHTML('usu-' + qual, 'Usuários do ' + nome, usuarios, cols2);

    return kpis
      + '<div class="grade g-2-1">' + porDia + lado + '</div>'
      + '<div class="grade g-3">' + cTop + cHora + cDur + '</div>'
      + (ehAud ? '<div class="grade g-1-1">' + cFb + cErr + '</div>'
               : '<div class="grade g-3">' + cOrigem + cFb + cErr + '</div>')
      + tab;
  }

  // ---- Trilha de eventos -----------------------------------------------------------------
  // Valor do chip legível: booleano vira sim/não, número ganha separador
  // (duração em s/min, bytes em KB/MB); objeto aparece como JSON.
  function fmtValor(k, v){
    if (typeof v === 'boolean') return v ? 'sim' : 'não';
    if (typeof v === 'number'){
      if (!isFinite(v) || proprio(CHAVES_CODIGO, k)) return String(v);
      if (k === 'duracao_s') return fmtSegundos(v);
      if (/^bytes/.test(k)) return fmtBytes(v);
      return fmtNum(v);
    }
    if (typeof v === 'string'){
      if (CHAVES_NUM.test(k) && v.trim() !== '' && !isNaN(v)) return fmtNum(Number(v));
      if (proprio(CHAVES_ENUM, k) && proprio(VALORES_EXIBIDOS, v)) return VALORES_EXIBIDOS[v];
      return v;
    }
    try { return JSON.stringify(v); } catch (e) { return String(v); }
  }
  // Texto completo do chip no tooltip (o valor longo é cortado com "...")
  function tituloValor(k, v){
    if (typeof v === 'number' && isFinite(v)){
      if (k === 'duracao_s') return fmtNum(v) + ' segundos';
      if (/^bytes/.test(k)) return fmtNum(v, 0) + ' bytes';
    }
    return typeof v === 'string' ? v : fmtValor(k, v);
  }
  // Um chip de detalhe, com destaque para os campos que o analista mais lê:
  // voto (👍/👎), status (ok/erro), comentário e URLs.
  function chipDet(k, v){
    if (k === 'voto'){
      if (v === 'like') return '<span class="chip voto like">👍 Gostou</span>';
      if (v === 'dislike') return '<span class="chip voto dislike">👎 Não gostou</span>';
    }
    if (k === 'status'){
      var cls = v === 'ok' ? ' st-ok' : (v === 'erro' ? ' st-erro' : '');
      return '<span class="chip' + cls + '"><b>status:</b> ' + esc(fmtValor(k, v)) + '</span>';
    }
    var titulo = esc(k + ': ' + tituloValor(k, v));
    if (k === 'comentario'){
      return '<span class="chip coment" title="' + titulo + '"><b>comentário:</b> <span class="v">' + esc(v) + '</span></span>';
    }
    if (k === 'url' && /^https?:\/\//.test(String(v))){
      return '<span class="chip" title="' + titulo + '"><b>url:</b> <a href="' + esc(v) +
        '" target="_blank" rel="noopener noreferrer"><span class="v">' + esc(v) + '</span></a></span>';
    }
    return '<span class="chip" title="' + titulo + '"><b>' + esc(k) + ':</b> <span class="v">' +
      esc(fmtValor(k, v)) + '</span></span>';
  }
  function detalhesHTML(ev){
    var ferr = ferramentaDe(ev);
    var html = '<span class="chip ferr ' + ferr + '" title="Ferramenta">' + esc(FERRAMENTAS[ferr].nome) + '</span>';
    var chaves = Object.keys(ev).filter(function(k){
      var v = ev[k];
      return !proprio(CAMPOS_FIXOS, k) && v !== '' && v != null;
    });
    var posicao = {};
    chaves.forEach(function(k, i){ posicao[k] = i; });
    function peso(k){ var i = ORDEM_CHIPS.indexOf(k); return i === -1 ? ORDEM_CHIPS.length : i; }
    chaves.sort(function(a, b){ return (peso(a) - peso(b)) || (posicao[a] - posicao[b]); });
    chaves.forEach(function(k){ html += chipDet(k, ev[k]); });
    return html;
  }

  function filtradosEventos(){
    var email = selEmail.value, ferr = selFerramenta.value, acao = selAcao.value, status = selStatus.value;
    var termo = semAcento((busca.value || '').trim());
    return eventos.filter(function(ev, i){
      if (email && (ev.email || '') !== email) return false;
      if (ferr && ferramentaDe(ev) !== ferr) return false;
      if (acao && ev.acao !== acao) return false;
      if (status && classeAcao(ev) !== status) return false;
      if (termo && textosBusca[i].indexOf(termo) === -1) return false;
      return true;
    });
  }
  function renderEventos(){
    var lista = filtradosEventos(), st = tabelas.eventos;
    var pager = pagerHTML('eventos', lista.length, st, [25, 50, 100], lista.length === 1 ? 'evento' : 'eventos');
    var pagina = lista.slice((st.pag - 1) * st.tam, st.pag * st.tam);
    corpoTab.innerHTML = pagina.map(function(ev){
      var c = classeAcao(ev);
      return '<tr role="row">'
        + '<td role="cell" class="quando">' + fmtQuandoHTML(ev.quando) + '</td>'
        + '<td role="cell" class="c-acao"><span class="acao' + (c ? ' ' + c : '') + '">' + esc(rotuloAcao(ev.acao)) + '</span></td>'
        + '<td role="cell" class="usuario"><div class="nome">' + esc(ev.nome || '—') + '</div>'
        + '<div class="email">' + esc(ev.email || '') + '</div></td>'
        + '<td role="cell" class="c-det"><div class="det">' + detalhesHTML(ev) + '</div></td>'
        + '</tr>';
    }).join('');
    vazio.hidden = lista.length > 0;
    pagerEventos.innerHTML = lista.length ? pager : '';
    eventosInfo.textContent = fmtNum(lista.length, 0) + ' de ' + fmtNum(eventos.length, 0) +
      ' eventos carregados no mês · fonte: logs/atividade-<mês>.jsonl';
  }

  // ---- Filtros ---------------------------------------------------------------------
  function preencherSelect(sel, valores, rotulo){
    var atual = sel.value;
    sel.innerHTML = '';
    var opTodos = document.createElement('option');
    opTodos.value = ''; opTodos.textContent = rotulo;
    sel.appendChild(opTodos);
    valores.forEach(function(v){
      var op = document.createElement('option');
      op.value = v.valor; op.textContent = v.texto;
      sel.appendChild(op);
    });
    sel.value = atual;
    if (sel.selectedIndex < 0) sel.selectedIndex = 0;
  }
  // Ações do filtro "Ação": só as da ferramenta escolhida (com "Todas", todas).
  function popularAcoes(){
    var ferr = selFerramenta.value, acoes = {};
    eventos.forEach(function(ev){
      if (ev.acao && (!ferr || ferramentaDe(ev) === ferr)) acoes[ev.acao] = 1;
    });
    preencherSelect(selAcao, Object.keys(acoes).map(function(a){
      return { valor: a, texto: rotuloAcao(a) };
    }).sort(function(a, b){ return a.texto.localeCompare(b.texto, 'pt-BR'); }), 'Todas');
  }
  function popularFiltros(meses){
    preencherSelect(selMes, (meses || []).map(function(m){
      return { valor: m, texto: fmtMes(m) };
    }), 'Mês atual');
    var pessoas = {};
    eventos.forEach(function(ev){ if (ev.email && !pessoas[ev.email]) pessoas[ev.email] = ev.nome || ''; });
    preencherSelect(selEmail, Object.keys(pessoas).sort().map(function(e){
      return { valor: e, texto: e };
    }), 'Todos');
    popularAcoes();
  }
  function baseFiltrada(){
    var email = selEmail.value;
    return email ? eventos.filter(function(ev){ return (ev.email || '') === email; }) : eventos;
  }

  // ---- Render / rotas -------------------------------------------------------------------
  function vistaDoHash(){
    var h = String(location.hash || '').replace(/^#/, '');
    return proprio(VISTAS, h) ? h : 'geral';
  }
  function atualizarCabecalho(){
    var v = VISTAS[vistaAtual];
    vistaTitulo.textContent = v.titulo;
    var mes = selMes.options[selMes.selectedIndex];
    vistaSub.textContent = v.sub + (carregou && mes ? ' · ' + mes.textContent : '');
    document.title = v.titulo + ' · Painel administrativo | ' + nomePlataforma;
    Array.prototype.forEach.call(nav.querySelectorAll('a'), function(a){
      var on = a.getAttribute('data-vista') === vistaAtual;
      a.classList.toggle('on', on);
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
  }

  // Redesenha a visão atual. `foco` = data-foco do controle que estava com o
  // foco (paginação, ordenação, "Ver tabela"): ele volta ao lugar depois.
  function render(foco){
    esconderTip();
    atualizarCabecalho();
    if (!carregou) return;
    var ehTrilha = vistaAtual === 'eventos';
    vista.hidden = ehTrilha;
    vistaEventos.hidden = !ehTrilha;
    if (ehTrilha){
      renderEventos();
    } else {
      var A = agregar(baseFiltrada());
      vista.innerHTML = vistaAtual === 'geral' ? htmlGeral(A) : htmlFerramenta(A, vistaAtual);
      ajustarDensidade();
      ajustarNumeros();
    }
    if (foco){
      var alvo = document.querySelector('[data-foco="' + foco + '"]');
      if (!alvo || alvo.disabled){
        var m = /^pg:([^:]+):/.exec(foco);
        alvo = m ? document.querySelector('[data-pager="' + m[1] + '"][aria-current="page"]') : null;
      }
      if (alvo) alvo.focus();
    }
  }
  function irPara(vistaNova, porClique){
    vistaAtual = vistaNova;
    render();
    if (porClique){
      try { vistaTitulo.focus({ preventScroll: true }); } catch (e) { vistaTitulo.focus(); }
      window.scrollTo(0, 0);
    }
  }
  window.addEventListener('hashchange', function(){ irPara(vistaDoHash(), true); });

  // Atalhos "Ver todos" (feedbacks/erros) -> trilha já filtrada
  function abrirTrilha(filtro){
    selFerramenta.value = filtro.ferramenta || '';
    popularAcoes();
    selAcao.value = filtro.acao || '';
    selStatus.value = filtro.status || '';
    busca.value = '';
    tabelas.eventos.pag = 1;
    if (location.hash === '#eventos') irPara('eventos', true);
    else location.hash = '#eventos';
  }

  // Número que não cabe no tile diminui em vez de ser cortado com "..."
  function ajustarNumeros(){
    Array.prototype.forEach.call(vista.querySelectorAll('.kpi .num'), function(el){
      el.style.fontSize = '';
      if (!el.clientWidth) return;
      var tamanho = parseFloat(getComputedStyle(el).fontSize) || 26;
      while (el.scrollWidth > el.clientWidth && tamanho > 15){
        tamanho -= 1;
        el.style.fontSize = tamanho + 'px';
      }
    });
  }
  // Colunas estreitas demais para escrever todos os rótulos: só os "chave"
  // (dias 1, 5, 10... / horas 9h, 12h...) — o resto fica no tooltip e na tabela.
  function ajustarDensidade(){
    Array.prototype.forEach.call(vista.querySelectorAll('.cc'), function(cc){
      var n = +cc.getAttribute('data-n') || 1;
      var largura = cc.querySelector('.cc-cols').clientWidth;
      cc.classList.toggle('denso', largura / n < 24);
    });
  }

  // ---- Tooltip ---------------------------------------------------------------------------
  var tipDono = null;
  function tipHTML(texto){
    return String(texto).split('\n').map(function(lin, i){
      if (i === 0) return '<div class="t-tit">' + esc(lin) + '</div>';
      if (lin === '--') return '<div class="t-sep"></div>';
      var m = /^@([\w-]+)\|(.*)$/.exec(lin);
      if (m) return '<div class="t-lin"><span class="k ' + esc(m[1]) + '"></span>' + negritoNumero(m[2]) + '</div>';
      return '<div class="t-lin">' + negritoNumero(lin) + '</div>';
    }).join('');
  }
  // "12 auditorias" -> "<b>12</b> auditorias"
  function negritoNumero(s){
    var m = /^([\d.,%~]+)(.*)$/.exec(s);
    return m ? '<b>' + esc(m[1]) + '</b>' + esc(m[2]) : esc(s);
  }
  function mostrarTip(el){
    var texto = el.getAttribute('data-tip');
    if (!texto) return;
    tipDono = el;
    tip.innerHTML = tipHTML(texto);
    tip.hidden = false;
    var alvo = el.classList.contains('cc-col') ? (el.querySelector('.cc-pilha') || el.firstChild) : el;
    var r = alvo.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight, folga = 10;
    var x = r.left + r.width / 2 - w / 2;
    x = Math.max(8, Math.min(x, window.innerWidth - w - 8));
    var y = r.top - h - folga;
    if (y < 8) y = r.bottom + folga;
    tip.style.left = Math.round(x) + 'px';
    tip.style.top = Math.round(y) + 'px';
  }
  function esconderTip(){
    tipDono = null;
    tip.hidden = true;
    var ativa = vista.querySelector('.cc-col.ativa');
    if (ativa) ativa.classList.remove('ativa');
  }
  function acharTip(el){
    while (el && el !== document){
      if (el.getAttribute && el.getAttribute('data-tip') != null) return el;
      el = el.parentNode;
    }
    return null;
  }
  vista.addEventListener('mouseover', function(e){
    var el = acharTip(e.target);
    if (el && el !== tipDono) mostrarTip(el);
    else if (!el && tipDono && document.activeElement !== tipDono.closest('.cc')) esconderTip();
  });
  vista.addEventListener('mouseleave', function(){
    if (!(document.activeElement && document.activeElement.classList.contains('cc'))) esconderTip();
  });
  // toque (celular): tocar mostra o tooltip
  vista.addEventListener('click', function(e){
    var el = acharTip(e.target);
    if (el) mostrarTip(el);
  });
  window.addEventListener('scroll', esconderTip, { passive: true });

  // Teclado no gráfico de colunas: um único Tab; as setas andam pelas colunas
  function ativarColuna(cc, i){
    var cols = cc.querySelectorAll('.cc-col');
    if (!cols[i]) return;
    var antiga = cc.querySelector('.cc-col.ativa');
    if (antiga) antiga.classList.remove('ativa');
    cols[i].classList.add('ativa');
    cc.setAttribute('data-ativa', String(i));
    mostrarTip(cols[i]);
    leitor.textContent = String(cols[i].getAttribute('data-tip') || '').replace(/@[\w-]+\|/g, '').replace(/\n--/g, '').replace(/\n/g, ', ');
  }
  vista.addEventListener('keydown', function(e){
    var cc = e.target.classList && e.target.classList.contains('cc') ? e.target : null;
    if (!cc) return;
    var n = cc.querySelectorAll('.cc-col').length, i = +cc.getAttribute('data-ativa');
    if (isNaN(i) || cc.getAttribute('data-ativa') == null) i = -1;
    if (!n) return;
    if (e.key === 'ArrowRight') i = i < 0 ? 0 : Math.min(n - 1, i + 1);
    else if (e.key === 'ArrowLeft') i = i < 0 ? n - 1 : Math.max(0, i - 1);
    else if (e.key === 'Home') i = 0;
    else if (e.key === 'End') i = n - 1;
    else if (e.key === 'Escape'){ esconderTip(); cc.removeAttribute('data-ativa'); return; }
    else return;
    e.preventDefault();
    ativarColuna(cc, i);
  });
  // ao receber o foco, começa pela coluna mais recente com valor
  vista.addEventListener('focusin', function(e){
    var cc = e.target.classList && e.target.classList.contains('cc') ? e.target : null;
    if (!cc) return;
    var cols = cc.querySelectorAll('.cc-col');
    for (var i = cols.length - 1; i >= 0; i--){
      if (cols[i].querySelector('.cc-pilha')){ ativarColuna(cc, i); return; }
    }
  });
  vista.addEventListener('focusout', function(e){
    if (e.target.classList && e.target.classList.contains('cc')){
      e.target.removeAttribute('data-ativa');
      esconderTip();
    }
  });

  // Paginação, ordenação, "Ver tabela" e "Ver todos" (delegados)
  document.addEventListener('click', function(e){
    var b = e.target.closest ? e.target.closest('button') : null;
    if (!b || b.disabled) return;
    var foco = b.getAttribute('data-foco');
    if (b.hasAttribute('data-pager')){
      var id = b.getAttribute('data-pager');
      tabelas[id].pag = +b.getAttribute('data-pag');
      render(foco);
      // página nova: se o topo da tabela saiu da tela, volta para ele
      var tb = document.activeElement && document.activeElement.closest ? document.activeElement.closest('.tab-box') : null;
      if (tb && tb.getBoundingClientRect().top < 0) tb.scrollIntoView({ block: 'start' });
    } else if (b.hasAttribute('data-ordenar')){
      var p = b.getAttribute('data-ordenar').split(':'), st = tabelas[p[0]];
      if (st.ordem === p[1]) st.dir = -st.dir;
      else { st.ordem = p[1]; st.dir = p[1] === 'nome' ? 1 : -1; }
      st.pag = 1;
      render(foco);
    } else if (b.hasAttribute('data-alternar')){
      var g = b.getAttribute('data-alternar');
      modoTabela[g] = !modoTabela[g];
      render(foco);
    } else if (b.hasAttribute('data-trilha')){
      try { abrirTrilha(JSON.parse(b.getAttribute('data-trilha'))); } catch (err) {}
    }
  });
  document.addEventListener('change', function(e){
    var s = e.target;
    if (s.hasAttribute && s.hasAttribute('data-pager-tam')){
      var id = s.getAttribute('data-pager-tam');
      tabelas[id].tam = +s.value;
      tabelas[id].pag = 1;
      render(s.getAttribute('data-foco'));
    }
  });

  var redimensionando = false;
  window.addEventListener('resize', function(){
    if (redimensionando) return;
    redimensionando = true;
    requestAnimationFrame(function(){
      redimensionando = false;
      esconderTip();
      ajustarDensidade();
      ajustarNumeros();
    });
  });

  // ---- Avisos e carregamento -------------------------------------------------------------
  function mostrarAviso(msg, comLogin){
    aviso.hidden = false;
    aviso.innerHTML = esc(msg) + (comLogin ? ' <a href="' + URL_LOGIN + '">Ir para o login</a>' : '');
  }
  function esconderAviso(){
    aviso.hidden = true;
    aviso.innerHTML = '';
  }
  // Corpo da resposta como objeto mesmo quando o servidor não devolve JSON
  function lerJson(r){
    return r.text().then(function(texto){
      var d = null;
      try { d = JSON.parse(texto); } catch (e) { d = null; }
      if (!d || typeof d !== 'object'){
        d = { erro: 'Resposta inesperada do servidor (HTTP ' + r.status + ').' };
      }
      return { status: r.status, d: d };
    });
  }

  function carregar(){
    var t = token();
    if (!t){
      mostrarAviso('Você precisa entrar no ' + nomePlataforma + ' antes de abrir o painel.', true);
      return;
    }
    btnAtualizar.disabled = true;
    principal.classList.add('carregando');
    principal.setAttribute('aria-busy', 'true');
    var qs = '?limite=' + LIMITE_EVENTOS + (selMes.value ? '&mes=' + encodeURIComponent(selMes.value) : '');
    fetch('/api/admin/atividade' + qs, { headers: { 'Authorization': 'Bearer ' + t } })
    .then(lerJson)
    .then(function(res){
      if (res.status === 401){
        mostrarAviso('Sessão inválida ou expirada. Entre de novo no ' + nomePlataforma + '.', true);
        return;
      }
      if (res.status === 403){
        mostrarAviso('Acesso restrito: sua conta não está na lista de administradores.', false);
        return;
      }
      if (res.status !== 200){
        mostrarAviso(res.d.erro || ('Falha ao carregar os logs (HTTP ' + res.status + ').'), false);
        return;
      }
      var d = res.d;
      esconderAviso();
      if (d.nome_plataforma) nomePlataforma = String(d.nome_plataforma);
      eventos = (Array.isArray(d.eventos) ? d.eventos : []).filter(function(ev){
        return ev && typeof ev === 'object';
      });
      baselineManualMin = Number(d.baseline_manual_min) || 0;
      baselineFerramentaMin = Number(d.baseline_ferramenta_min) || 0;
      baselinePlannerManualMin = Number(d.baseline_planner_manual_min) || 0;
      baselinePlannerFerramentaMin = Number(d.baseline_planner_ferramenta_min) || 0;
      moeda = d.moeda || 'R$';
      cotacaoDolar = Number(d.cotacao_dolar) || 0;
      precoEntradaUsd = Number(d.preco_entrada_usd_milhao) || 0;
      precoSaidaUsd = Number(d.preco_saida_usd_milhao) || 0;
      indexarVotos();
      prepararBusca();
      popularFiltros(d.meses || []);
      Object.keys(tabelas).forEach(function(k){ tabelas[k].pag = 1; });
      carregou = true;
      filtrosTopo.hidden = false;
      notaLimite.hidden = eventos.length < LIMITE_EVENTOS;
      notaLimite.textContent = 'O painel carrega só os ' + fmtNum(LIMITE_EVENTOS, 0) +
        ' eventos mais recentes do mês. Use "Exportar CSV do mês" para ter o mês completo.';
      render();
    })
    .catch(function(e){
      mostrarAviso('Falha ao falar com o servidor: ' + e.message, false);
    })
    .then(function(){
      btnAtualizar.disabled = false;
      principal.classList.remove('carregando');
      principal.removeAttribute('aria-busy');
    });
  }

  // ---- Downloads (rotas protegidas) ---------------------------------------------------------
  // fetch + blob porque as rotas exigem o token no cabeçalho (um link comum
  // não manda o Authorization).
  function baixar(url, nomePadrao){
    return fetch(url, { headers: { 'Authorization': 'Bearer ' + token() } })
    .then(function(r){
      if (!r.ok){
        return lerJson(r).then(function(res){
          var erro = new Error(res.d.erro || ('HTTP ' + r.status));
          erro.status = r.status;
          throw erro;
        });
      }
      var m = /filename="([^"]+)"/.exec(r.headers.get('Content-Disposition') || '');
      return r.blob().then(function(blob){
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = (m && m[1]) || nomePadrao;
        document.body.appendChild(a);
        a.click();
        setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); }, 4000);
      });
    });
  }
  function rotuloBotao(btn, texto){ btn.querySelector('.txt').textContent = texto; }

  // Baixa o projeto inteiro (.zip) — para instalar a plataforma em outro PC.
  function baixarProjeto(){
    if (!token()){ mostrarAviso('Entre no ' + nomePlataforma + ' antes de baixar o projeto.', true); return; }
    btnBackup.disabled = true;
    rotuloBotao(btnBackup, 'Gerando zip...');
    baixar('/api/admin/backup', slugPlataforma() + '-projeto.zip')
    .catch(function(e){ mostrarAviso('Falha ao baixar o projeto: ' + e.message, e.status === 401); })
    .then(function(){
      btnBackup.disabled = false;
      rotuloBotao(btnBackup, 'Baixar projeto (.zip)');
    });
  }
  // Exporta o mês selecionado em CSV (o mês inteiro, sem os filtros locais)
  function exportarCsv(){
    if (!token()){ mostrarAviso('Entre no ' + nomePlataforma + ' antes de exportar.', true); return; }
    btnExportar.disabled = true;
    var qs = selMes.value ? '?mes=' + encodeURIComponent(selMes.value) : '';
    baixar('/api/admin/exportar' + qs, slugPlataforma() + '-logs.csv')
    .catch(function(e){ mostrarAviso('Falha ao exportar: ' + e.message, e.status === 401); })
    .then(function(){ btnExportar.disabled = false; });
  }

  btnBackup.addEventListener('click', baixarProjeto);
  btnExportar.addEventListener('click', exportarCsv);
  btnAtualizar.addEventListener('click', carregar);
  selMes.addEventListener('change', carregar);           // mês muda no servidor
  selEmail.addEventListener('change', function(){        // demais filtros são locais
    Object.keys(tabelas).forEach(function(k){ tabelas[k].pag = 1; });
    render();
  });
  function filtroTrilha(){ tabelas.eventos.pag = 1; renderEventos(); }
  selFerramenta.addEventListener('change', function(){ popularAcoes(); filtroTrilha(); });
  selAcao.addEventListener('change', filtroTrilha);
  selStatus.addEventListener('change', filtroTrilha);
  // busca: espera a pessoa parar de digitar (a trilha pode ter milhares de linhas)
  var timerBusca = null;
  busca.addEventListener('input', function(){
    clearTimeout(timerBusca);
    timerBusca = setTimeout(filtroTrilha, 160);
  });

  vistaAtual = vistaDoHash();
  atualizarCabecalho();
  carregar();
})();
