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
