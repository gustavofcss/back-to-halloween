// Caixinhas do código de 6 dígitos (verifique_email.html e redefinir_senha.html).
// Uma caixa por dígito; o <input type="hidden" id="codigo"> é o que o form manda.
function ligarCaixasOtp(boxes, hidden) {
  function sincronizar() {
    hidden.value = boxes.map(function (b) { return b.value; }).join('');
  }

  // Espalha uma sequência de dígitos a partir da caixa i: colar, ou o
  // autopreenchimento "one-time-code" do iOS/Android, que entrega o código
  // inteiro de uma vez só.
  function espalhar(i, digitos) {
    digitos.split('').slice(0, boxes.length - i).forEach(function (d, j) { boxes[i + j].value = d; });
    sincronizar();
    boxes[Math.min(i + digitos.length, boxes.length - 1)].focus();
  }

  boxes.forEach(function (box, i) {
    box.addEventListener('input', function (evt) {
      var digitos = box.value.replace(/\D/g, '');
      // Mais de um dígito de uma vez (e não uma tecla só) = autopreenchimento.
      if (digitos.length > 1 && !(evt.data && evt.data.length === 1)) {
        espalhar(i, digitos);
        return;
      }
      box.value = digitos.slice(-1);  // digitar numa caixa cheia troca o dígito
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
      espalhar(i, texto);
    });
  });
  return sincronizar;
}

// verifique_email.html -- caixinhas do código + "errei o e-mail". Guarda
// em #form-mudar-email (só existe nessa página) pra não colidir com o
// redefinir_senha.html, que também tem .otp-box/#codigo.
(function () {
  var formMudarEmail = document.getElementById('form-mudar-email');
  if (!formMudarEmail) { return; }

  var boxes = Array.prototype.slice.call(document.querySelectorAll('.otp-box'));
  var hidden = document.getElementById('codigo');
  if (boxes.length && hidden) {
    ligarCaixasOtp(boxes, hidden);
    // <input type="hidden" required> não é validado pelo navegador: sem isso
    // dava pra enviar o código vazio ou pela metade, e o servidor contava
    // como tentativa errada (são só 5 antes de invalidar o código).
    if (hidden.form) {
      hidden.form.addEventListener('submit', function (evt) {
        if (hidden.value.length === boxes.length) { return; }
        evt.preventDefault();
        var vazia = boxes.filter(function (b) { return !b.value; })[0] || boxes[0];
        vazia.focus();
      });
    }
    boxes[0].focus();
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

  var otpBoxes = Array.prototype.slice.call(document.querySelectorAll('.otp-box'));
  ligarCaixasOtp(otpBoxes, codigo);

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
