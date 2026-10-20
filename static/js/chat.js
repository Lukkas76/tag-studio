/* Aplicação principal do chat: conversas (localStorage), sidebar, composer,
   seletor de canal, anexo de plano, envio (inteiro ou em partes) e busca no
   Confluence. TA.iniciarApp() é chamada pelo auditor.js depois que a sessão
   é confirmada (página /auditor). */
(function(){
  'use strict';
  var TA = window.TA;

  TA.iniciarApp = function(){
      var CFG = TA.cfg;
      var LIMITE_PARTE = CFG.limiteParte || 999999;
      var ENXUGAR = !!CFG.enxugar;

      // ---- refs DOM ----
      var chat = document.getElementById('chat');
      var campo = document.getElementById('texto');
      var btnEnviar = document.getElementById('btnEnviar');
      var btnAnexo = document.getElementById('btnAnexo');
      var btnConfluence = document.getElementById('btnConfluence');
      var inputArquivo = document.getElementById('arquivo');
      var chip = document.getElementById('chip');
      var chipNome = document.getElementById('chipNome');
      var chipRemover = document.getElementById('chipRemover');
      var listaConversas = document.getElementById('listaConversas');
      var btnNovo = document.getElementById('btnNovo');
      var tituloConversa = document.getElementById('tituloConversa');
      var btnMenu = document.getElementById('btnMenu');
      var sidebar = document.getElementById('sidebar');
      var overlay = document.getElementById('overlay');
      var app = document.querySelector('.app');

      var anexo = null;
      var enviando = false;
      var canalArea = document.getElementById('canalArea');
      var botoesCanal = canalArea ? canalArea.querySelectorAll('.canal-opt') : [];
      var canalSelecionado = null;

      // =====================================================================
      // Conversas (persistidas em localStorage, por navegador)
      // =====================================================================
      var LS_KEY = 'tagauditor_conversas_v1';
      var estado = { conversas: [], atualId: null };

      function gerarId(){
        return 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
      }
      function agora(){ return Date.now(); }

      function carregar(){
        try {
          var bruto = localStorage.getItem(LS_KEY);
          if (bruto){
            var o = JSON.parse(bruto);
            if (o && Array.isArray(o.conversas)){
              estado.conversas = o.conversas;
              estado.atualId = o.atualId || null;
            }
          }
        } catch(e){ estado = { conversas: [], atualId: null }; }
      }
      function salvar(){
        try { localStorage.setItem(LS_KEY, JSON.stringify(estado)); }
        catch(e){ /* localStorage cheio/indisponivel: segue sem persistir */ }
      }
      function conversaAtual(){
        for (var i = 0; i < estado.conversas.length; i++){
          if (estado.conversas[i].id === estado.atualId) return estado.conversas[i];
        }
        return null;
      }
      function ordenadas(){
        return estado.conversas.slice().sort(function(a, b){
          return (b.atualizadoEm || 0) - (a.atualizadoEm || 0);
        });
      }
      function podarVazias(){
        var atual = conversaAtual();
        var atualVazia = atual && !atual.mensagens.length;
        estado.conversas = estado.conversas.filter(function(c){
          return c.mensagens.length || (atualVazia && c.id === estado.atualId);
        });
      }
      function novaConversa(){
        var vazia = null;
        for (var i = 0; i < estado.conversas.length; i++){
          if (!estado.conversas[i].mensagens.length){ vazia = estado.conversas[i]; break; }
        }
        if (!vazia){
          vazia = { id: gerarId(), titulo: 'Nova conversa', criadoEm: agora(), atualizadoEm: agora(), mensagens: [] };
          estado.conversas.push(vazia);
        }
        estado.atualId = vazia.id;
        podarVazias();
        salvar();
        renderSidebar();
        renderChat();
        fecharSidebarSeMobile();
        campo.focus();
      }
      function garantirConversa(){
        if (!conversaAtual()){
          if (estado.conversas.length){ estado.atualId = ordenadas()[0].id; }
          else { novaConversa(); }
        }
      }
      function abrir(id){
        estado.atualId = id;
        salvar();
        renderSidebar();
        renderChat();
        fecharSidebarSeMobile();
      }
      function apagar(id){
        var c = null, idx = -1;
        for (var i = 0; i < estado.conversas.length; i++){ if (estado.conversas[i].id === id){ c = estado.conversas[i]; idx = i; } }
        if (!c) return;
        if (!confirm('Apagar a conversa "' + c.titulo + '"? Esta ação não pode ser desfeita.')) return;
        estado.conversas.splice(idx, 1);
        if (estado.atualId === id){
          estado.atualId = estado.conversas.length ? ordenadas()[0].id : null;
          if (!estado.atualId){ novaConversa(); return; }
        }
        salvar();
        renderSidebar();
        renderChat();
      }
      function renomear(id){
        var c = null;
        for (var i = 0; i < estado.conversas.length; i++){ if (estado.conversas[i].id === id) c = estado.conversas[i]; }
        if (!c) return;
        var novo = prompt('Novo nome da conversa:', c.titulo);
        if (novo == null) return;
        novo = novo.trim();
        if (!novo) return;
        c.titulo = novo.slice(0, 80);
        c.tituloManual = true;
        salvar();
        renderSidebar();
        if (c.id === estado.atualId) tituloConversa.textContent = c.titulo;
      }
      function registrarUsuario(display, content, nomeAnexo){
        var c = conversaAtual(); if (!c) return;
        c.mensagens.push({ role: 'usuario', display: display, content: content, anexoNome: nomeAnexo || null });
        c.atualizadoEm = agora();
        tituloAuto(c);
        salvar();
        renderSidebar();
      }
      function registrarAgente(display, content){
        var c = conversaAtual(); if (!c) return null;
        var msg = { id: gerarId(), role: 'agente', display: display, content: content, feedback: null };
        c.mensagens.push(msg);
        c.atualizadoEm = agora();
        salvar();
        return msg;
      }
      function tituloAuto(c){
        if (c.tituloManual) return;
        for (var i = 0; i < c.mensagens.length; i++){
          if (c.mensagens[i].role === 'usuario'){
            var t = (c.mensagens[i].display || '').trim();
            if (t){ c.titulo = t.slice(0, 48) + (t.length > 48 ? '…' : ''); }
            break;
          }
        }
        if (c.id === estado.atualId) tituloConversa.textContent = c.titulo;
      }
      var MAX_HIST_ENVIO = 30;
      function historicoParaModelo(){
        var c = conversaAtual(); if (!c) return [];
        var msgs = c.mensagens.filter(function(m){ return m.role === 'usuario' || m.role === 'agente'; });
        if (msgs.length > MAX_HIST_ENVIO){
          msgs = msgs.slice(0, 2).concat(msgs.slice(msgs.length - (MAX_HIST_ENVIO - 2)));
        }
        return msgs.map(function(m){
          return { role: m.role === 'usuario' ? 'user' : 'assistant', content: m.content };
        });
      }

      function renderSidebar(){
        listaConversas.innerHTML = '';
        var lista = ordenadas();
        if (!lista.length){
          var vazio = document.createElement('div');
          vazio.className = 'vazio';
          vazio.textContent = 'Nenhuma conversa ainda.';
          listaConversas.appendChild(vazio);
          return;
        }
        lista.forEach(function(c){
          var row = document.createElement('div');
          row.className = 'conv' + (c.id === estado.atualId ? ' ativa' : '');
          row.title = c.titulo;

          var tit = document.createElement('span');
          tit.className = 'titulo';
          tit.textContent = c.titulo;
          tit.addEventListener('click', function(){ abrir(c.id); });
          row.appendChild(tit);

          var acoes = document.createElement('span');
          acoes.className = 'acoes';
          var bRen = document.createElement('button');
          bRen.type = 'button'; bRen.title = 'Renomear'; bRen.innerHTML = '&#9998;';
          bRen.addEventListener('click', function(ev){ ev.stopPropagation(); renomear(c.id); });
          var bDel = document.createElement('button');
          bDel.type = 'button'; bDel.title = 'Apagar'; bDel.innerHTML = '&#128465;';
          bDel.addEventListener('click', function(ev){ ev.stopPropagation(); apagar(c.id); });
          acoes.appendChild(bRen); acoes.appendChild(bDel);
          row.appendChild(acoes);

          row.addEventListener('click', function(){ abrir(c.id); });
          listaConversas.appendChild(row);
        });
      }

      function renderChat(){
        var c = conversaAtual();
        chat.innerHTML = '';
        tituloConversa.textContent = c ? c.titulo : 'Auditor';
        if (!c || !c.mensagens.length){
          mostrarBoasVindas();
          return;
        }
        c.mensagens.forEach(function(m){ addMsg(m.role, m.display, m.anexoNome, m); });
        rolar();
      }
      function mostrarBoasVindas(){
        var bv = document.createElement('div');
        bv.className = 'boas-vindas';
        bv.innerHTML =
          '<img src="/static/logo-tag-auditor.png" alt="">' +
          '<h2></h2>' +
          '<p>Anexe o plano de tagueamento (.json) para auditar, ou digite uma pergunta.</p>';
        // nome da plataforma (config.py) entra como texto, nunca como HTML
        bv.querySelector('h2').textContent = 'Olá! Sou o Auditor do ' +
          ((CFG.plataforma && CFG.plataforma.nome) || 'Tag Studio') + '.';
        chat.appendChild(bv);
      }

      function rolar(){ chat.scrollTop = chat.scrollHeight; }

      function addMsg(tipo, texto, nomeAnexo, msgRef){
        var bv = chat.querySelector('.boas-vindas');
        if (bv) bv.remove();

        var div = document.createElement('div');
        div.className = 'msg ' + tipo;
        if (tipo === 'agente'){
          var barra = document.createElement('div'); barra.className = 'barra-copiar';
          var btn = document.createElement('button'); btn.className = 'btn-copiar'; btn.type = 'button'; btn.textContent = '⧉ Copiar';
          btn.addEventListener('click', function(){
            var ok = false;
            try { ok = TA.copiarTexto(texto); } catch (e) {}
            btn.textContent = ok ? '✓ Copiado' : '❌ Erro';
            btn.classList.toggle('ok', ok);
            setTimeout(function(){ btn.textContent = '⧉ Copiar'; btn.classList.remove('ok'); }, 1800);
          });
          barra.appendChild(btn); div.appendChild(barra);
          var corpo = document.createElement('div'); corpo.className = 'md'; corpo.innerHTML = TA.renderMarkdown(texto); div.appendChild(corpo);
          div.appendChild(criarFeedback(msgRef));
        } else {
          div.textContent = texto;
        }
        if (nomeAnexo){
          var linha = document.createElement('div');
          linha.className = 'anexo-linha';
          linha.textContent = '📎 ' + nomeAnexo;
          div.appendChild(linha);
        }
        chat.appendChild(div);
        rolar();
      }

      // Barra de avaliação da resposta (👍/👐). Guarda o voto na mensagem
      // (localStorage) e envia ao servidor para o painel admin medir satisfação.
      function criarFeedback(msgRef){
        var barra = document.createElement('div');
        barra.className = 'feedback';
        var pergunta = document.createElement('span');
        pergunta.className = 'feedback-txt';
        pergunta.textContent = 'Esta auditoria ajudou?';
        var SVG_LIKE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10v11H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1zm0 0 5-8a2.2 2.2 0 0 1 3 2.2L14 8h5.2a2 2 0 0 1 2 2.3l-1.3 8a2 2 0 0 1-2 1.7H7z"></path></svg>';
        var SVG_DIS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 14V3h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1zm0 0-5 8a2.2 2.2 0 0 1-3-2.2L10 16H4.8a2 2 0 0 1-2-2.3l1.3-8a2 2 0 0 1 2-1.7H17z"></path></svg>';
        var bLike = document.createElement('button');
        bLike.type = 'button'; bLike.className = 'fb-btn fb-like'; bLike.title = 'Ajudou';
        bLike.setAttribute('aria-label', 'Ajudou'); bLike.innerHTML = SVG_LIKE;
        var bDis = document.createElement('button');
        bDis.type = 'button'; bDis.className = 'fb-btn fb-dis'; bDis.title = 'Não ajudou';
        bDis.setAttribute('aria-label', 'Não ajudou'); bDis.innerHTML = SVG_DIS;

        function pintar(){
          var v = msgRef && msgRef.feedback;
          bLike.classList.toggle('ativo', v === 'like');
          bDis.classList.toggle('ativo', v === 'dislike');
          if (v) pergunta.textContent = 'Obrigado pelo retorno!';
        }

        function votar(voto){
          if (!msgRef) return;
          var jaEra = msgRef.feedback;
          msgRef.feedback = voto;
          salvar();
          pintar();
          if (voto === 'dislike') caixaComentario.style.display = 'flex';
          // Mesmo voto de novo (clique repetido): não registra outro evento —
          // evita inflar a trilha e a contagem de 👍/👎 no painel admin.
          if (jaEra === voto) return;
          TA.api.feedback({
            voto: voto,
            session_id: conversaAtual() ? conversaAtual().id : 'sem-sessao',
            msg_id: msgRef.id || '',
            comentario: (msgRef.feedbackComentario || '')
          }).catch(function(){ /* sem retorno visível: não atrapalha o uso */ });
        }
        bLike.addEventListener('click', function(){ votar('like'); });
        bDis.addEventListener('click', function(){ votar('dislike'); });

        // Motivo opcional quando não ajudou (ajuda a melhorar as regras)
        var caixaComentario = document.createElement('div');
        caixaComentario.className = 'fb-motivo';
        caixaComentario.style.display = 'none';
        var inp = document.createElement('input');
        inp.type = 'text'; inp.maxLength = 500;
        inp.placeholder = 'Opcional: o que faltou ou saiu errado?';
        var bEnv = document.createElement('button');
        bEnv.type = 'button'; bEnv.className = 'fb-enviar'; bEnv.textContent = 'Enviar';
        bEnv.addEventListener('click', function(){
          if (!msgRef) return;
          msgRef.feedbackComentario = inp.value.trim();
          salvar();
          TA.api.feedback({
            voto: 'dislike',
            session_id: conversaAtual() ? conversaAtual().id : 'sem-sessao',
            msg_id: msgRef.id || '',
            comentario: msgRef.feedbackComentario
          }).then(function(){
            caixaComentario.innerHTML = '<span class="feedback-txt">Enviado. Obrigado!</span>';
          }).catch(function(){});
        });
        caixaComentario.appendChild(inp);
        caixaComentario.appendChild(bEnv);

        barra.appendChild(pergunta);
        barra.appendChild(bLike);
        barra.appendChild(bDis);
        var wrap = document.createElement('div');
        wrap.appendChild(barra);
        wrap.appendChild(caixaComentario);
        pintar();
        if (msgRef && msgRef.feedback === 'dislike') caixaComentario.style.display = 'flex';
        return wrap;
      }

      var indicador = null;
      function mostrarDigitando(ligar){
        if (ligar && !indicador){
          indicador = document.createElement('div');
          indicador.className = 'digitando';
          indicador.innerHTML = '<span></span><span></span><span></span>';
          chat.appendChild(indicador);
          rolar();
        } else if (!ligar && indicador){
          indicador.remove();
          indicador = null;
        }
      }

      function travar(ligar){
        enviando = ligar;
        btnEnviar.disabled = ligar;
        btnAnexo.disabled = ligar;
        if (btnConfluence) btnConfluence.disabled = ligar;
        campo.disabled = ligar;
        Array.prototype.forEach.call(botoesCanal, function(b){ b.disabled = ligar; });
      }
      function limparAnexo(){
        anexo = null;
        inputArquivo.value = '';
        chip.style.display = 'none';
        resetarCanal();
      }

      // ---- Canal do plano (obrigatório quando há anexo) -------------------
      // O agente não deduz o canal a partir do JSON; com um plano anexado o
      // usuário precisa escolher no seletor ou escrever na mensagem.
      function canalNoTexto(t){
        return /canal\b\s*(?:é|eh|do|de|=|:|-|–|—)?\s*["']?(?:web\s?view|web|app(?:\s*nativo)?|nativo|mobile|aplicativo)\b/i.test(t || '');
      }
      function mostrarSeletorCanal(){
        if (!canalArea) return;
        canalArea.style.display = 'flex';
        canalArea.classList.remove('erro', 'atencao');
        void canalArea.offsetWidth; // reinicia a animação de destaque
        canalArea.classList.add('atencao');
      }
      function resetarCanal(){
        canalSelecionado = null;
        if (!canalArea) return;
        canalArea.style.display = 'none';
        canalArea.classList.remove('erro', 'atencao');
        Array.prototype.forEach.call(botoesCanal, function(b){
          b.classList.remove('ativo');
          b.setAttribute('aria-pressed', 'false');
        });
      }
      function exigirCanal(){
        if (!canalArea) return;
        canalArea.classList.remove('erro', 'atencao');
        void canalArea.offsetWidth; // reinicia a animação de "shake"
        canalArea.classList.add('erro');
        if (canalArea.scrollIntoView) canalArea.scrollIntoView({ block: 'nearest' });
      }
      Array.prototype.forEach.call(botoesCanal, function(b){
        b.addEventListener('click', function(){
          if (enviando) return;
          canalSelecionado = b.getAttribute('data-canal');
          canalArea.classList.remove('erro', 'atencao');
          Array.prototype.forEach.call(botoesCanal, function(x){
            var ativo = (x === b);
            x.classList.toggle('ativo', ativo);
            x.setAttribute('aria-pressed', ativo ? 'true' : 'false');
          });
        });
      });

      function enviarUmaParte(texto, jsonParte, historico, sessao){
        return TA.api.enviar({
          texto: texto, json_anexo: jsonParte,
          historico: historico || [], session_id: sessao
        });
      }

      function enviarEmPartes(texto, partes, ctx){
        var sessao = conversaAtual() ? conversaAtual().id : 'sem-sessao';
        registrarUsuario(ctx.userDisplay, '[Plano grande auditado em ' + partes.length + ' partes — conteúdo não reenviado por tamanho]', ctx.nomeAnexo);
        var info = document.createElement('div');
        info.className = 'msg sistema';
        chat.appendChild(info);
        travar(true);
        mostrarDigitando(true);

        var i = 0, falhas = [];
        (function proxima(){
          if (i >= partes.length){
            mostrarDigitando(false);
            travar(false);
            info.textContent = 'Auditoria concluída: ' + partes.length + ' partes processadas' +
              (falhas.length ? ' — ' + falhas.length + ' falharam (partes: ' + falhas.join(', ') + '). Reenvie o arquivo mais tarde para tentar de novo.' : '.');
            rolar(); campo.focus();
            return;
          }
          var n = i + 1;
          info.textContent = 'Plano grande: dividido em ' + partes.length + ' partes. Auditando parte ' + n + ' de ' + partes.length + '... (não feche esta aba)';
          rolar();
          var sessaoParte = sessao + ('00' + n).slice(-3);
          enviarUmaParte(texto, partes[i], [], sessaoParte)
            .then(function(res){
              if (res.ok){
                var disp = '#### 📄 Parte ' + n + ' de ' + partes.length + '\n\n' + (res.dados.resposta || '(sem conteúdo)');
                var msgP = registrarAgente(disp, disp);
                addMsg('agente', disp, null, msgP);
              } else {
                falhas.push(n); addMsg('erro', 'Parte ' + n + '/' + partes.length + ': ' + (res.dados.erro || 'Erro desconhecido.'));
              }
            })
            .catch(function(e){ falhas.push(n); addMsg('erro', 'Parte ' + n + '/' + partes.length + ': ' + e.message); })
            .then(function(){ i++; proxima(); });
        })();
      }

      function enviar(){
        if (enviando) return;
        var texto = campo.value.trim();
        if (!texto && !anexo) return;

        // Canal obrigatório com plano anexado: escolhido no seletor ou
        // escrito na própria mensagem (ex.: "Canal: Web").
        var temCanalNoTexto = canalNoTexto(texto);
        if (anexo && !canalSelecionado && !temCanalNoTexto){
          exigirCanal();
          return;
        }

        garantirConversa();

        var nomeAnexo = anexo ? anexo.nome : null;
        var conteudo = anexo ? anexo.conteudo : null;
        var canal = (anexo && !temCanalNoTexto) ? canalSelecionado : null;

        var textoEnvio = texto;
        var displayUser = texto || 'Auditar o plano de tagueamento anexado.';
        if (canal){
          textoEnvio = texto
            ? texto + '\n\nCanal: ' + canal + '.'
            : 'Audite o seguinte plano de tagueamento. Canal: ' + canal + '.';
          displayUser = texto
            ? texto + '\nCanal: ' + canal
            : 'Auditar o plano de tagueamento anexado — Canal: ' + canal + '.';
        }

        addMsg('usuario', displayUser, nomeAnexo);
        campo.value = '';
        campo.style.height = 'auto';
        limparAnexo();

        if (conteudo && conteudo.length > LIMITE_PARTE){
          var partes = TA.plano.dividirPlano(conteudo, LIMITE_PARTE);
          if (partes){
            enviarEmPartes(textoEnvio, partes, { userDisplay: displayUser, nomeAnexo: nomeAnexo });
            return;
          }
        }

        var historico = historicoParaModelo();
        var sessao = conversaAtual() ? conversaAtual().id : 'sem-sessao';
        travar(true);
        mostrarDigitando(true);
        enviarUmaParte(textoEnvio, conteudo, historico, sessao)
        .then(function(res){
          mostrarDigitando(false);
          if (res.ok){
            var resp = res.dados.resposta || '(sem conteúdo)';
            registrarUsuario(displayUser, res.dados.user_content || displayUser, nomeAnexo);
            var msgAg = registrarAgente(resp, resp);
            addMsg('agente', resp, null, msgAg);
          } else {
            addMsg('erro', res.dados.erro || 'Erro desconhecido.');
          }
        })
        .catch(function(e){
          mostrarDigitando(false);
          addMsg('erro', 'Falha ao falar com o servidor (a janela preta está aberta?): ' + e.message);
        })
        .finally(function(){
          travar(false);
          campo.focus();
        });
      }

      // Valida, limpa e anexa um plano (vindo de arquivo local OU do Confluence)
      function processarPlano(nomeArquivo, textoJson){
        var obj;
        try { obj = JSON.parse(textoJson); }
        catch(e){
          addMsg('erro', 'O arquivo "' + nomeArquivo + '" não contém um JSON válido: ' + e.message);
          return false;
        }
        var stats = { campos: 0, chars: 0 };
        var semImagens = TA.plano.limparB64(obj, stats);
        var brutoLen = JSON.stringify(semImagens).length;
        var res = ENXUGAR ? TA.plano.enxugarPlano(semImagens) : { obj: semImagens, aplicado: false };
        var limpo = JSON.stringify(res.obj);
        anexo = { nome: nomeArquivo, conteudo: limpo };
        var kb = Math.max(1, Math.round(limpo.length / 1024));
        var reduziu = res.aplicado && (brutoLen - limpo.length) > 0;
        chipNome.textContent = nomeArquivo + ' (' + kb + ' KB' + (stats.campos ? ' · sem imagens' : '') + (reduziu ? ' · enxuto' : '') + ')';
        chip.style.display = 'flex';
        mostrarSeletorCanal();
        if (stats.campos){
          addMsg('sistema', 'Anexo otimizado: ' + stats.campos + ' campo(s) de imagem/base64 removido(s) (' +
            Math.max(1, Math.round(stats.chars / 1024)) + ' KB a menos). Só o conteúdo auditável do plano será enviado.');
        }
        if (reduziu){
          addMsg('sistema', 'Plano enxuto: mantidos apenas eventos, parâmetros e canal (' +
            Math.max(1, Math.round((brutoLen - limpo.length) / 1024)) + ' KB a menos). Conteúdo não auditável (DOM, capturas, metadados) removido.');
        }
        return true;
      }

      inputArquivo.addEventListener('change', function(ev){
        var f = ev.target.files[0];
        if (!f) return;
        if (!/\.json$/i.test(f.name)){
          addMsg('erro', 'Apenas arquivos .json são aceitos.');
          inputArquivo.value = '';
          return;
        }
        var leitor = new FileReader();
        leitor.onload = function(){
          if (!processarPlano(f.name, leitor.result)) inputArquivo.value = '';
        };
        leitor.onerror = function(){ addMsg('erro', 'Não consegui ler o arquivo.'); };
        leitor.readAsText(f, 'utf-8');
      });

      // ---- Confluence: cola a URL da página e o servidor busca o plano ----
      var SVG_CONFLUENCE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M.87 18.257c-.248.382-.53.875-.763 1.245a.764.764 0 0 0 .255 1.04l4.965 3.054a.764.764 0 0 0 1.058-.26c.199-.332.454-.763.733-1.221 1.967-3.247 3.945-2.853 7.508-1.146l4.957 2.337a.764.764 0 0 0 1.028-.382l2.364-5.346a.764.764 0 0 0-.382-1 599.851 599.851 0 0 1-4.965-2.361C10.911 10.97 5.224 11.185.87 18.257zM23.131 5.743c.249-.405.531-.875.764-1.25a.764.764 0 0 0-.256-1.034L18.675.404a.764.764 0 0 0-1.058.26c-.195.335-.451.763-.734 1.225-1.966 3.246-3.945 2.85-7.508 1.146L4.437.694a.764.764 0 0 0-1.027.382L1.046 6.422a.764.764 0 0 0 .382 1c1.039.49 3.105 1.467 4.965 2.361 6.698 3.246 12.392 3.029 16.738-4.04z"/></svg>';

      // Cartão de progresso com spinner e etapas — some quando a busca termina
      function criarLoaderConfluence(nomeAnexo){
        var passos = nomeAnexo
          ? ['Baixando "' + nomeAnexo + '" da página…', 'Validando o conteúdo do plano…', 'Quase lá…']
          : ['Conectando à página…', 'Procurando anexos .json…', 'Baixando o plano…', 'Quase lá…'];
        var box = document.createElement('div');
        box.className = 'loader-conf';
        box.setAttribute('role', 'status');
        box.setAttribute('aria-live', 'polite');
        box.innerHTML =
          '<div class="loader-conf-icone">' + SVG_CONFLUENCE + '<span class="loader-conf-anel"></span></div>' +
          '<div class="loader-conf-info">' +
            '<div class="loader-conf-titulo">Buscando o plano no Confluence' +
              '<span class="loader-conf-pontos"><span>.</span><span>.</span><span>.</span></span></div>' +
            '<div class="loader-conf-passo"></div>' +
          '</div>';
        var passoEl = box.querySelector('.loader-conf-passo');
        var i = 0;
        passoEl.textContent = passos[0];
        var timer = setInterval(function(){
          if (i < passos.length - 1){ i++; passoEl.textContent = passos[i]; }
        }, 2600);
        box.encerrar = function(){ clearInterval(timer); box.remove(); };
        return box;
      }

      function buscarConfluence(url, nome){
        travar(true);
        var loader = criarLoaderConfluence(nome);
        chat.appendChild(loader);
        rolar();
        TA.api.confluence(url, nome)
        .then(function(res){
          loader.encerrar();
          if (!res.ok){
            addMsg('erro', res.dados.erro || 'Falha ao buscar no Confluence.');
            return;
          }
          if (res.dados.opcoes){
            escolherAnexoConfluence(url, res.dados.opcoes);
            return;
          }
          var arq = res.dados.arquivo || {};
          if (processarPlano(arq.nome || 'plano.json', arq.conteudo || '')){
            addMsg('sistema', 'Plano "' + (arq.nome || 'plano.json') + '" obtido do Confluence. ' +
              'Selecione o canal do plano (obrigatório) e clique em Enviar.');
            campo.focus();
          }
        })
        .catch(function(e){
          loader.encerrar();
          addMsg('erro', 'Falha ao falar com o servidor: ' + e.message);
        })
        .finally(function(){ travar(false); });
      }

      function escolherAnexoConfluence(url, opcoes){
        var box = document.createElement('div');
        box.className = 'msg sistema';
        var titulo = document.createElement('div');
        titulo.innerHTML = 'A página tem <b>' + opcoes.length + ' anexos .json</b> (do mais novo para o mais antigo). Qual auditar?';
        box.appendChild(titulo);
        var linha = document.createElement('div');
        linha.style.cssText = 'display:flex; gap:8px; flex-wrap:wrap; justify-content:center; margin-top:10px;';
        opcoes.forEach(function(nomeAnexo){
          var b = document.createElement('button');
          b.type = 'button';
          b.textContent = nomeAnexo;
          b.style.cssText = 'border:1px solid var(--marinho); background:var(--marinho); color:#fff; ' +
            'border-radius:8px; padding:8px 14px; font-size:12.5px; font-weight:600; cursor:pointer; ' +
            'max-width:100%; overflow:hidden; text-overflow:ellipsis;';
          b.addEventListener('click', function(){
            box.remove();
            buscarConfluence(url, nomeAnexo);
          });
          linha.appendChild(b);
        });
        box.appendChild(linha);
        chat.appendChild(box);
        rolar();
      }

      if (btnConfluence){
        btnConfluence.addEventListener('click', function(){
          var url = prompt('Cole a URL da página do Confluence com o plano de tagueamento:');
          if (url == null) return;
          url = url.trim();
          if (!url) return;
          garantirConversa();
          buscarConfluence(url, null);
        });
      }

      btnAnexo.addEventListener('click', function(){ inputArquivo.click(); });
      chipRemover.addEventListener('click', limparAnexo);
      btnEnviar.addEventListener('click', enviar);
      btnNovo.addEventListener('click', novaConversa);

      campo.addEventListener('input', function(){
        campo.style.height = 'auto';
        campo.style.height = Math.min(campo.scrollHeight, 140) + 'px';
      });
      campo.addEventListener('keydown', function(ev){
        if (ev.key === 'Enter' && !ev.shiftKey){
          ev.preventDefault();
          enviar();
        }
      });

      chat.addEventListener('click', function(ev){
        var alvo = ev.target;
        while (alvo && alvo !== chat && !(alvo.classList && alvo.classList.contains('btn-toggle-ok'))){
          alvo = alvo.parentNode;
        }
        if (!alvo || alvo === chat) return;
        var wrap = alvo;
        while (wrap && !(wrap.classList && wrap.classList.contains('tabela-wrap'))){
          wrap = wrap.parentNode;
        }
        if (!wrap) return;
        var mostrando = wrap.classList.toggle('mostrar-ok');
        alvo.textContent = (mostrando ? 'Ocultar ' : 'Mostrar ')
          + alvo.getAttribute('data-ocultas') + ' sem ajustes';
      });

      var LS_SIDEBAR = 'tagauditor_sidebar_aberta';
      function salvarSidebar(){ try { localStorage.setItem(LS_SIDEBAR, app.classList.contains('sb-aberta') ? '1' : '0'); } catch(e){} }
      function carregarSidebar(){ try { if (localStorage.getItem(LS_SIDEBAR) === '1') app.classList.add('sb-aberta'); } catch(e){} }
      function alternarSidebar(){ app.classList.toggle('sb-aberta'); salvarSidebar(); }
      function fecharSidebar(){ app.classList.remove('sb-aberta'); salvarSidebar(); }
      function fecharSidebarSeMobile(){ if (window.matchMedia('(max-width:760px)').matches) fecharSidebar(); }
      btnMenu.addEventListener('click', alternarSidebar);
      overlay.addEventListener('click', fecharSidebar);

      carregar();
      carregarSidebar();
      garantirConversa();
      podarVazias();
      salvar();
      renderSidebar();
      renderChat();
      campo.focus();
  };
})();
