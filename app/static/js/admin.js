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
        cb.dispatchEvent(new Event('change'));
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
    if (editando || modalAberto) { return; }

    fetch(opts.url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (resp) { return resp.ok ? resp.json() : null; })
      .then(function (dados) {
        if (!dados || !dados.itens) { return; }
        var novos = 0;
        dados.itens.forEach(function (item) {
          if (opts.idsConhecidos.has(item.id)) { return; }
          opts.idsConhecidos.add(item.id);
          opts.inserirNovo(item);
          novos += 1;
        });
        if (novos > 0) { window.atualizarTotaisAdmin(dados.totais); }
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

// admin/pedidos.html -- busca, seleção em lote, reação a admin:sucesso e
// polling de pedidos novos.
if (paginaAdmin === 'pedidos') {
  (function () {
    var input = document.getElementById('admin-pedidos-search');
    var table = document.getElementById('admin-pedidos-table');
    if (!input || !table) { return; }
    var rows = table.querySelectorAll('tbody tr');
    input.addEventListener('input', function () {
      var q = input.value.trim().toLowerCase();
      rows.forEach(function (tr) {
        tr.style.display = (!q || tr.textContent.toLowerCase().includes(q)) ? '' : 'none';
      });
    });
  })();

  (function () {
    var checkAll = document.getElementById('pedidos-check-all');
    var itens = document.querySelectorAll('.pedidos-check-item');
    var bar = document.getElementById('pedidos-bulk-bar');
    var contagem = document.getElementById('pedidos-bulk-count');
    if (!checkAll || !itens.length) { return; }

    function atualizar() {
      // Recalcula o total AO VIVO (não usa itens.length, capturado uma vez
      // no load) -- depois que "Apagar" remove uma linha do DOM (via
      // admin:sucesso), o total de caixinhas realmente presentes cai, mas
      // itens.length continua contando a linha já removida. Sem isso,
      // "Selecionar todos" nunca mais fica marcado depois do primeiro apagar.
      var total = document.querySelectorAll('.pedidos-check-item').length;
      var marcados = document.querySelectorAll('.pedidos-check-item:checked').length;
      bar.classList.toggle('admin-bulk-bar--visivel', marcados > 0);
      contagem.textContent = marcados + ' selecionado(s)';
      checkAll.checked = total > 0 && marcados === total;
    }
    checkAll.addEventListener('change', function () {
      itens.forEach(function (item) { item.checked = checkAll.checked; });
      atualizar();
    });
    itens.forEach(function (item) { item.addEventListener('change', atualizar); });
    // Exposta pro polling (abaixo) conseguir religar uma linha nova no
    // "selecionar todos" e recalcular o total sem duplicar essa lógica.
    window.religarSelecaoPedido = function (checkbox) {
      checkbox.addEventListener('change', atualizar);
      atualizar();
    };
  })();

  // Confirmar/cancelar trocam o selo de status e os botões da linha;
  // apagar tira a linha da tabela -- sem isso a tela só refletia a mutação
  // depois de um reload inteiro da página.
  document.addEventListener('admin:sucesso', function (evt) {
    var pillClasses = { confirmado: 'admin-pill--ok', pendente: 'admin-pill--warn', cancelado: 'admin-pill--bad' };
    evt.detail.dados.aplicados.forEach(function (item) {
      var checkbox = document.querySelector('.pedidos-check-item[value="' + item.id + '"]');
      if (!checkbox) { return; }
      var linha = checkbox.closest('tr');
      if (!item.status) { linha.remove(); return; }

      var pill = linha.querySelector('.admin-pill');
      pill.className = 'admin-pill ' + (pillClasses[item.status] || 'admin-pill--neutral');
      pill.textContent = item.status;

      var celulaAcoes = linha.querySelector('td:last-child');
      if (item.status === 'confirmado') {
        celulaAcoes.innerHTML =
          '<button type="submit" formaction="/admin/pedidos/' + item.id + '/cancelar" class="admin-btn-outline">Cancelar</button>';
      } else if (item.status === 'cancelado') {
        celulaAcoes.innerHTML =
          '<button type="button" class="admin-btn-outline is-danger" onclick="return confirmarAcaoLote(document.getElementById(\'pedidos-lote-form\'), \'/admin/pedidos/' + item.id + '/apagar\', \'Apagar esse pedido cancelado? Não tem como desfazer.\', true)">Apagar</button>';
      }
    });
  });

  // Polling suave (10s) -- pedido novo de outro cliente aparece sozinho no
  // topo da tabela, com uma animação, sem recarregar a página inteira nem
  // mexer no que já estava na tela (achado real, pedido do usuário: não
  // queria o reload cheio de antes, só que o item novo "surgisse" com uma
  // transição em vez de aparecer do nada).
  (function () {
    var tbody = document.querySelector('#admin-pedidos-table tbody');
    var idsConhecidos = new Set(
      Array.prototype.map.call(document.querySelectorAll('.pedidos-check-item'), function (cb) {
        return parseInt(cb.value, 10);
      })
    );
    var pillClasses = { confirmado: 'admin-pill--ok', pendente: 'admin-pill--warn', cancelado: 'admin-pill--bad' };

    function inserirNovoPedido(item) {
      if (!tbody) { location.reload(); return; } // tabela vazia antes (sem nenhum pedido) -- não dá pra inserir linha sem recarregar essa vez
      var esc = window.escaparHtmlAdmin;
      var extra = item.acompanhante_nome ? '<span class="a-order__extra">+ ' + esc(item.acompanhante_nome) + '</span>' : '';
      var compradoPor = item.slot === 2 ? '<span class="a-order__extra">Comprado por ' + esc(item.comprador_nome) + '</span>' : '';
      var cortesiaTag = item.cortesia ? '<span class="admin-pill admin-pill--accent">Cortesia</span>' : '';
      var acoes = '';
      if (item.status === 'pendente') {
        acoes =
          '<button type="submit" formaction="/admin/pedidos/' + item.id + '/confirmar" class="admin-btn-primary">Confirmar Pix</button> ' +
          '<button type="submit" formaction="/admin/pedidos/' + item.id + '/cancelar" class="admin-btn-outline">Cancelar</button>';
      } else if (item.status === 'confirmado') {
        acoes = '<button type="submit" formaction="/admin/pedidos/' + item.id + '/cancelar" class="admin-btn-outline">Cancelar</button>';
      } else if (item.status === 'cancelado') {
        acoes =
          '<button type="button" class="admin-btn-outline is-danger" onclick="return confirmarAcaoLote(document.getElementById(\'pedidos-lote-form\'), \'/admin/pedidos/' + item.id + '/apagar\', \'Apagar esse pedido cancelado? Não tem como desfazer.\', true)">Apagar</button>';
      }
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
        '<td data-label="Ações">' + acoes + '</td>';
      tbody.insertBefore(tr, tbody.firstChild);
      if (window.religarSelecaoPedido) { window.religarSelecaoPedido(tr.querySelector('.pedidos-check-item')); }
    }

    window.iniciarPollingAdmin({
      url: '/admin/pedidos/lista',
      intervaloMs: 10000,
      idsConhecidos: idsConhecidos,
      inserirNovo: inserirNovoPedido,
    });
  })();
}

// admin/cadastros.html -- seleção em lote, reação a admin:sucesso e
// polling de cadastros pendentes novos.
if (paginaAdmin === 'cadastros') {
  (function () {
    var checkAll = document.getElementById('cadastros-check-all');
    var itens = document.querySelectorAll('.cadastros-check-item');
    var bar = document.getElementById('cadastros-bulk-bar');
    var contagem = document.getElementById('cadastros-bulk-count');
    if (!checkAll || !itens.length) { return; }

    function atualizar() {
      // Total ao vivo, não itens.length (capturado uma vez no load) -- depois
      // que confirmar/recusar remove uma linha via admin:sucesso, o total
      // real de caixinhas cai, mas itens.length continua com a antiga.
      var total = document.querySelectorAll('.cadastros-check-item').length;
      var marcados = document.querySelectorAll('.cadastros-check-item:checked').length;
      bar.classList.toggle('admin-bulk-bar--visivel', marcados > 0);
      contagem.textContent = marcados + ' selecionado(s)';
      checkAll.checked = total > 0 && marcados === total;
    }
    checkAll.addEventListener('change', function () {
      itens.forEach(function (item) { item.checked = checkAll.checked; });
      atualizar();
    });
    itens.forEach(function (item) { item.addEventListener('change', atualizar); });

    window.religarSelecaoCadastro = function (checkbox) {
      checkbox.addEventListener('change', atualizar);
      atualizar();
    };
  })();

  // Confirmar e recusar sempre tiram o cadastro da lista de pendentes.
  document.addEventListener('admin:sucesso', function (evt) {
    evt.detail.dados.aplicados.forEach(function (item) {
      var checkbox = document.querySelector('.cadastros-check-item[value="' + item.id + '"]');
      if (checkbox) { checkbox.closest('tr').remove(); }
    });
  });

  (function () {
    var tbody = document.querySelector('#admin-cadastros-table tbody');
    var idsConhecidos = new Set(
      Array.prototype.map.call(document.querySelectorAll('.cadastros-check-item'), function (cb) {
        return parseInt(cb.value, 10);
      })
    );

    function iniciais(nome) {
      var partes = String(nome || '').split(' ');
      var s = (partes[0] ? partes[0][0] : '') + (partes.length > 1 && partes[1] ? partes[1][0] : '');
      return s.toUpperCase();
    }

    function inserirNovoCadastro(item) {
      if (!tbody) { location.reload(); return; }
      var esc = window.escaparHtmlAdmin;
      var vazio = document.querySelector('.a-empty');
      if (vazio) { location.reload(); return; }
      var tr = document.createElement('tr');
      tr.className = 'a-tr a-tr--cadastros a-item-novo';
      tr.innerHTML =
        '<td class="admin-table__check-col"><input type="checkbox" name="ids" value="' + item.id + '" class="cadastros-check-item" aria-label="Selecionar ' + esc(item.nome) + '"></td>' +
        '<td class="a-order__namecell" data-label="Nome"><span class="avatar">' + esc(iniciais(item.nome)) + '</span><span class="a-order__name">' + esc(item.nome) + '</span></td>' +
        '<td class="a-order__email" data-label="E-mail">' + esc(item.email) + '</td>' +
        '<td class="a-order__extra" data-label="Cadastrado em">' + esc(item.data) + '</td>' +
        '<td data-label="Ações">' +
        '<button type="submit" formaction="/admin/cadastros/' + item.id + '/confirmar" class="admin-btn-primary">Confirmar cadastro</button> ' +
        '<button type="button" class="admin-btn-outline is-danger" onclick="return confirmarAcaoLote(document.getElementById(\'cadastros-lote-form\'), \'/admin/cadastros/' + item.id + '/recusar\', \'Recusar e apagar esse cadastro?\', true)">Recusar</button>' +
        '</td>';
      tbody.insertBefore(tr, tbody.firstChild);
      if (window.religarSelecaoCadastro) { window.religarSelecaoCadastro(tr.querySelector('.cadastros-check-item')); }
    }

    window.iniciarPollingAdmin({
      url: '/admin/cadastros/lista',
      intervaloMs: 10000,
      idsConhecidos: idsConhecidos,
      inserirNovo: inserirNovoCadastro,
    });
  })();
}

// admin/hierarquia.html -- busca, seleção em lote de clientes, reação a
// admin:sucesso (promover/rebaixar/remover) e polling de clientes novos.
if (paginaAdmin === 'hierarquia') {
  (function () {
    var input = document.getElementById('admin-hierarquia-search');
    var rows = document.querySelectorAll('.admin-group__row');
    if (!input || !rows.length) { return; }
    input.addEventListener('input', function () {
      var q = input.value.trim().toLowerCase();
      rows.forEach(function (row) {
        row.style.display = (!q || row.textContent.toLowerCase().includes(q)) ? '' : 'none';
      });
    });
  })();

  (function () {
    var checkAll = document.getElementById('clientes-check-all');
    var itens = document.querySelectorAll('.clientes-check-item');
    var bar = document.getElementById('clientes-bulk-bar');
    var contagem = document.getElementById('clientes-bulk-count');
    if (!checkAll || !itens.length) { return; }

    function atualizar() {
      // Total ao vivo, não itens.length (capturado uma vez no load) -- depois
      // que promover/remover tira uma linha via admin:sucesso, o total real
      // de caixinhas cai, mas itens.length continua com a antiga.
      var total = document.querySelectorAll('.clientes-check-item').length;
      var marcados = document.querySelectorAll('.clientes-check-item:checked').length;
      bar.classList.toggle('admin-bulk-bar--visivel', marcados > 0);
      contagem.textContent = marcados + ' selecionado(s)';
      checkAll.checked = total > 0 && marcados === total;
    }
    checkAll.addEventListener('change', function () {
      itens.forEach(function (item) { item.checked = checkAll.checked; });
      atualizar();
    });
    itens.forEach(function (item) { item.addEventListener('change', atualizar); });

    window.religarSelecaoCliente = function (checkbox) {
      checkbox.addEventListener('change', atualizar);
      atualizar();
    };
  })();

  // Promover/rebaixar/remover sempre tiram a pessoa do grupo em que ela
  // estava (cliente promovido some da lista de Clientes, admin rebaixado
  // some da lista de Administradores) -- por isso um único handler genérico
  // (tirar a linha da tela) cobre as 5 rotas de mutação da Hierarquia, single
  // e em lote. A pessoa aparece no grupo novo no próximo refresh da página.
  document.addEventListener('admin:sucesso', function (evt) {
    evt.detail.dados.aplicados.forEach(function (item) {
      var linha = document.querySelector('.admin-group__row[data-row-id="' + item.id + '"]');
      if (linha) { linha.remove(); }
    });
  });

  (function () {
    var lista = document.querySelector('#clientes-lote-form .admin-group__list');
    var idsConhecidos = new Set(
      Array.prototype.map.call(document.querySelectorAll('.clientes-check-item'), function (cb) {
        return parseInt(cb.value, 10);
      })
    );

    function iniciais(nome) {
      var partes = String(nome || '').split(' ');
      var s = (partes[0] ? partes[0][0] : '') + (partes.length > 1 && partes[1] ? partes[1][0] : '');
      return s.toUpperCase();
    }

    function inserirNovoCliente(item) {
      if (!lista) { location.reload(); return; }
      var esc = window.escaparHtmlAdmin;
      var div = document.createElement('div');
      div.className = 'admin-group__row a-item-novo';
      div.setAttribute('data-row-id', item.id);
      div.innerHTML =
        '<div class="admin-group__row-inner">' +
        '<input type="checkbox" name="ids" value="' + item.id + '" class="clientes-check-item" aria-label="Selecionar ' + esc(item.nome) + '">' +
        '<span class="avatar">' + esc(iniciais(item.nome)) + '</span>' +
        '<span class="admin-group__info"><span class="admin-group__name">' + esc(item.nome) + '</span><span class="admin-group__email">' + esc(item.email) + '</span></span>' +
        '<span class="admin-group__action admin-group__action--duplo">' +
        '<button type="submit" formaction="/admin/hierarquia/' + item.id + '/promover" class="admin-btn-primary">Promover a admin</button>' +
        '<button type="button" class="admin-btn-outline is-danger" onclick="return confirmarAcaoLote(document.getElementById(\'clientes-lote-form\'), \'/admin/hierarquia/' + item.id + '/remover\', \'Apagar esse cliente? Pedidos pendentes ou cancelados dele também são apagados; quem tem pedido confirmado não pode ser apagado. Não tem como desfazer.\', true)">Apagar</button>' +
        '</span>' +
        '</div>';
      lista.insertBefore(div, lista.firstChild);
      if (window.religarSelecaoCliente) { window.religarSelecaoCliente(div.querySelector('.clientes-check-item')); }
    }

    window.iniciarPollingAdmin({
      url: '/admin/hierarquia/lista',
      intervaloMs: 10000,
      idsConhecidos: idsConhecidos,
      inserirNovo: inserirNovoCliente,
    });
  })();
}

// admin/material.html -- reação a admin:sucesso (apagar tira o card) e
// polling de material novo.
if (paginaAdmin === 'material') {
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
      div.innerHTML =
        '<span class="a-material__name">' + esc(item.nome) + '</span>' +
        '<span class="a-material__desc">' + esc(item.descricao) + '</span>' +
        '<span class="a-material__value">R$ ' + esc(item.valor) + '</span>' +
        '<span class="a-material__meta">' + esc(item.data) + ' &middot; ' + esc(item.cadastrado_por) + '</span>' +
        '<form method="post" action="/admin/material/' + item.id + '/apagar" onsubmit="return confirmarAcao(this, \'Apagar esse material?\', true)">' +
        '<button type="submit" class="admin-btn-outline is-danger">Apagar</button>' +
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
