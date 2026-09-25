// verifique_email.html -- caixinhas do código + "errei o e-mail". Guarda
// em #form-mudar-email (só existe nessa página) pra não colidir com o
// redefinir_senha.html, que também tem .otp-box/#codigo.
(function () {
  var formMudarEmail = document.getElementById('form-mudar-email');
  if (!formMudarEmail) { return; }

  var boxes = Array.prototype.slice.call(document.querySelectorAll('.otp-box'));
  var hidden = document.getElementById('codigo');
  if (boxes.length && hidden) {
    function sincronizar() {
      hidden.value = boxes.map(function (b) { return b.value; }).join('');
    }

    boxes.forEach(function (box, i) {
      box.addEventListener('input', function () {
        box.value = box.value.replace(/\D/g, '').slice(-1);
        sincronizar();
        if (box.value && boxes[i + 1]) { boxes[i + 1].focus(); }
      });
      box.addEventListener('keydown', function (evt) {
        if (evt.key === 'Backspace' && !box.value && boxes[i - 1]) { boxes[i - 1].focus(); }
      });
      box.addEventListener('paste', function (evt) {
        var texto = (evt.clipboardData || window.clipboardData).getData('text').replace(/\D/g, '');
        if (!texto) { return; }
        evt.preventDefault();
        texto.split('').slice(0, boxes.length - i).forEach(function (d, j) { boxes[i + j].value = d; });
        sincronizar();
        boxes[Math.min(i + texto.length, boxes.length - 1)].focus();
      });
    });
    if (boxes[0]) { boxes[0].focus(); }
  }

  document.getElementById('btn-mudar-email').addEventListener('click', function () {
    formMudarEmail.style.display = 'flex';
    this.style.display = 'none';
    document.getElementById('novo_email').focus();
  });
})();

// redefinir_senha.html -- etapa código + etapa senha nova, num form só.
(function () {
  var form = document.getElementById('redefinir-form');
  if (!form) { return; }

  var etapaCodigo = document.getElementById('etapa-codigo');
  var etapaSenha = document.getElementById('etapa-senha');
  var email = document.getElementById('email');
  var codigo = document.getElementById('codigo');
  var senha = document.getElementById('senha');
  var confirmar = document.getElementById('confirmar_senha');
  var aviso = document.getElementById('senha-erro');

  // Caixinhas do código (uma por dígito) -- #codigo (hidden) é quem o
  // form realmente manda, sincronizado a cada digitação.
  var otpBoxes = Array.prototype.slice.call(document.querySelectorAll('.otp-box'));
  function sincronizarOtp() {
    codigo.value = otpBoxes.map(function (b) { return b.value; }).join('');
  }
  otpBoxes.forEach(function (box, i) {
    box.addEventListener('input', function () {
      box.value = box.value.replace(/\D/g, '').slice(-1);
      sincronizarOtp();
      if (box.value && otpBoxes[i + 1]) { otpBoxes[i + 1].focus(); }
    });
    box.addEventListener('keydown', function (evt) {
      if (evt.key === 'Backspace' && !box.value && otpBoxes[i - 1]) { otpBoxes[i - 1].focus(); }
    });
    box.addEventListener('paste', function (evt) {
      var texto = (evt.clipboardData || window.clipboardData).getData('text').replace(/\D/g, '');
      if (!texto) { return; }
      evt.preventDefault();
      texto.split('').slice(0, otpBoxes.length - i).forEach(function (d, j) { otpBoxes[i + j].value = d; });
      sincronizarOtp();
      otpBoxes[Math.min(i + texto.length, otpBoxes.length - 1)].focus();
    });
  });

  document.getElementById('btn-continuar').addEventListener('click', function () {
    // required/pattern do e-mail ainda vale (reportValidity mostra o
    // balão nativo se algo estiver errado); #codigo é hidden agora
    // (as caixinhas visíveis são só pra digitação), então checa o
    // tamanho na mão em vez de reportValidity (navegador não valida
    // campo escondido direito).
    if (!email.reportValidity()) { return; }
    if (codigo.value.length !== 6) {
      otpBoxes[0].focus();
      return;
    }
    etapaCodigo.style.display = 'none';
    etapaSenha.style.display = 'flex';
    senha.setAttribute('required', 'required');
    confirmar.setAttribute('required', 'required');
    senha.focus();
  });

  document.getElementById('btn-voltar').addEventListener('click', function () {
    etapaSenha.style.display = 'none';
    etapaCodigo.style.display = 'block';
    senha.removeAttribute('required');
    confirmar.removeAttribute('required');
  });

  // Reavalia a cada letra digitada, não só no submit -- sem isso, quem
  // via o aviso aparecer (senhas diferentes), corrigia o campo e não
  // clicava em "Redefinir senha" de novo antes de olhar a tela, continuava
  // vendo "não coincidem" preso na tela mesmo já estando certo (achado
  // real, usuário reportou em print -- mesmo bug já corrigido em
  // signup.html).
  function revalidar() {
    aviso.style.display = (senha.value && confirmar.value && senha.value !== confirmar.value)
      ? 'block' : 'none';
  }
  senha.addEventListener('input', revalidar);
  confirmar.addEventListener('input', revalidar);
  form.addEventListener('submit', function (evt) {
    if (senha.value !== confirmar.value) {
      evt.preventDefault();
      aviso.style.display = 'block';
    }
  });
})();
