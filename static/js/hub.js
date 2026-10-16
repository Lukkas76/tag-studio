/* Home da plataforma (index.html, depois do login): saudação, cartões das
   ferramentas, resumo de uso do mês (GET /api/resumo) e atalho do painel
   administrativo para admins.

   TA.hub.mostrar(usuario) é chamada pelo login.js quando a sessão é
   confirmada (no load ou ao fim do login). A Home não dispara eventos GA4:
   não há mapa homologado para ela. Textos que vêm da conta (nome) entram
   sempre por textContent, nunca como HTML. */
(function(){
  'use strict';
  var TA = window.TA;

  var MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
               'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];

  function el(id){ return document.getElementById(id); }

  function nomePlataforma(){
    return (TA.cfg.plataforma && TA.cfg.plataforma.nome) || 'Tag Studio';
  }

  // "Lucas Silva" -> "Lucas". Nome todo em maiúsculas (comum em cadastro
  // corporativo) vira "Lucas" para a saudação não parecer um grito.
  function primeiroNome(usuario){
    var nome = String((usuario && usuario.nome) || '').trim().split(/\s+/)[0] || '';
    if (nome && nome === nome.toUpperCase() && nome !== nome.toLowerCase()){
      nome = nome.charAt(0) + nome.slice(1).toLowerCase();
    }
    return nome;
  }

  // "2026-10" -> "outubro". Sem o mês do servidor, usa o mês do navegador.
  function mesPorExtenso(mes){
    var m = /^(\d{4})-(\d{2})$/.exec(mes || '');
    var idx = m ? parseInt(m[2], 10) - 1 : -1;
    return MESES[idx] || MESES[new Date().getMonth()];
  }

  // "2026-10-15T14:03:11" -> "15/10 às 14:03". Lido como texto de propósito:
  // o horário já é o do servidor e new Date() poderia aplicar fuso.
  function formatarQuando(iso){
    var m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(iso || '');
    return m ? m[3] + '/' + m[2] + ' às ' + m[4] + ':' + m[5] : '';
  }

  function numero(n){
    n = Number(n) || 0;
    try { return n.toLocaleString('pt-BR'); } catch (e) { return String(n); }
  }
  function contagem(n, singular, plural){
    return numero(n) + ' ' + ((Number(n) || 0) === 1 ? singular : plural);
  }

  // ---- Saudação --------------------------------------------------------------
  function renderSaudacao(usuario){
    var alvo = el('hubSaudacao');
    if (!alvo) return;
    var nome = primeiroNome(usuario);
    alvo.textContent = '';
    if (!nome){ alvo.textContent = 'Olá.'; return; }
    var span = document.createElement('span');
    span.className = 'hub-nome';
    span.textContent = nome;
    alvo.appendChild(document.createTextNode('Olá, '));
    alvo.appendChild(span);
    alvo.appendChild(document.createTextNode('.'));
  }

  // ---- Resumo de uso do mês (GET /api/resumo) --------------------------------
  // Três estados: carregando (esqueleto), pronto (números) e erro (aviso +
  // "Tentar de novo"). Só a resposta da chamada mais recente é desenhada.
  var seqResumo = 0;

  function esqueleto(classe){
    var s = document.createElement('span');
    s.className = 'hub-esqueleto' + (classe ? ' ' + classe : '');
    s.setAttribute('aria-hidden', 'true');
    return s;
  }
  function anunciar(texto){
    var a = el('usoAnuncio');
    if (a) a.textContent = texto || '';
  }

  function estadoCarregando(){
    var painel = el('painelUso');
    if (painel) painel.setAttribute('aria-busy', 'true');
    el('usoErro').hidden = true;
    el('usoStats').hidden = false;
    el('usoPlataforma').hidden = false;
    ['usoAuditorias', 'usoAnalises', 'usoUltima'].forEach(function(id){
      var dd = el(id);
      dd.textContent = '';
      dd.appendChild(esqueleto(id === 'usoUltima' ? 'hub-esqueleto-largo' : ''));
    });
    var nota = el('usoPlataforma');
    nota.textContent = '';
    nota.appendChild(esqueleto('hub-esqueleto-linha'));
  }

  function estadoErro(porPedido){
    var painel = el('painelUso');
    if (painel) painel.removeAttribute('aria-busy');
    el('usoStats').hidden = true;
    el('usoPlataforma').hidden = true;
    el('usoErro').hidden = false;
    anunciar('Não foi possível carregar o seu uso agora.');
    // Falhou de novo depois do "Tentar de novo": o foco volta para o botão
    if (porPedido) el('usoTentar').focus();
  }

  function renderResumo(d, porPedido){
    var u = d.usuario || {};
    var p = d.plataforma || {};
    var painel = el('painelUso');
    if (painel) painel.removeAttribute('aria-busy');
    el('usoErro').hidden = true;
    el('usoStats').hidden = false;
    el('usoPlataforma').hidden = false;

    el('usoMes').textContent = mesPorExtenso(d.mes);
    el('usoAuditorias').textContent = numero(u.auditorias);
    el('usoAnalises').textContent = numero(u.analises);

    var ultima = el('usoUltima');
    var quando = formatarQuando(u.ultima_atividade);
    ultima.textContent = '';
    if (quando){
      ultima.textContent = quando;
    } else {
      // "—" na tela; para leitor de tela, uma frase em vez de "travessão"
      var traco = document.createElement('span');
      traco.setAttribute('aria-hidden', 'true');
      traco.textContent = '—';
      var leitor = document.createElement('span');
      leitor.className = 'hub-sr';
      leitor.textContent = 'Nenhuma atividade este mês';
      ultima.appendChild(traco);
      ultima.appendChild(leitor);
    }

    el('usoPlataforma').textContent = 'Na plataforma este mês: ' +
      contagem(p.auditorias, 'auditoria', 'auditorias') + ' · ' +
      contagem(p.analises, 'análise', 'análises') + ' · ' +
      contagem(p.usuarios, 'pessoa', 'pessoas');

    // Carga automática: os números aparecem na tela, sem anúncio. Depois de
    // um "Tentar de novo", avisa quem usa leitor de tela que deu certo.
    anunciar(porPedido ? 'Uso do mês atualizado.' : '');
  }

  // porPedido = a pessoa clicou em "Tentar de novo" (e não a carga automática)
  function carregarResumo(porPedido){
    var minha = ++seqResumo;
    anunciar('');
    // O botão "Tentar de novo" some enquanto carrega: o foco vai para o
    // título do painel em vez de cair no início da página.
    if (porPedido){
      var titulo = el('tituloUso');
      if (titulo){
        try { titulo.focus({ preventScroll: true }); } catch (e) { titulo.focus(); }
      }
    }
    estadoCarregando();
    TA.api.resumo()
    .then(function(res){
      if (minha !== seqResumo) return;
      if (!res.ok || !res.dados || typeof res.dados !== 'object'){ estadoErro(porPedido); return; }
      renderResumo(res.dados, porPedido);
    })
    .catch(function(){
      // Rede fora, servidor reiniciando ou 401 (o api.js já chamou o
      // TA.sair() e a página vai para o login).
      if (minha === seqResumo) estadoErro(porPedido);
    });
  }

  // ---- Mostrar a Home ---------------------------------------------------------
  var ligado = false;
  function ligarEventos(){
    if (ligado) return;
    ligado = true;
    var tentar = el('usoTentar');
    if (tentar) tentar.addEventListener('click', function(){ carregarResumo(true); });
    // Voltar para a Home pelo "Voltar" do navegador pode restaurar a página
    // do cache (bfcache) com números velhos: recarrega o resumo.
    window.addEventListener('pageshow', function(ev){
      if (ev.persisted && !el('telaHub').hidden) carregarResumo();
    });
  }

  function mostrar(usuario){
    var u = usuario || TA.usuarioSalvo() || {};
    var telaLogin = el('telaLogin');
    var telaHub = el('telaHub');
    if (telaLogin) telaLogin.style.display = 'none';
    telaHub.hidden = false;

    TA.sessao.renderMenu(u);
    // Já estamos no início: o atalho "Início da plataforma" do menu sobra aqui
    var btnInicio = el('btnInicio');
    if (btnInicio) btnInicio.style.display = 'none';

    renderSaudacao(u);
    document.title = nomePlataforma() + ' | Início';
    el('hubAdmin').hidden = !u.admin;
    el('usoMes').textContent = mesPorExtenso(null);

    ligarEventos();
    carregarResumo();

    // Foco no título: depois do login, o botão que tinha o foco sumiu; assim
    // teclado e leitor de tela começam pela saudação.
    var titulo = el('hubTitulo');
    if (titulo){
      try { titulo.focus({ preventScroll: true }); } catch (e) { titulo.focus(); }
    }
  }

  TA.hub = { mostrar: mostrar };
})();
