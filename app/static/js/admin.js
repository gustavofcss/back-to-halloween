// Convenção AJAX do admin (ver .dev-squad/memory.md): toda mutação vira
// fetch() com o header X-Requested-With, envelope JSON único
// {ok, aplicados, ignorados, totais, aviso} -- sem isso a página inteira
// recarregava a cada confirmar/cancelar/apagar (achado real reportado
// pelo usuário: lentidão sentida na parte de admin).
(function () {
  var overlay = document.getElementById('confirm-overlay');
  var textEl = document.getElementById('confirm-modal-text');
  var iconEl = document.getElementById('confirm-modal-icon');
  var btnOk = document.getElementById('confirm-modal-ok');
  var btnCancel = document.getElementById('confirm-modal-cancel');
  var toast = document.getElementById('admin-toast');
  var toastTimer = null;
  var formPendente = null;
  var actionPendente = null;

  function mostrarToast(msg, tipo) {
    if (!toast || !msg) { return; }
    toast.textContent = msg;
    toast.className = 'admin-toast admin-toast--visivel' + (tipo ? ' admin-toast--' + tipo : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toast.className = 'admin-toast';
    }, 5000);
  }

  function atualizarTotais(totais) {
    if (!totais) { return; }
    Object.keys(totais).forEach(function (chave) {
      document.querySelectorAll('[data-admin-total="' + chave + '"]').forEach(function (el) {
        el.textContent = totais[chave];
      });
    });
    atualizarBarras();
  }

  // O número muda sozinho (data-admin-total, acima), mas a largura da
  // barra e o "restantes" (card Vagas + card do evento na sidebar) não --
  // recalcula os dois a partir do número que acabou de ser escrito no DOM.
  function atualizarBarras() {
    var v = document.querySelector('[data-admin-total="ingressos_vendidos_total"]');
    var c = document.querySelector('[data-admin-total="capacidade_maxima"]');
    if (!v || !c) { return; }
    var vendidos = parseInt(v.textContent, 10) || 0;
    var capacidade = parseInt(c.textContent, 10) || 0;
    var pct = capacidade ? Math.min(100, Math.max(2, Math.round(vendidos / capacidade * 100))) : 0;
    document.querySelectorAll('[data-admin-bar] > div').forEach(function (el) { el.style.width = pct + '%'; });
    document.querySelectorAll('[data-admin-restantes]').forEach(function (el) { el.textContent = Math.max(0, capacidade - vendidos); });
  }
  // Exposta pra window.iniciarPollingAdmin (definido mais abaixo) também
  // poder atualizar os KPIs/pílulas quando o polling encontra item novo,
  // sem duplicar essa lógica.
  window.atualizarTotaisAdmin = atualizarTotais;

  function desmarcarSelecao(form) {
    if (!form) { return; }
    form.querySelectorAll('input[type="checkbox"]').forEach(function (cb) {
      if (cb.checked) {
        cb.checked = false;
        cb.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
  }

  // Envia uma ação de mutação do admin via fetch() -- cada página escuta
  // o evento 'admin:sucesso' pra decidir o que fazer na tela (tirar uma
  // linha, trocar um selo de status etc.), porque só a própria página
  // sabe como sua lista deve reagir a cada rota.
  window.enviarAcaoAdmin = function (form, actionUrl) {
    var lote = /-lote$/.test(actionUrl);
    var headers = { 'X-Requested-With': 'XMLHttpRequest' };
    var corpo = null;
    if (lote) {
      var ids = Array.prototype.map.call(
        form.querySelectorAll('input[name="ids"]:checked'),
        function (cb) { return parseInt(cb.value, 10); }
      );
      headers['Content-Type'] = 'application/json';
      corpo = JSON.stringify({ ids: ids });
    }

    return fetch(actionUrl, { method: 'POST', headers: headers, body: corpo, credentials: 'same-origin' })
      .then(function (resp) {
        return resp.json()
          .catch(function () { return null; })
          .then(function (dados) { return { status: resp.status, dados: dados }; });
      })
      .then(function (resultado) {
        var dados = resultado.dados;
        if (!dados) {
          mostrarToast('Falha inesperada. Recarregue a página e tente de novo.', 'erro');
          return;
        }
        if (!dados.ok) {
          if (dados.codigo === 'auth' && dados.login_url) {
            window.location.href = dados.login_url;
            return;
          }
          mostrarToast(dados.erro || 'Não foi possível concluir a ação.', 'erro');
          return;
        }

        atualizarTotais(dados.totais);
        if (lote) { desmarcarSelecao(form); }
        document.dispatchEvent(new CustomEvent('admin:sucesso', {
          detail: { actionUrl: actionUrl, dados: dados, form: form }
        }));

        // Os dois avisos são independentes (um lote pode ter itens
        // ignorados E falha de e-mail ao mesmo tempo) -- concatena em vez
        // de só mostrar um, senão o aviso de e-mail (que pede ação manual)
        // podia sumir silenciosamente atrás do aviso de "já estava assim".
        var partesAviso = [];
        if (dados.ignorados && dados.ignorados.length) {
          var motivos = dados.ignorados.map(function (i) { return i.motivo; });
          var extra = motivos.length > 1 ? ' (+' + (motivos.length - 1) + ')' : '';
          partesAviso.push(motivos[0] + extra);
        }
        if (dados.aviso) { partesAviso.push(dados.aviso); }
        if (partesAviso.length) { mostrarToast(partesAviso.join(' '), 'aviso'); }
      })
      .catch(function () {
        mostrarToast('Falha de conexão. Verifique sua internet e tente de novo.', 'erro');
      });
  };

  // Ícone + botão "Confirmar" vermelho só em ações destrutivas
  // (apagar/remover/recusar, sem volta) -- `perigoso` é passado
  // explicitamente por cada chamador, não adivinhado pela URL/mensagem.
  function abrirModal(perigoso) {
    overlay.style.display = 'flex';
    btnOk.classList.toggle('is-perigoso', !!perigoso);
    if (iconEl) { iconEl.classList.toggle('is-hidden', !perigoso); }
    btnCancel.focus();
  }

  window.confirmarAcao = function (form, mensagem, perigoso) {
    formPendente = form;
    actionPendente = form.getAttribute('action');
    textEl.textContent = mensagem;
    abrirModal(perigoso);
    return false;
  };

  // Igual confirmarAcao, mas pra ação em lote: o form tem vários botões
  // de submit com formaction diferente (confirmar/cancelar/apagar em
  // lote), então recebe a action certa por fora em vez de ler do form.
  window.confirmarAcaoLote = function (form, action, mensagem, perigoso) {
    formPendente = form;
    actionPendente = action;
    textEl.textContent = mensagem;
    abrirModal(perigoso);
    return false;
  };

  btnOk.addEventListener('click', function () {
    overlay.style.display = 'none';
    if (formPendente) { window.enviarAcaoAdmin(formPendente, actionPendente); }
    formPendente = null;
    actionPendente = null;
  });
  btnCancel.addEventListener('click', function () {
    overlay.style.display = 'none';
    formPendente = null;
    actionPendente = null;
  });
  // Esc fecha o modal de confirmação (teclado)
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && overlay.style.display === 'flex') { btnCancel.click(); }
  });

  overlay.addEventListener('click', function (e) {
    if (e.target === overlay) {
      overlay.style.display = 'none';
      formPendente = null;
      actionPendente = null;
    }
  });

  // Botões de ação direta (Confirmar, Promover, Rebaixar...) continuam
  // <button type="submit" formaction="...">, sem onclick nenhum -- só
  // precisam que o form tenha a classe admin-ajax-form pra virar fetch()
  // em vez de navegação cheia.
  document.querySelectorAll('form.admin-ajax-form').forEach(function (form) {
    form.addEventListener('submit', function (evt) {
      evt.preventDefault();
      var submitter = evt.submitter;
      var actionUrl = (submitter && submitter.getAttribute('formaction')) || form.getAttribute('action');
      if (!actionUrl) { return; }
      // Ação que não tem volta (confirmar pagamento, cancelar pedido pago,
      // promover a admin...) pede confirmação num modal. {n} vira a quantidade
      // de itens marcados, pra o admin ver QUANTOS serão afetados.
      var mensagem = submitter && submitter.getAttribute('data-confirmar');
      if (mensagem) {
        var marcados = /-lote$/.test(actionUrl) ? form.querySelectorAll('input[name="ids"]:checked').length : 1;
        if (marcados === 0) { return; }
        window.confirmarAcaoLote(form, actionUrl, mensagem.replace('{n}', marcados), submitter.hasAttribute('data-perigoso'));
        return;
      }
      window.enviarAcaoAdmin(form, actionUrl);
    });
  });
})();

// Escapa string do usuário (nome, e-mail, descrição...) antes de entrar
// em innerHTML -- o Jinja escapa sozinho no HTML que o servidor manda,
// mas o polling abaixo monta HTML NO CLIENTE a partir de JSON, então
// precisa escapar na mão pra não abrir um caminho de XSS armazenado
// (nome de usuário/material vira HTML cru sem isso).
window.escaparHtmlAdmin = function (valor) {
  var mapa = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(valor == null ? '' : valor).replace(/[&<>"']/g, function (c) { return mapa[c]; });
};

// Polling leve por página (10s em Pedidos/Cadastros/Hierarquia, 30s em
// Material -- cada bloco abaixo chama isso com sua própria config) no
// lugar do location.reload() de 60s que existia antes: reload cheio
// perdia scroll/seleção e fazia item novo "surgir do nada" (achado real,
// pedido do usuário). Busca só a lista via fetch() e insere com uma
// animação suave só o que é realmente NOVO -- o que já estava na tela
// não é tocado. Pausa se o admin estiver digitando em algum campo ou com
// o modal de confirmação aberto (não precisa mais pausar por seleção em
// lote marcada -- essa técnica não mexe nas linhas existentes).
window.iniciarPollingAdmin = function (opts) {
  setInterval(function () {
    var ativo = document.activeElement;
    var editando = ativo && ['INPUT', 'TEXTAREA', 'SELECT'].indexOf(ativo.tagName) !== -1;
    var overlayEl = document.getElementById('confirm-overlay');
    var modalAberto = overlayEl && overlayEl.style.display === 'flex';
    // aba em segundo plano: não gasta requisição à toa (volta no próximo ciclo)
    if (document.hidden || editando || modalAberto) { return; }

    fetch(opts.url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (resp) { return resp.ok ? resp.json() : null; })
      .then(function (dados) {
        if (!dados || !dados.itens) { return; }
        var naLista = new Set();
        dados.itens.forEach(function (item) {
          naLista.add(item.id);
          if (opts.idsConhecidos.has(item.id)) {
            // já estava na tela: o status pode ter mudado (Pix conciliado, pedido
            // expirado, ação de outro admin) -- antes só linha NOVA era tratada
            if (opts.atualizarExistente) { opts.atualizarExistente(item); }
            return;
          }
          opts.idsConhecidos.add(item.id);
          opts.inserirNovo(item);
        });
        // saiu da lista (apagado/aprovado por outro admin): tira da tela também
        if (opts.removerAusentes) {
          Array.from(opts.idsConhecidos).forEach(function (id) {
            if (!naLista.has(id)) { opts.idsConhecidos.delete(id); opts.removerAusentes(id); }
          });
        }
        // totais e selos a CADA ciclo, não só quando chega item novo
        window.atualizarTotaisAdmin(dados.totais);
        if (opts.depois) { opts.depois(); }
      })
      .catch(function () {});
  }, opts.intervaloMs);
};

// Qual página do admin estamos -- <main class="a-main" data-page="...">
// (ver admin/_layout.html) carrega o active_nav de cada template. Cada
// bloco abaixo só roda na sua própria página, pra não anexar 5 pollings
// simultâneos (um por seção) em toda página do admin.
var paginaAdmin = (function () {
  var main = document.querySelector('.a-main');
  return main ? main.dataset.page : '';
})();

// Busca + seleção em lote de uma lista (Pedidos, Cadastros, Hierarquia).
// A busca só ESCONDE as linhas (display:none): "Selecionar todos" marca só as que
// estão à vista, e filtrar desmarca o que ficou escondido. Antes marcava todas, e
// "Cancelar/Confirmar/Promover selecionados" atingia gente que o admin nem estava
// vendo. Tudo é consultado AO VIVO (as linhas mudam: polling, apagar...), por isso
// as linhas que o polling insere já entram na busca e no "Selecionar todos".
window.ligarListaAdmin = function (cfg) {
  var checkAll = document.getElementById(cfg.checkAll);
  var bar = document.getElementById(cfg.bar);
  var contagem = document.getElementById(cfg.contagem);
  var busca = cfg.busca ? document.getElementById(cfg.busca) : null;

  function linhas() { return Array.prototype.slice.call(document.querySelectorAll(cfg.linhas)); }
  function itens() { return Array.prototype.slice.call(document.querySelectorAll(cfg.item)); }
  function escondida(item) {
    var linha = item.closest(cfg.linhas);
    return !!linha && linha.style.display === 'none';
  }

  function atualizar() {
    var marcados = document.querySelectorAll(cfg.item + ':checked').length;
    var visiveis = itens().filter(function (i) { return !escondida(i); });
    if (bar) { bar.classList.toggle('admin-bulk-bar--visivel', marcados > 0); }
    if (contagem) { contagem.textContent = marcados + ' selecionado(s)'; }
    if (checkAll) { checkAll.checked = visiveis.length > 0 && visiveis.every(function (i) { return i.checked; }); }
  }

  function filtrar() {
    var q = busca ? busca.value.trim().toLowerCase() : '';
    linhas().forEach(function (linha) {
      linha.style.display = (!q || linha.textContent.toLowerCase().includes(q)) ? '' : 'none';
    });
    itens().forEach(function (i) { if (escondida(i)) { i.checked = false; } });
    atualizar();
  }

  if (busca) { busca.addEventListener('input', filtrar); }
  if (checkAll) {
    checkAll.addEventListener('change', function () {
      var marcar = checkAll.checked;
      itens().forEach(function (i) { i.checked = marcar && !escondida(i); });
      atualizar();
    });
  }
  document.addEventListener('change', function (evt) {
    if (evt.target.matches && evt.target.matches(cfg.item)) { atualizar(); }
  });
  atualizar();
  return { atualizar: atualizar, filtrar: filtrar };
};

function iniciaisDe(nome) {
  var partes = String(nome || '').split(' ');
  var s = (partes[0] ? partes[0][0] : '') + (partes.length > 1 && partes[1] ? partes[1][0] : '');
  return s.toUpperCase();
}

// admin/pedidos.html -- busca, seleção em lote, reação a admin:sucesso e
// polling (pedidos novos, status que mudaram sozinhos, pedidos que sumiram).
if (paginaAdmin === 'pedidos') {
  (function () {
    var pillClasses = { confirmado: 'admin-pill--ok', pendente: 'admin-pill--warn', cancelado: 'admin-pill--bad' };
    var tbody = document.querySelector('#admin-pedidos-table tbody');
    var lista = window.ligarListaAdmin({
      item: '.pedidos-check-item', linhas: '#admin-pedidos-table tbody tr',
      checkAll: 'pedidos-check-all', bar: 'pedidos-bulk-bar', contagem: 'pedidos-bulk-count',
      busca: 'admin-pedidos-search'
    });

    var MSG_CONFIRMAR = 'Confirmar o pagamento deste pedido? O cliente recebe o ingresso por e-mail.';
    var MSG_CANCELAR = 'Cancelar este pedido? A vaga é liberada.';
    var MSG_CANCELAR_PAGO = 'Cancelar este pedido JÁ PAGO? A vaga é liberada e o dinheiro precisa ser devolvido por fora.';

    function botao(acao, id, rotulo, classe, mensagem, perigoso) {
      return '<button type="submit" formaction="/admin/pedidos/' + id + '/' + acao + '" class="' + classe + '"' +
        ' data-confirmar="' + mensagem + '"' + (perigoso ? ' data-perigoso' : '') + '>' + rotulo + '</button>';
    }

    function acoesPedido(id, status) {
      if (status === 'pendente') {
        return botao('confirmar', id, 'Confirmar Pix', 'admin-btn-primary', MSG_CONFIRMAR, false) + ' ' +
          botao('cancelar', id, 'Cancelar', 'admin-btn-outline', MSG_CANCELAR, false);
      }
      if (status === 'confirmado') {
        return botao('cancelar', id, 'Cancelar', 'admin-btn-outline', MSG_CANCELAR_PAGO, true);
      }
      if (status === 'cancelado') {
        return '<button type="button" class="admin-btn-outline is-danger" onclick="return confirmarAcaoLote(document.getElementById(\'pedidos-lote-form\'), \'/admin/pedidos/' + id + '/apagar\', \'Apagar esse pedido cancelado? Não tem como desfazer.\', true)">Apagar</button>';
      }
      return '';
    }

    // Troca o selo de STATUS (não o primeiro .admin-pill da linha: numa cortesia o
    // primeiro é o selo "Cortesia") e os botões da linha.
    function aplicarStatus(linha, id, status) {
      var pill = linha.querySelector('td[data-label="Status"] .admin-pill');
      if (pill) {
        pill.className = 'admin-pill ' + (pillClasses[status] || 'admin-pill--neutral');
        pill.textContent = status;
      }
      var celula = linha.querySelector('td[data-label="Ações"]') || linha.querySelector('td:last-child');
      if (celula) { celula.innerHTML = acoesPedido(id, status); }
    }

    function linhaDoPedido(id) {
      var checkbox = document.querySelector('.pedidos-check-item[value="' + id + '"]');
      return checkbox ? checkbox.closest('tr') : null;
    }

    // Confirmar/cancelar trocam o selo e os botões da linha; apagar tira a linha.
    document.addEventListener('admin:sucesso', function (evt) {
      evt.detail.dados.aplicados.forEach(function (item) {
        var linha = linhaDoPedido(item.id);
        if (!linha) { return; }
        if (!item.status) { linha.remove(); return; }
        aplicarStatus(linha, item.id, item.status);
      });
      lista.atualizar();
    });

    var idsConhecidos = new Set(
      Array.prototype.map.call(document.querySelectorAll('.pedidos-check-item'), function (cb) {
        return parseInt(cb.value, 10);
      })
    );

    function inserirNovoPedido(item) {
      if (!tbody) { location.reload(); return; } // tabela vazia antes: não dá pra inserir linha sem recarregar essa vez
      var esc = window.escaparHtmlAdmin;
      var extra = item.acompanhante_nome ? '<span class="a-order__extra">+ ' + esc(item.acompanhante_nome) + '</span>' : '';
      var compradoPor = item.slot === 2 ? '<span class="a-order__extra">Comprado por ' + esc(item.comprador_nome) + '</span>' : '';
      var cortesiaTag = item.cortesia ? '<span class="admin-pill admin-pill--accent">Cortesia</span>' : '';
      var tr = document.createElement('tr');
      tr.className = 'a-tr a-item-novo';
      tr.innerHTML =
        '<td class="admin-table__check-col"><input type="checkbox" name="ids" value="' + item.id + '" class="pedidos-check-item" aria-label="Selecionar pedido #' + item.id + '"></td>' +
        '<td class="a-order__id" data-label="Pedido">#' + item.id + '</td>' +
        '<td class="a-order__client" data-label="Cliente"><span class="a-order__name">' + esc(item.nome) + '</span>' + cortesiaTag + extra + compradoPor +
        '<span class="a-order__email">' + esc(item.email) + '</span><span class="a-order__extra">' + esc(item.data) + '</span></td>' +
        '<td data-label="Qtd.">' + item.quantidade + '×</td>' +
        '<td class="a-order__value" data-label="Valor">R$ ' + item.valor + '</td>' +
        '<td data-label="Status"><span class="admin-pill ' + (pillClasses[item.status] || 'admin-pill--neutral') + '">' + esc(item.status) + '</span></td>' +
        '<td data-label="Ações">' + acoesPedido(item.id, item.status) + '</td>';
      tbody.insertBefore(tr, tbody.firstChild);
      lista.filtrar();  // a linha nova respeita a busca que está digitada
    }

    window.iniciarPollingAdmin({
      url: '/admin/pedidos/lista',
      intervaloMs: 10000,
      idsConhecidos: idsConhecidos,
      inserirNovo: inserirNovoPedido,
      atualizarExistente: function (item) {
        var linha = linhaDoPedido(item.id);
        var pill = linha && linha.querySelector('td[data-label="Status"] .admin-pill');
        if (pill && pill.textContent.trim() !== item.status) { aplicarStatus(linha, item.id, item.status); }
      },
      removerAusentes: function (id) {
        var linha = linhaDoPedido(id);
        if (linha) { linha.remove(); }
      },
      depois: function () { lista.atualizar(); },
    });
  })();
}

// admin/cadastros.html -- seleção em lote, reação a admin:sucesso e polling de
// cadastros pendentes (novos, e os que outro admin já aprovou/recusou).
if (paginaAdmin === 'cadastros') {
  (function () {
    var tbody = document.querySelector('#admin-cadastros-table tbody');
    var lista = window.ligarListaAdmin({
      item: '.cadastros-check-item', linhas: '#admin-cadastros-table tbody tr',
      checkAll: 'cadastros-check-all', bar: 'cadastros-bulk-bar', contagem: 'cadastros-bulk-count'
    });

    function linhaDoCadastro(id) {
      var checkbox = document.querySelector('.cadastros-check-item[value="' + id + '"]');
      return checkbox ? checkbox.closest('tr') : null;
    }

    // Confirmar e recusar sempre tiram o cadastro da lista de pendentes.
    document.addEventListener('admin:sucesso', function (evt) {
      evt.detail.dados.aplicados.forEach(function (item) {
        var linha = linhaDoCadastro(item.id);
        if (linha) { linha.remove(); }
      });
      lista.atualizar();
    });

    var idsConhecidos = new Set(
      Array.prototype.map.call(document.querySelectorAll('.cadastros-check-item'), function (cb) {
        return parseInt(cb.value, 10);
      })
    );

    function inserirNovoCadastro(item) {
      if (!tbody) { location.reload(); return; }
      var esc = window.escaparHtmlAdmin;
      if (document.querySelector('.a-empty')) { location.reload(); return; }
      var tr = document.createElement('tr');
      tr.className = 'a-tr a-tr--cadastros a-item-novo';
      tr.innerHTML =
        '<td class="admin-table__check-col"><input type="checkbox" name="ids" value="' + item.id + '" class="cadastros-check-item" aria-label="Selecionar ' + esc(item.nome) + '"></td>' +
        '<td class="a-order__namecell" data-label="Nome"><span class="avatar">' + esc(iniciaisDe(item.nome)) + '</span><span class="a-order__name">' + esc(item.nome) + '</span></td>' +
        '<td class="a-order__email" data-label="E-mail">' + esc(item.email) + '</td>' +
        '<td class="a-order__extra" data-label="Cadastrado em">' + esc(item.data) + '</td>' +
        '<td data-label="Ações">' +
        '<button type="submit" formaction="/admin/cadastros/' + item.id + '/confirmar" class="admin-btn-primary">Confirmar cadastro</button> ' +
        '<button type="button" class="admin-btn-outline is-danger" onclick="return confirmarAcaoLote(document.getElementById(\'cadastros-lote-form\'), \'/admin/cadastros/' + item.id + '/recusar\', \'Recusar e apagar esse cadastro?\', true)">Recusar</button>' +
        '</td>';
      tbody.insertBefore(tr, tbody.firstChild);
      lista.atualizar();
    }

    window.iniciarPollingAdmin({
      url: '/admin/cadastros/lista',
      intervaloMs: 10000,
      idsConhecidos: idsConhecidos,
      inserirNovo: inserirNovoCadastro,
      removerAusentes: function (id) {
        var linha = linhaDoCadastro(id);
        if (linha) { linha.remove(); }
      },
      depois: function () { lista.atualizar(); },
    });
  })();
}

// admin/hierarquia.html -- busca, seleção em lote de clientes, reação a
// admin:sucesso (promover/rebaixar/remover) e polling de clientes.
if (paginaAdmin === 'hierarquia') {
  (function () {
    var lista = window.ligarListaAdmin({
      item: '.clientes-check-item', linhas: '.admin-group__row',
      checkAll: 'clientes-check-all', bar: 'clientes-bulk-bar', contagem: 'clientes-bulk-count',
      busca: 'admin-hierarquia-search'
    });

    function linhaDaPessoa(id) {
      return document.querySelector('.admin-group__row[data-row-id="' + id + '"]');
    }

    // Promover/rebaixar/remover sempre tiram a pessoa do grupo em que ela
    // estava (cliente promovido some da lista de Clientes, admin rebaixado
    // some da lista de Administradores) -- por isso um único handler genérico
    // (tirar a linha da tela) cobre as 5 rotas de mutação da Hierarquia, single
    // e em lote. A pessoa aparece no grupo novo no próximo refresh da página.
    document.addEventListener('admin:sucesso', function (evt) {
      evt.detail.dados.aplicados.forEach(function (item) {
        var linha = linhaDaPessoa(item.id);
        if (linha) { linha.remove(); }
      });
      lista.atualizar();
    });

    var listaEl = document.querySelector('#clientes-lote-form .admin-group__list');
    var idsConhecidos = new Set(
      Array.prototype.map.call(document.querySelectorAll('.clientes-check-item'), function (cb) {
        return parseInt(cb.value, 10);
      })
    );

    function inserirNovoCliente(item) {
      if (!listaEl) { location.reload(); return; }
      var esc = window.escaparHtmlAdmin;
      var div = document.createElement('div');
      div.className = 'admin-group__row a-item-novo';
      div.setAttribute('data-row-id', item.id);
      div.innerHTML =
        '<div class="admin-group__row-inner">' +
        '<input type="checkbox" name="ids" value="' + item.id + '" class="clientes-check-item" aria-label="Selecionar ' + esc(item.nome) + '">' +
        '<span class="avatar">' + esc(iniciaisDe(item.nome)) + '</span>' +
        '<span class="admin-group__info"><span class="admin-group__name">' + esc(item.nome) + '</span><span class="admin-group__email">' + esc(item.email) + '</span></span>' +
        '<span class="admin-group__action admin-group__action--duplo">' +
        '<button type="submit" formaction="/admin/hierarquia/' + item.id + '/promover" class="admin-btn-primary" data-confirmar="Promover esta pessoa a ADMIN? Ela passa a ver pedidos, cadastros e e-mail em massa." data-perigoso>Promover a admin</button>' +
        '<button type="button" class="admin-btn-outline is-danger" onclick="return confirmarAcaoLote(document.getElementById(\'clientes-lote-form\'), \'/admin/hierarquia/' + item.id + '/remover\', \'Apagar esse cliente? Pedidos pendentes ou cancelados dele também são apagados; quem tem pedido confirmado não pode ser apagado. Não tem como desfazer.\', true)">Apagar</button>' +
        '</span>' +
        '</div>';
      listaEl.insertBefore(div, listaEl.firstChild);
      lista.filtrar();
    }

    window.iniciarPollingAdmin({
      url: '/admin/hierarquia/lista',
      intervaloMs: 10000,
      idsConhecidos: idsConhecidos,
      inserirNovo: inserirNovoCliente,
      removerAusentes: function (id) {
        var linha = linhaDaPessoa(id);
        if (linha) { linha.remove(); }
      },
      depois: function () { lista.atualizar(); },
    });
  })();
}

// admin/material.html -- reação a admin:sucesso (apagar tira o card) e
// polling de material novo.
if (paginaAdmin === 'material') {
  // Editar um material: o card troca a visão pelo formulário (CSS .is-editing).
  document.addEventListener('click', function (evt) {
    var botao = evt.target.closest('[data-material-editar], [data-material-cancelar]');
    if (!botao) { return; }
    var card = botao.closest('[data-material-id]');
    if (!card) { return; }
    var editando = botao.hasAttribute('data-material-editar');
    card.classList.toggle('is-editing', editando);
    if (editando) {
      var primeiro = card.querySelector('.a-material__edit input');
      if (primeiro) { primeiro.focus(); }
    }
  });

  document.addEventListener('admin:sucesso', function (evt) {
    evt.detail.dados.aplicados.forEach(function (item) {
      var card = document.querySelector('[data-material-id="' + item.id + '"]');
      if (card) { card.remove(); }
    });
  });

  (function () {
    var grid = document.querySelector('.a-material-grid');
    var idsConhecidos = new Set(
      Array.prototype.map.call(document.querySelectorAll('[data-material-id]'), function (card) {
        return parseInt(card.getAttribute('data-material-id'), 10);
      })
    );

    function inserirNovoMaterial(item) {
      if (!grid) { location.reload(); return; }
      var esc = window.escaparHtmlAdmin;
      var div = document.createElement('div');
      div.className = 'a-card a-item-novo';
      div.setAttribute('data-material-id', item.id);
      var nome = esc(item.nome);
      var id = parseInt(item.id, 10);
      div.innerHTML =
        '<span class="a-material__name a-material__view">' + nome + '</span>' +
        '<span class="a-material__desc a-material__view">' + esc(item.descricao) + '</span>' +
        '<span class="a-material__value a-material__view">R$ ' + esc(item.valor) + '</span>' +
        '<span class="a-material__meta a-material__view">' + esc(item.data) + ' &middot; ' + esc(item.cadastrado_por) + '</span>' +
        '<div class="a-material__actions a-material__view">' +
        '<button type="button" class="admin-btn-outline" data-material-editar>Editar</button>' +
        '<form method="post" action="/admin/material/' + id + '/apagar" onsubmit="return confirmarAcao(this, \'Apagar esse material?\', true)">' +
        '<button type="submit" class="admin-btn-outline is-danger">Apagar</button>' +
        '</form></div>' +
        '<form method="post" action="/admin/material/' + id + '/editar" class="a-material__edit">' +
        '<div class="field"><label for="mat-nome-' + id + '">Nome do material</label>' +
        '<input id="mat-nome-' + id + '" name="nome_material" class="input" value="' + nome + '" maxlength="160" required></div>' +
        '<div class="field"><label for="mat-valor-' + id + '">Valor (R$)</label>' +
        '<input id="mat-valor-' + id + '" name="valor" class="input" type="number" step="0.01" min="0.01" value="' + esc(item.valor) + '" required></div>' +
        '<div class="field"><label for="mat-desc-' + id + '">Descrição (opcional)</label>' +
        '<input id="mat-desc-' + id + '" name="descricao" class="input" value="' + esc(item.descricao_edicao) + '"></div>' +
        '<div class="row"><button type="submit" class="admin-btn-primary">Salvar</button>' +
        '<button type="button" class="admin-btn-outline" data-material-cancelar>Cancelar</button></div>' +
        '</form>';
      grid.insertBefore(div, grid.firstChild);
    }

    window.iniciarPollingAdmin({
      url: '/admin/material/lista',
      intervaloMs: 30000,
      idsConhecidos: idsConhecidos,
      inserirNovo: inserirNovoMaterial,
    });
  })();
}

// admin/eventos.html -- apagar um lote muda quem é o "ativo" (calculado no
// Jinja a partir da ordem da lista), por isso recarrega em vez de só
// tirar o card na hora.
if (paginaAdmin === 'eventos') {
  document.addEventListener('admin:sucesso', function (evt) {
    if (/\/eventos\/\d+\/apagar$/.test(evt.detail.actionUrl)) {
      location.reload();
    }
  });
}
