(function () {
  var form = document.getElementById('comprar-form');
  if (!form) { return; }
  var max = parseInt(form.dataset.max, 10);
  var price = parseFloat(form.dataset.preco);
  var promo2 = form.dataset.promo2 ? parseFloat(form.dataset.promo2) : 0;
  var input = document.getElementById('quantidade');
  var display = document.getElementById('qty-display');
  var totalDisplay = document.getElementById('total-display');
  var tagSide = document.getElementById('tag-side');
  var tagValue = document.getElementById('tag-value');
  var dec = document.getElementById('qty-dec');
  var inc = document.getElementById('qty-inc');
  var acompanhanteBox = document.getElementById('acompanhante-fields');
  var acompanhanteInputs = acompanhanteBox ? acompanhanteBox.querySelectorAll('input') : [];

  function render() {
    display.textContent = input.value;
    var qtd = parseInt(input.value, 10);
    var total = (promo2 && qtd === 2) ? promo2 : price * qtd;
    totalDisplay.textContent = total.toFixed(2);
    // Etiqueta de cima acompanha a quantidade: com 2 ingressos mostra os dois
    // lados (A + B) e o total (casadinha ou 2 × preço), não só o preço unitário.
    if (tagSide) { tagSide.textContent = qtd === 2 ? 'LADO A + B · 2 INGRESSOS' : 'LADO A · INGRESSO'; }
    if (tagValue) { tagValue.textContent = 'R$ ' + total.toFixed(2); }
    if (acompanhanteBox) {
      var precisa = parseInt(input.value, 10) === 2;
      // 'flex', não 'block' -- .dashed-top é display:flex;gap:12px
      // no CSS, mas style inline sempre vence a classe. Com
      // 'block' o gap não tinha efeito nenhum (só funciona em
      // flex/grid) e os campos do acompanhante ficavam colados,
      // sem espaço nenhum entre um e outro (achado real, usuário
      // reportou em print comparando com os campos do titular,
      // que são filhos diretos do form -- esses sim recebiam o
      // gap:14px do form, por isso só o acompanhante parecia
      // "apertado").
      acompanhanteBox.style.display = precisa ? 'flex' : 'none';
      acompanhanteInputs.forEach(function (campo) {
        if (precisa) { campo.setAttribute('required', 'required'); }
        else { campo.removeAttribute('required'); }
      });
    }
  }
  dec.addEventListener('click', function () {
    input.value = Math.max(1, parseInt(input.value, 10) - 1);
    render();
  });
  inc.addEventListener('click', function () {
    input.value = Math.min(max, parseInt(input.value, 10) + 1);
    render();
  });
})();
