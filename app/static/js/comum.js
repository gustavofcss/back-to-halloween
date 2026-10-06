// Trava o botão de submit assim que o form é enviado, pra clique duplo
// (ou duplo toque no celular) não mandar a mesma requisição 2x -- achado
// real: cadastrar material 2x seguidas criou 2 linhas iguais.
document.addEventListener('submit', function (evt) {
  var btn = evt.target.querySelector('button[type="submit"]');
  if (btn && !btn.disabled) {
    setTimeout(function () {
      btn.disabled = true;
      btn.style.opacity = '.6';
      btn.style.cursor = 'not-allowed';
    }, 0);
  }
}, true);

// Formata WhatsApp ao vivo -- (DD) DDDDD-DDDD (celular, 9 dígitos) ou
// (DD) DDDD-DDDD (fixo, 8 dígitos). Uma função só (antes havia cópias em
// comprar.html e admin/pedidos.html) -- o backend já tolera essa
// formatação (tira tudo que não é dígito antes de validar, ver
// _validar_whatsapp em pedidos.py), só isso aqui muda.
function formatarWhatsapp(digitos) {
  if (digitos.length <= 2) { return digitos.replace(/^(\d*)/, '($1'); }
  if (digitos.length <= 6) { return digitos.replace(/^(\d{2})(\d*)/, '($1) $2'); }
  if (digitos.length <= 10) { return digitos.replace(/^(\d{2})(\d{4})(\d*)/, '($1) $2-$3'); }
  return digitos.replace(/^(\d{2})(\d{5})(\d*)/, '($1) $2-$3');
}
document.querySelectorAll('[data-mask="whatsapp"]').forEach(function (campo) {
  campo.addEventListener('input', function () {
    var pos = campo.selectionStart, antes = campo.value.length;
    campo.value = formatarWhatsapp(campo.value.replace(/\D/g, '').slice(0, 11));
    var diff = campo.value.length - antes;
    campo.setSelectionRange(pos + diff, pos + diff);
  });
});

// Toggle de formulário -- substitui 3 cópias (cortesia, material, lote),
// cada uma com seu próprio botão "+ Novo..." que revelava a seção e
// rolava até ela.
document.querySelectorAll('[data-toggle-form]').forEach(function (btn) {
  var alvo = document.getElementById(btn.getAttribute('data-toggle-form'));
  if (!alvo) { return; }
  btn.addEventListener('click', function () {
    alvo.classList.remove('is-hidden');
    var y = alvo.getBoundingClientRect().top + window.pageYOffset - 16;
    window.scrollTo({ top: y, behavior: 'smooth' });
    var primeiro = alvo.querySelector('input:not([type=hidden]):not([disabled]), select, textarea');
    if (primeiro) { primeiro.focus({ preventScroll: true }); }
  });
});
document.querySelectorAll('[data-fechar-form]').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var alvo = document.getElementById(btn.getAttribute('data-fechar-form'));
    if (alvo) { alvo.classList.add('is-hidden'); }
  });
});

// Botão "Atualizar" das páginas (admin e Meus pedidos): recarrega a página no
// lugar do F5. Trava o botão e gira o ícone pra mostrar que está trabalhando.
document.querySelectorAll('[data-atualizar-pagina]').forEach(function (btn) {
  btn.addEventListener('click', function () {
    btn.disabled = true;
    btn.classList.add('is-atualizando');
    window.location.reload();
  });
});
