/* Painel administrativo da plataforma (Tag Auditor + Tag Planner).
   Consome GET /api/admin/atividade (restrito aos e-mails de EMAILS_ADMIN no
   tagauditor/config.py) e mostra a trilha de auditoria. O mês é filtrado no
   servidor; ferramenta, usuário, ação e busca são filtros locais e valem
   para TUDO na tela (cartões, gráfico, feedbacks e tabela), para os números
   sempre baterem entre si. */
(function(){
  'use strict';

  var CFG = window.TAG_CONFIG || {};
  // Nome da plataforma (NOME_PLATAFORMA no config.py) para as mensagens. O
  // HTML já chega com o nome trocado pelo servidor; a API também o devolve.
  var nomePlataforma = (CFG.plataforma && CFG.plataforma.nome) || 'Tag Studio';
  // ?volta= faz o login devolver a pessoa para o painel depois de entrar
  var URL_LOGIN = '/?volta=/admin';
  var LIMITE_EVENTOS = 5000;     // teto aceito por /api/admin/atividade
  var ALTURA_BARRA_MAX = 112;    // px: área do gráfico (132) menos o valor no topo

  var principal = document.getElementById('principal');
  var aviso = document.getElementById('aviso');
  var cards = document.getElementById('cards');
  var filtros = document.getElementById('filtros');
  var secAtividade = document.getElementById('secAtividade');
  var tabelaBox = document.getElementById('tabelaBox');
  var corpoTab = document.getElementById('corpoTab');
  var vazio = document.getElementById('vazio');
  var rodapeTab = document.getElementById('rodapeTab');
  var selMes = document.getElementById('selMes');
  var selFerramenta = document.getElementById('selFerramenta');
  var selEmail = document.getElementById('selEmail');
  var selAcao = document.getElementById('selAcao');
  var busca = document.getElementById('busca');
  var btnAtualizar = document.getElementById('btnAtualizar');
  var btnBackup = document.getElementById('btnBackup');
  var btnExportar = document.getElementById('btnExportar');
  var graficoBox = document.getElementById('graficoBox');
  var grafico = document.getElementById('grafico');
  var graficoInfo = document.getElementById('graficoInfo');
  var graficoLegenda = document.getElementById('graficoLegenda');
  var graficoVazio = document.getElementById('graficoVazio');
  var graficoTabela = document.getElementById('graficoTabela');
  var graficoTip = document.getElementById('graficoTip');
  var graficoLeitor = document.getElementById('graficoLeitor');
  var btnTabelaGrafico = document.getElementById('btnTabelaGrafico');
  var feedbacksBox = document.getElementById('feedbacksBox');
  var feedbacksLista = document.getElementById('feedbacksLista');

  var eventos = [];        // eventos do mês carregado (mais recentes primeiro)
  var textosBusca = [];    // texto pesquisável de cada evento (mesmo índice)
  var votoAtual = Object.create(null);   // 'sessão|mensagem' -> evento do voto vigente
  var baselineManualMin = 0;             // minutos por mapa no processo 100% manual
  var baselineFerramentaMin = 0;         // minutos por mapa usando o Tag Auditor
  var baselinePlannerManualMin = 0;      // minutos por análise sem o Tag Planner
  var baselinePlannerFerramentaMin = 0;  // minutos por análise com o Tag Planner
  var moeda = 'R$';
  var cotacaoDolar = 0;                     // R$ por US$ 1
  var precoEntradaUsd = 0, precoSaidaUsd = 0;  // US$ por 1 milhão de tokens

  var ROTULOS = {
    auditoria: 'Auditoria',
    confluence: 'Confluence',
    feedback: 'Feedback',
    planner_analise: 'Análise (Planner)',
    planner_figma: 'Figma (Planner)',
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
    auditor: { nome: 'Tag Auditor', curto: 'Auditor' },
    planner: { nome: 'Tag Planner', curto: 'Planner' },
    plataforma: { nome: 'Plataforma', curto: 'Plataforma' }
  };
  // Colunas próprias da tabela; `ferramenta` vira o chip calculado (abaixo)
  var CAMPOS_FIXOS = { quando: 1, acao: 1, email: 1, nome: 1, ip: 1, ferramenta: 1 };
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
    return s.normalize ? s.normalize('NFD').replace(/[\u0300-\u036f]/g, '') : s;
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
  function fmtMes(mes){
    var nomes = ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'];
    var m = /^(\d{4})-(\d{2})$/.exec(mes);
    return m ? (nomes[parseInt(m[2], 10) - 1] + '/' + m[1]) : mes;
  }
  // "2026-07-22T14:03:11" -> "22/07/2026 14:03:11" (hora em tom mais suave)
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
  function fmtDiaLongo(dia){   // 'sex, 16/10/2026'
    return fmtDiaCurto(dia) + '/' + dia.slice(0, 4);
  }
  function fmtDiaCurto(dia){   // 'sex, 16/10' (o ano já está no filtro de mês)
    return DIAS_SEMANA[dataDoDia(dia).getUTCDay()] + ', ' + dia.slice(8, 10) + '/' + dia.slice(5, 7);
  }
  // Todos os dias de `inicio` a `fim` (inclusive). No gráfico, dia sem uso
  // aparece vazio em vez de sumir: o eixo do tempo não fica comprimido.
  function diasEntre(inicio, fim){
    var dias = [], d = dataDoDia(inicio), ultimo = dataDoDia(fim).getTime();
    while (d.getTime() <= ultimo && dias.length < 400){
      dias.push(d.toISOString().slice(0, 10));
      d = new Date(d.getTime() + 864e5);
    }
    return dias;
  }

  // ---- Classificação dos eventos -------------------------------------------
  // Ferramenta de cada evento: o que começa com planner_ ou traz
  // ferramenta=planner é do Tag Planner; auditoria, Confluence e feedback sem
  // ferramenta (os votos antigos não têm o campo) são do Tag Auditor; o resto
  // (login, logout, admin_*, servidor, bq_* antigos) é da plataforma.
  function ferramentaDe(ev){
    var acao = String(ev.acao || '');
    if (acao.indexOf('planner_') === 0 || ev.ferramenta === 'planner') return 'planner';
    if (acao === 'auditoria' || acao === 'confluence' || acao === 'feedback') return 'auditor';
    return 'plataforma';
  }
  // Análise concluída do Tag Planner. Mesma regra do /api/resumo (Home), para
  // os números baterem: pergunta de acompanhamento não conta como análise.
  function ehAnaliseOk(ev){
    return ev.acao === 'planner_analise' && ev.status === 'ok' && ev.modo === 'analise';
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
  // cruzado com os filtros; assim a busca por um comentário antigo não
  // ressuscita um voto que a pessoa já trocou.
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
  // ("tag planner", "falha de login"), não só pelo JSON cru do log.
  function prepararBusca(){
    textosBusca = eventos.map(function(ev){
      return semAcento(JSON.stringify(ev) + ' ' + rotuloAcao(ev.acao) + ' ' +
                       FERRAMENTAS[ferramentaDe(ev)].nome);
    });
  }

  // ---- Avisos e carregamento -----------------------------------------------
  function mostrarAviso(msg, comLogin){
    aviso.hidden = false;
    aviso.innerHTML = esc(msg) + (comLogin ? ' <a href="' + URL_LOGIN + '">Ir para o login</a>' : '');
  }
  function esconderAviso(){
    aviso.hidden = true;
    aviso.innerHTML = '';
  }
  function mostrarPainel(){
    filtros.hidden = false;
    cards.hidden = false;
    graficoBox.hidden = false;
    feedbacksBox.hidden = false;
    secAtividade.hidden = false;
    tabelaBox.hidden = false;
  }
  // Corpo da resposta como objeto mesmo quando o servidor não devolve JSON
  // (ex.: página de erro em texto) — quem chama sempre lê d.erro.
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
  // Se a ação marcada não existe na nova ferramenta, volta para "Todas".
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
    var emails = {};
    eventos.forEach(function(ev){ if (ev.email) emails[ev.email] = 1; });
    preencherSelect(selEmail, Object.keys(emails).sort().map(function(e){
      return { valor: e, texto: e };
    }), 'Todos');
    popularAcoes();
  }

  function filtrados(){
    var ferr = selFerramenta.value, email = selEmail.value, acao = selAcao.value;
    var termo = semAcento((busca.value || '').trim());
    return eventos.filter(function(ev, i){
      if (ferr && ferramentaDe(ev) !== ferr) return false;
      if (email && (ev.email || '') !== email) return false;
      if (acao && ev.acao !== acao) return false;
      if (termo && textosBusca[i].indexOf(termo) === -1) return false;
      return true;
    });
  }

  // ---- Cartões-resumo --------------------------------------------------------
  function porFerramentaVazio(){
    return { tokE: 0, tokS: 0, likes: 0, dislikes: 0 };
  }
  function resumir(lista){
    var r = {
      auditorias: 0, audsOk: 0, analises: 0, perguntas: 0, erros: 0, logins: 0, usuarios: 0,
      tokE: 0, tokS: 0, temTokens: false, temEstimativa: false,
      likes: 0, dislikes: 0, fbRecentes: [],
      porFerr: { auditor: porFerramentaVazio(), planner: porFerramentaVazio(), plataforma: porFerramentaVazio() }
    };
    var emails = {};
    lista.forEach(function(ev){
      var pf = r.porFerr[ferramentaDe(ev)];
      // Auditoria = CONCLUÍDA (mesma regra da Home e do card de análises do
      // Planner); as que falharam entram só no card Erros.
      if (ev.acao === 'auditoria' && ev.status !== 'erro'){
        r.auditorias++;
        r.audsOk++;   // "mapa feito" do tempo economizado
      }
      if (ehAnaliseOk(ev)) r.analises++;
      if (ev.acao === 'planner_analise' && ev.status === 'ok' && ev.modo === 'pergunta') r.perguntas++;
      // Erro = qualquer evento com status "erro" (auditoria, Confluence, Figma,
      // análise do Planner...) + falha de login
      if (ev.status === 'erro' || ev.acao === 'login_falha') r.erros++;
      if (ev.acao === 'login') r.logins++;
      if (ehVotoAtual(ev)){
        if (ev.voto === 'like'){ r.likes++; pf.likes++; }
        else { r.dislikes++; pf.dislikes++; }
        r.fbRecentes.push(ev);   // `lista` já vem do mais novo para o mais antigo
      }
      if (ev.email) emails[ev.email] = 1;
      if (ev.tokens_entrada != null || ev.tokens_saida != null){
        var te = Number(ev.tokens_entrada) || 0, ts = Number(ev.tokens_saida) || 0;
        r.temTokens = true;
        r.tokE += te; r.tokS += ts;
        pf.tokE += te; pf.tokS += ts;
        if (ev.tokens_fonte === 'estimativa') r.temEstimativa = true;
      }
    });
    r.usuarios = Object.keys(emails).length;
    return r;
  }

  function definir(id, texto, classe){
    var el = document.getElementById(id);
    el.textContent = texto;
    if (classe != null) el.className = classe;
  }
  // Detalhe do cartão no tooltip nativo (title): a conta por trás do número
  function dica(id, texto){
    var el = document.getElementById(id);
    if (texto) el.title = texto;
    else el.removeAttribute('title');
  }
  function textoPorFerramenta(r, campo){
    var a = r.porFerr.auditor[campo], p = r.porFerr.planner[campo];
    if (!a || !p) return '';
    return 'Tag Auditor: ' + fmtNum(a, 0) + '\nTag Planner: ' + fmtNum(p, 0);
  }

  function renderCards(r, total){
    definir('cAuditorias', fmtNum(r.auditorias, 0));
    definir('cAnalises', fmtNum(r.analises, 0));
    dica('kAnalises', 'Análises concluídas no Tag Planner. Perguntas de acompanhamento não contam' +
      (r.perguntas ? ' (' + plural(r.perguntas, 'pergunta', 'perguntas') + ' no filtro).' : '.'));
    definir('cErros', fmtNum(r.erros, 0), 'num' + (r.erros > 0 ? ' ruim' : ' mudo'));
    definir('cLogins', fmtNum(r.logins, 0));
    definir('cUsuarios', fmtNum(r.usuarios, 0));
    definir('cEventos', fmtNum(total, 0));

    // "~" quando algum evento só tem estimativa de tokens (a API não devolveu usage)
    var prefixo = r.temEstimativa ? '~' : '';
    definir('cTokensEntrada', r.temTokens ? prefixo + fmtNum(r.tokE, 0) : '—');
    definir('cTokensSaida', r.temTokens ? prefixo + fmtNum(r.tokS, 0) : '—');
    dica('kTokensEntrada', textoPorFerramenta(r, 'tokE'));
    dica('kTokensSaida', textoPorFerramenta(r, 'tokS'));

    // custo = tokens/1 milhão × preço (US$) × cotação — entrada e saída diferem
    var temPreco = (precoEntradaUsd > 0 || precoSaidaUsd > 0) && cotacaoDolar > 0;
    var custoUsd = (r.tokE / 1e6) * precoEntradaUsd + (r.tokS / 1e6) * precoSaidaUsd;
    definir('cCusto', (r.temTokens && temPreco) ? prefixo + fmtDinheiro(custoUsd * cotacaoDolar) : '—');
    definir('rCusto', !temPreco ? 'Custo (configure o preço)'
      : r.temTokens ? 'Custo estimado (' + fmtUsd(custoUsd) + ')' : 'Custo estimado');

    renderSatisfacao(r);
    renderTempo(r);
    ajustarNumeros();
  }

  // Número que não cabe no tile (ex.: milhões de tokens em tela de 1366px)
  // diminui de tamanho em vez de ser cortado com "..." — o valor é exato e
  // o admin compara com a fatura da Azure.
  function ajustarNumeros(){
    Array.prototype.forEach.call(cards.querySelectorAll('.kpi .num'), function(el){
      el.style.fontSize = '';
      if (!el.clientWidth) return;   // painel escondido
      var tamanho = parseFloat(getComputedStyle(el).fontSize) || 24;
      while (el.scrollWidth > el.clientWidth && tamanho > 15){
        tamanho -= 1;
        el.style.fontSize = tamanho + 'px';
      }
    });
  }

  // Satisfação: % de 👍 sobre os votos LÍQUIDOS do filtro (das duas ferramentas)
  function renderSatisfacao(r){
    var votos = r.likes + r.dislikes;
    var pct = votos ? Math.round(r.likes / votos * 100) : 0;
    definir('cSatisfacao', votos ? pct + '%' : '—',
            'num' + (!votos ? '' : pct >= 70 ? ' bom' : pct >= 40 ? ' alerta' : ' ruim'));
    definir('rSatisfacao', votos ? ('👍 ' + r.likes + ' · 👎 ' + r.dislikes) : 'Satisfação (👍/👎)');
    var a = r.porFerr.auditor, p = r.porFerr.planner, linhas = [];
    if (a.likes + a.dislikes) linhas.push('Tag Auditor: 👍 ' + a.likes + ' · 👎 ' + a.dislikes);
    if (p.likes + p.dislikes) linhas.push('Tag Planner: 👍 ' + p.likes + ' · 👎 ' + p.dislikes);
    dica('kSatisfacao', 'Voto líquido: cada resposta conta uma vez e vale o voto mais recente.' +
      (linhas.length ? '\n' + linhas.join('\n') : ''));
  }

  // Tempo economizado (estimativa do time, config.py):
  //   Tag Auditor = auditorias concluídas × (manual − com a ferramenta)
  //   Tag Planner = análises concluídas × (manual − com o Planner), só se manual > com o Planner
  function renderTempo(r){
    var poupAud = Math.max(0, baselineManualMin - baselineFerramentaMin);
    var poupPlan = Math.max(0, baselinePlannerManualMin - baselinePlannerFerramentaMin);
    var aplicaAud = r.audsOk > 0 && poupAud > 0;
    var aplicaPlan = r.analises > 0 && poupPlan > 0;
    var minutos = (aplicaAud ? r.audsOk * poupAud : 0) + (aplicaPlan ? r.analises * poupPlan : 0);
    definir('cTempo', (aplicaAud || aplicaPlan) ? '~' + fmtDuracao(minutos * 60) : '—');

    var aud = fmtDuracao(baselineManualMin * 60) + ' → ' + fmtDuracao(baselineFerramentaMin * 60);
    var plan = fmtDuracao(baselinePlannerManualMin * 60) + ' → ' + fmtDuracao(baselinePlannerFerramentaMin * 60);
    var elRotulo = document.getElementById('rTempo');
    if (aplicaAud && aplicaPlan){
      // as duas ferramentas: o ganho por item de cada uma, uma por linha (o
      // "manual → com a ferramenta" das duas não cabe no tile sem quebrar mal)
      elRotulo.innerHTML = '<span class="lin">Economizado por item:</span> ' +
        '<span class="lin">Auditor ' + esc(fmtDuracao(poupAud * 60)) + '/mapa</span> ' +
        '<span class="lin">Planner ' + esc(fmtDuracao(poupPlan * 60)) + '/análise</span>';
    } else {
      var rotulo = 'Tempo economizado';
      if (aplicaAud) rotulo = 'Economizado (' + aud + ' por mapa)';
      else if (aplicaPlan) rotulo = 'Economizado no Tag Planner (' + plan + ' por análise)';
      else if (r.audsOk || r.analises) rotulo = 'Tempo economizado (sem estimativa configurada)';
      elRotulo.textContent = rotulo;
    }

    // tooltip: a conta de cada ferramenta e onde ajustar as estimativas
    var linhas = [];
    if (r.audsOk){
      linhas.push(aplicaAud
        ? 'Tag Auditor: ' + plural(r.audsOk, 'auditoria concluída', 'auditorias concluídas') + ' × ' +
          fmtDuracao(poupAud * 60) + ' = ' + fmtDuracao(r.audsOk * poupAud * 60)
        : 'Tag Auditor: sem estimativa (TEMPO_MANUAL_MIN / TEMPO_COM_FERRAMENTA_MIN)');
    }
    if (r.analises){
      linhas.push(aplicaPlan
        ? 'Tag Planner: ' + plural(r.analises, 'análise concluída', 'análises concluídas') + ' × ' +
          fmtDuracao(poupPlan * 60) + ' = ' + fmtDuracao(r.analises * poupPlan * 60)
        : 'Tag Planner: sem estimativa (TEMPO_MANUAL_PLANNER_MIN / TEMPO_COM_PLANNER_MIN)');
    }
    dica('kTempo', linhas.length ? 'Estimativa (ajuste em tagauditor/config.py):\n' + linhas.join('\n') : '');
  }

  // ---- Tabela de eventos -----------------------------------------------------
  function classeAcao(ev){
    if (ev.status === 'erro' || ev.acao === 'login_falha' || ev.acao === 'admin_negado') return 'acao erro';
    if (ev.status === 'ok' || ev.acao === 'login') return 'acao ok';
    return 'acao';
  }
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
    // os dois-pontos ficam dentro do <b>: o espaço do chip (flex gap) vem
    // depois deles ("status: ok"), não antes ("status : ok")
    if (k === 'status'){
      var cls = v === 'ok' ? ' st-ok' : (v === 'erro' ? ' st-erro' : '');
      return '<span class="chip' + cls + '"><b>status:</b> ' + esc(fmtValor(k, v)) + '</span>';
    }
    var titulo = esc(k + ': ' + tituloValor(k, v));
    if (k === 'comentario'){
      return '<span class="chip coment" title="' + titulo + '"><b>comentário:</b> <span class="v">' +
        esc(v) + '</span></span>';
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
  function renderTabela(lista){
    var html = '';
    lista.forEach(function(ev){
      html += '<tr role="row">'
        + '<td role="cell" class="quando">' + fmtQuandoHTML(ev.quando) + '</td>'
        + '<td role="cell" class="c-acao"><span class="' + classeAcao(ev) + '">' + esc(rotuloAcao(ev.acao)) + '</span></td>'
        + '<td role="cell" class="usuario"><div class="nome">' + esc(ev.nome || '—') + '</div>'
        + '<div class="email">' + esc(ev.email || '') + '</div></td>'
        + '<td role="cell" class="ip">' + esc(ev.ip || '—') + '</td>'
        + '<td role="cell" class="c-det"><div class="det">' + detalhesHTML(ev) + '</div></td>'
        + '</tr>';
    });
    corpoTab.innerHTML = html;
    vazio.hidden = lista.length > 0;
    var rodape = fmtNum(lista.length, 0) + ' evento(s) exibido(s) de ' + fmtNum(eventos.length, 0) +
      ' carregados no mês. Fonte: logs/atividade-<mês>.jsonl no servidor.';
    if (eventos.length >= LIMITE_EVENTOS){
      rodape += ' Atenção: o painel carrega só os ' + fmtNum(LIMITE_EVENTOS, 0) +
        ' eventos mais recentes do mês; use "Exportar CSV" para ter o mês completo.';
    }
    rodapeTab.textContent = rodape;
  }

  // ---- Feedbacks recentes ------------------------------------------------------
  // Últimos votos LÍQUIDOS (um por resposta), com 👍/👎, quem votou, a
  // ferramenta e o comentário (ou o dia e a hora quando não houver).
  function renderFeedbacks(itens){
    if (!itens.length){
      feedbacksLista.innerHTML = '<div class="fb-vazio">Sem feedbacks no período.</div>';
      return;
    }
    var html = '';
    itens.slice(0, 6).forEach(function(ev){
      var like = ev.voto === 'like';
      var ferr = ferramentaDe(ev);
      var det = (ev.comentario && String(ev.comentario).trim())
        ? esc(ev.comentario) : esc(fmtDiaHora(ev.quando));
      html += '<div class="fb-row">'
        + '<span class="fb-pill ' + (like ? 'like' : 'dislike') + '" role="img" aria-label="' +
          (like ? 'Gostou' : 'Não gostou') + '">' + (like ? '👍' : '👎') + '</span>'
        + '<span class="who" title="' + esc(ev.email || '') + '">' + esc(ev.nome || ev.email || '—') + '</span>'
        + '<span class="fb-ferr ' + ferr + '" title="' + esc(FERRAMENTAS[ferr].nome) + '">' +
          esc(FERRAMENTAS[ferr].curto) + '</span>'
        + '<span class="txt" title="' + det + '">' + det + '</span>'
        + '</div>';
    });
    feedbacksLista.innerHTML = html;
  }

  // ---- Gráfico "Uso por dia" (barras empilhadas) -------------------------------
  // Auditorias (vermelho, embaixo) + análises do Tag Planner (vinho, em cima),
  // do 1º ao último dia com uso no filtro. Mesmas regras dos cartões, para os
  // totais baterem: auditoria concluída (status ≠ erro); análise = ehAnaliseOk.
  var colunasGrafico = [];   // [{dia, aud, plan, total}] na ordem do eixo
  var colAtiva = -1;         // coluna com o tooltip aberto (-1 = nenhuma)
  var modoTabela = false;    // true = mostra a tabela no lugar das barras

  function textoColuna(c){
    if (!c.total) return fmtDiaLongo(c.dia) + ': sem uso';
    return fmtDiaLongo(c.dia) + ': ' + plural(c.aud, 'auditoria', 'auditorias') + ' e ' +
      plural(c.plan, 'análise do Tag Planner', 'análises do Tag Planner') +
      ' (' + fmtNum(c.total, 0) + ' no total)';
  }
  function tipHTML(c){
    return '<div class="t-dia">' + esc(fmtDiaLongo(c.dia)) + '</div>'
      + '<div class="t-lin"><span class="k aud"></span><b>' + fmtNum(c.aud, 0) + '</b>' +
        (c.aud === 1 ? 'auditoria' : 'auditorias') + '</div>'
      + '<div class="t-lin"><span class="k plan"></span><b>' + fmtNum(c.plan, 0) + '</b>' +
        (c.plan === 1 ? 'análise (Tag Planner)' : 'análises (Tag Planner)') + '</div>'
      + '<div class="t-lin t-total"><b>' + fmtNum(c.total, 0) + '</b>no total</div>';
  }
  function tabelaGraficoHTML(totAud, totPlan){
    var linhas = '';
    colunasGrafico.forEach(function(c){
      if (!c.total) return;
      linhas += '<tr><td>' + esc(fmtDiaCurto(c.dia)) + '</td><td>' + fmtNum(c.aud, 0) + '</td><td>' +
        fmtNum(c.plan, 0) + '</td><td>' + fmtNum(c.total, 0) + '</td></tr>';
    });
    // cabeçalho com a mesma marca de cor do gráfico (liga a tabela às barras)
    return '<table class="tab-grafico"><caption class="sr">Uso por dia (só os dias com uso)</caption>'
      + '<thead><tr><th scope="col">Dia</th>'
      + '<th scope="col"><span class="cab-serie"><span class="sw aud"></span>Auditorias</span></th>'
      + '<th scope="col"><span class="cab-serie"><span class="sw plan"></span>Análises</span> (Planner)</th>'
      + '<th scope="col">Total</th></tr></thead>'
      + '<tbody>' + linhas + '</tbody>'
      + '<tfoot><tr><th scope="row">Total</th><td>' + fmtNum(totAud, 0) + '</td><td>' + fmtNum(totPlan, 0) +
        '</td><td>' + fmtNum(totAud + totPlan, 0) + '</td></tr></tfoot></table>';
  }

  function renderGrafico(lista){
    var porDia = {}, diasPorEmail = {}, totAud = 0, totPlan = 0;
    lista.forEach(function(ev){
      var ehAud = ev.acao === 'auditoria' && ev.status !== 'erro', ehPlan = ehAnaliseOk(ev);
      if (!ehAud && !ehPlan) return;
      var dia = String(ev.quando || '').slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(dia)) return;
      var d = porDia[dia] || (porDia[dia] = { aud: 0, plan: 0 });
      if (ehAud){ d.aud++; totAud++; } else { d.plan++; totPlan++; }
      if (ev.email) (diasPorEmail[ev.email] = diasPorEmail[ev.email] || {})[dia] = 1;
    });
    var ativos = Object.keys(porDia).sort();
    desativarColuna();
    colunasGrafico = [];
    if (!ativos.length){
      graficoInfo.textContent = '';
      graficoLegenda.innerHTML = '';
      grafico.innerHTML = '';
      graficoTabela.innerHTML = '';
      aplicarModoGrafico();
      return;
    }
    colunasGrafico = diasEntre(ativos[0], ativos[ativos.length - 1]).map(function(dia){
      var d = porDia[dia] || { aud: 0, plan: 0 };
      return { dia: dia, aud: d.aud, plan: d.plan, total: d.aud + d.plan };
    });
    var max = 0;
    colunasGrafico.forEach(function(c){ if (c.total > max) max = c.total; });

    // usuários recorrentes = com auditoria/análise em 2 dias ou mais
    var recorrentes = 0;
    Object.keys(diasPorEmail).forEach(function(e){
      if (Object.keys(diasPorEmail[e]).length >= 2) recorrentes++;
    });
    var series = [];
    if (totAud) series.push(plural(totAud, 'auditoria', 'auditorias'));
    if (totPlan) series.push(plural(totPlan, 'análise do Tag Planner', 'análises do Tag Planner'));
    graficoInfo.textContent = '· ' + series.join(' + ') + ' em ' + plural(ativos.length, 'dia', 'dias') +
      ' · ' + plural(recorrentes, 'usuário recorrente', 'usuários recorrentes') + ' (2+ dias)';

    // legenda: só as séries que existem no filtro; a cor segue a ferramenta,
    // nunca a posição (filtrar não repinta a série que sobra)
    graficoLegenda.innerHTML =
      (totAud ? '<span class="item"><span class="sw aud"></span>Auditorias</span>' : '') +
      (totPlan ? '<span class="item"><span class="sw plan"></span>Análises (Tag Planner)</span>' : '');

    var html = '', picoMarcado = false;
    colunasGrafico.forEach(function(c, i){
      var hTot = 0, hAud = 0, hPlan = 0, ambos = c.aud > 0 && c.plan > 0;
      if (c.total){
        hTot = Math.max(ambos ? 6 : 4, Math.round(c.total / max * ALTURA_BARRA_MAX));
        if (ambos){
          var util = hTot - 2;   // 2px de respiro branco entre as fatias
          hPlan = Math.max(2, Math.round(util * c.plan / c.total));
          hAud = Math.max(2, util - hPlan);
        } else if (c.aud) hAud = hTot;
        else hPlan = hTot;
      }
      var nDia = +c.dia.slice(8, 10), semana = dataDoDia(c.dia).getUTCDay();
      var clsDia = 'g-dia' + (nDia === 1 || nDia % 5 === 0 ? ' chave' : '') +
        (semana === 0 || semana === 6 ? ' fds' : '');
      var pico = c.total > 0 && c.total === max && !picoMarcado;
      if (pico) picoMarcado = true;
      html += '<div class="g-col" data-i="' + i + '" role="img" aria-label="' + esc(textoColuna(c)) + '">'
        + '<div class="g-area">'
        + (c.total
          ? '<span class="g-val' + (pico ? ' pico' : '') + '">' + fmtNum(c.total, 0) + '</span>'
            + '<div class="g-pilha" style="height:' + (hAud + hPlan + (ambos ? 2 : 0)) + 'px">'
            + (hPlan ? '<div class="g-seg plan" style="height:' + hPlan + 'px"></div>' : '')
            + (hAud ? '<div class="g-seg aud" style="height:' + hAud + 'px"></div>' : '')
            + '</div>'
          : '')
        + '</div>'
        + '<span class="' + clsDia + '">' + nDia + '</span>'
        + '</div>';
    });
    grafico.innerHTML = html;
    grafico.setAttribute('data-max', String(max));
    graficoTabela.innerHTML = tabelaGraficoHTML(totAud, totPlan);
    aplicarModoGrafico();
  }

  // Barras x tabela x vazio, conforme os dados e o botão "Ver tabela"
  function aplicarModoGrafico(){
    var temDados = colunasGrafico.length > 0;
    grafico.hidden = !temDados || modoTabela;
    graficoLegenda.hidden = !temDados || modoTabela;
    graficoTabela.hidden = !temDados || !modoTabela;
    graficoVazio.hidden = temDados;
    btnTabelaGrafico.hidden = !temDados;
    desativarColuna();
    ajustarDensidade();
  }
  function alternarTabelaGrafico(){
    modoTabela = !modoTabela;
    btnTabelaGrafico.textContent = modoTabela ? 'Ver gráfico' : 'Ver tabela';
    aplicarModoGrafico();
  }
  // Colunas estreitas demais para escrever o dia e o valor em todas (mês
  // cheio, celular): mostra só os dias 1, 5, 10... e o valor do pico; os
  // outros números continuam no tooltip e na tabela.
  function ajustarDensidade(){
    var n = colunasGrafico.length;
    if (!n || grafico.hidden || !grafico.clientWidth) return;
    var digitos = String(grafico.getAttribute('data-max') || '0').length;
    var minimo = Math.max(20, digitos * 7 + 6);
    if (grafico.clientWidth / n < minimo) grafico.classList.add('denso');
    else grafico.classList.remove('denso');
  }

  function colunasDom(){ return grafico.querySelectorAll('.g-col'); }
  function acharColuna(el){
    while (el && el !== grafico){
      if (el.classList && el.classList.contains('g-col')) return el;
      el = el.parentNode;
    }
    return null;
  }
  // Tooltip ao lado da coluna (não cobre a barra): à direita, ou à esquerda
  // se não couber; em tela estreita, centralizado no topo da área.
  function posicionarTip(col){
    var caixa = graficoBox.getBoundingClientRect();
    var rc = col.getBoundingClientRect();
    var ra = col.firstChild.getBoundingClientRect();
    var w = graficoTip.offsetWidth, folga = 8;
    var x = rc.right - caixa.left + folga;
    if (x + w > caixa.width - folga) x = rc.left - caixa.left - w - folga;
    if (x < folga){
      x = Math.min(Math.max(folga, rc.left - caixa.left + rc.width / 2 - w / 2), caixa.width - w - folga);
    }
    graficoTip.style.left = Math.round(Math.max(0, x)) + 'px';
    graficoTip.style.top = Math.round(ra.top - caixa.top + 4) + 'px';
  }
  function ativarColuna(i, anunciar){
    var cols = colunasDom();
    if (!cols[i] || !colunasGrafico[i]) return;
    if (colAtiva >= 0 && cols[colAtiva]) cols[colAtiva].classList.remove('ativa');
    colAtiva = i;
    cols[i].classList.add('ativa');
    graficoTip.innerHTML = tipHTML(colunasGrafico[i]);
    graficoTip.hidden = false;
    posicionarTip(cols[i]);
    // pelo teclado, o leitor de tela ouve o dia (região aria-live)
    if (anunciar) graficoLeitor.textContent = textoColuna(colunasGrafico[i]);
  }
  function desativarColuna(){
    if (colAtiva >= 0){
      var col = colunasDom()[colAtiva];
      if (col) col.classList.remove('ativa');
    }
    colAtiva = -1;
    graficoTip.hidden = true;
  }

  grafico.addEventListener('mousemove', function(e){
    var col = acharColuna(e.target);
    if (!col) return;
    var i = +col.getAttribute('data-i');
    if (i !== colAtiva) ativarColuna(i, false);
  });
  grafico.addEventListener('mouseleave', function(){
    if (document.activeElement !== grafico) desativarColuna();
  });
  // toque (celular): tocar na coluna mostra os números do dia
  grafico.addEventListener('click', function(e){
    var col = acharColuna(e.target);
    if (col) ativarColuna(+col.getAttribute('data-i'), false);
  });
  // teclado: o gráfico é um único Tab; as setas andam pelos dias
  grafico.addEventListener('keydown', function(e){
    var n = colunasGrafico.length, i = colAtiva;
    if (!n) return;
    if (e.key === 'ArrowRight') i = i < 0 ? 0 : Math.min(n - 1, i + 1);
    else if (e.key === 'ArrowLeft') i = i < 0 ? n - 1 : Math.max(0, i - 1);
    else if (e.key === 'Home') i = 0;
    else if (e.key === 'End') i = n - 1;
    else if (e.key === 'Escape'){ desativarColuna(); return; }
    else return;
    e.preventDefault();
    ativarColuna(i, true);
  });
  // ao receber o foco, começa pelo dia mais recente com uso
  grafico.addEventListener('focus', function(){
    if (colAtiva >= 0) return;
    for (var i = colunasGrafico.length - 1; i >= 0; i--){
      if (colunasGrafico[i].total){ ativarColuna(i, true); return; }
    }
  });
  grafico.addEventListener('blur', desativarColuna);
  btnTabelaGrafico.addEventListener('click', alternarTabelaGrafico);

  var redimensionando = false;
  window.addEventListener('resize', function(){
    if (redimensionando) return;
    redimensionando = true;
    requestAnimationFrame(function(){
      redimensionando = false;
      desativarColuna();
      ajustarDensidade();
      ajustarNumeros();
    });
  });

  // ---- Render geral ------------------------------------------------------------
  function render(){
    var lista = filtrados();
    var r = resumir(lista);
    renderCards(r, lista.length);
    renderGrafico(lista);
    renderFeedbacks(r.fbRecentes);
    renderTabela(lista);
  }

  function carregar(){
    var t = token();
    if (!t){
      mostrarAviso('Você precisa entrar no ' + nomePlataforma + ' antes de abrir o painel.', true);
      return;
    }
    btnAtualizar.disabled = true;
    // recarga: o quadro anterior fica esmaecido até chegar o novo (sem piscar)
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
      mostrarPainel();   // antes do render: o gráfico mede a largura visível
      popularFiltros(d.meses || []);
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

  // ---- Downloads (rotas protegidas) ------------------------------------------------
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

  // Baixa o projeto inteiro (.zip) — para instalar a plataforma em outro PC.
  function baixarProjeto(){
    if (!token()){ mostrarAviso('Entre no ' + nomePlataforma + ' antes de baixar o projeto.', true); return; }
    btnBackup.disabled = true;
    btnBackup.textContent = 'Gerando zip...';
    baixar('/api/admin/backup', slugPlataforma() + '-projeto.zip')
    .catch(function(e){ mostrarAviso('Falha ao baixar o projeto: ' + e.message, e.status === 401); })
    .then(function(){
      btnBackup.disabled = false;
      btnBackup.innerHTML = '<span aria-hidden="true">&#8681;</span> Baixar projeto (.zip)';
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
  selMes.addEventListener('change', carregar);           // mês muda no servidor
  selFerramenta.addEventListener('change', function(){   // demais filtros são locais
    popularAcoes();
    render();
  });
  selEmail.addEventListener('change', render);
  selAcao.addEventListener('change', render);
  // busca: espera a pessoa parar de digitar (a tabela pode ter milhares de linhas)
  var timerBusca = null;
  busca.addEventListener('input', function(){
    clearTimeout(timerBusca);
    timerBusca = setTimeout(render, 160);
  });
  btnAtualizar.addEventListener('click', carregar);

  carregar();
})();
