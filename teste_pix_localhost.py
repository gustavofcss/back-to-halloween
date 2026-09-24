"""Servidor local ISOLADO pra testar a hipotese central do MD: um Pix pago
DIRETO na chave (Copia e Cola fixo, sem passar pelo Checkout do Mercado
Pago) aparece no relatorio de liberacoes, e com que identificador?

NAO faz parte do sistema principal -- nao importa pedidos.py/admin.py, nao
mexe no banco de ingressos. So mostra o QR/copia-e-cola de teste (o mesmo
R$30 ja usado no MD), cronometra 5 minutos a partir de quando a tela
apareceu, e ao fim desse prazo puxa sozinho o relatorio do Mercado Pago
recortado EXATAMENTE nesse intervalo.

Uso:
    python teste_pix_localhost.py
    (abre em http://127.0.0.1:5091)
"""
from __future__ import annotations

import base64
import csv
import io
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

import qrcode
from flask import Flask, jsonify, render_template_string

sys.path.insert(0, str(Path(__file__).parent / "app"))
import config  # noqa: E402
from teste_mercadopago import baixar_relatorio, criar_relatorio, listar_relatorios  # noqa: E402


def _pix_copia_cola_do_md() -> str:
    # Le direto do MD em vez de copiar/colar a string na mao aqui -- um
    # payload Pix EMV so passa no CRC se vier byte a byte igual, e uma
    # transcricao manual de 174 caracteres e um jeito facil de introduzir
    # um erro invisivel (aconteceu numa tentativa anterior desta mesma
    # sessao, pegado so por checar o tamanho).
    caminho = Path(__file__).parent / "Horror_Night_Party_Pix_Mercado_Pago(1).md"
    with open(caminho, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if linha.startswith("00020126"):
                return linha
    raise RuntimeError("Pix Copia e Cola nao encontrado no MD.")


# Pix Copia e Cola de teste, R$30 -- o mesmo ja documentado em
# Horror_Night_Party_Pix_Mercado_Pago(1).md (secao 3), pago direto na
# chave, sem Checkout do Mercado Pago.
PIX_COPIA_COLA = _pix_copia_cola_do_md()

JANELA = timedelta(minutes=5)
# Folga alem da janela de pagamento antes de puxar o relatorio -- o Pix
# pode ser aprovado a poucos segundos do fim da janela, mas o Mercado Pago
# so registra o movimento no relatorio de liberacoes um tempo depois de
# aprovado (achado real do teste anterior: o pedido de R$30 do checkout
# ficou pronto no relatorio so ~1min depois de criado). Sem essa folga o
# relatorio recortaria um Pix aprovado no timer mas ainda nao processado.
FOLGA_RELATORIO = timedelta(minutes=3)

app = Flask(__name__)

_estado_lock = Lock()
_estado = {"inicio": None, "relatorio": None}


def _qr_base64(texto: str) -> str:
    img = qrcode.make(texto)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _segundos_restantes() -> int:
    decorrido = datetime.now(timezone.utc) - _estado["inicio"]
    return max(0, int((JANELA - decorrido).total_seconds()))


def _rodar_relatorio() -> dict:
    inicio: datetime = _estado["inicio"]
    fim = inicio + JANELA + FOLGA_RELATORIO
    tarefa = criar_relatorio(inicio, fim)
    task_id = tarefa.get("id")

    file_name = None
    for _ in range(18):
        time.sleep(10)
        relatorios = listar_relatorios()
        if relatorios:
            mais_recente = sorted(relatorios, key=lambda r: r.get("date_created", ""))[-1]
            file_name = mais_recente.get("file_name")
        if file_name:
            break
    else:
        return {"erro": "Relatorio nao ficou pronto a tempo (3min).", "task_id": task_id}

    csv_texto = baixar_relatorio(file_name)
    leitor = csv.DictReader(io.StringIO(csv_texto), delimiter=";")
    linhas = [linha for linha in leitor if any((v or "").strip() for v in linha.values())]
    return {
        "task_id": task_id,
        "file_name": file_name,
        "periodo": {"inicio": inicio.isoformat(), "fim": fim.isoformat()},
        "linhas": linhas,
    }


PAGINA = """
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Teste Pix direto -- Mercado Pago</title>
<style>
  body{font-family:system-ui,sans-serif;background:#0d0710;color:#eee;display:flex;
    flex-direction:column;align-items:center;padding:32px 16px;gap:18px}
  .card{background:#1a1220;border:1px solid #3a2a40;border-radius:16px;padding:24px;
    max-width:420px;width:100%;text-align:center;display:flex;flex-direction:column;gap:14px}
  img{width:220px;height:220px;align-self:center;background:#fff;padding:8px;border-radius:8px}
  textarea{width:100%;min-height:70px;background:#0d0710;color:#eee;border:1px solid #3a2a40;
    border-radius:8px;padding:8px;font-size:12px;resize:none}
  #tempo{font-size:28px;font-weight:800;color:#ff8a2b}
  #expirado{display:none;color:#ff6b6b;font-weight:700;font-size:18px}
  #relatorio{white-space:pre-wrap;text-align:left;font-size:12px;background:#0d0710;
    border:1px solid #3a2a40;border-radius:8px;padding:12px;max-height:400px;overflow:auto}
  button{background:#ff8a2b;border:none;color:#2a1503;font-weight:700;padding:10px 16px;
    border-radius:8px;cursor:pointer}
</style>
</head>
<body>
  <div class="card">
    <h2>Pix de teste -- R$ 30,00</h2>
    <img src="data:image/png;base64,{{ qr }}" alt="QR Code Pix">
    <textarea readonly onclick="this.select()">{{ copia_cola }}</textarea>
    <p>Expira em <span id="tempo">5:00</span></p>
    <p id="expirado">Pix expirado -- puxando o relatorio...</p>
  </div>
  <div class="card" id="card-relatorio" style="display:none">
    <h3>Relatorio do periodo</h3>
    <div id="relatorio">aguardando...</div>
  </div>

<script>
  var restante = {{ segundos_restantes }};
  var elTempo = document.getElementById('tempo');
  var elExpirado = document.getElementById('expirado');
  var elCardRelatorio = document.getElementById('card-relatorio');
  var elRelatorio = document.getElementById('relatorio');
  var relatorioIniciado = false;

  function mostrarTempo() {
    var m = Math.floor(restante / 60), s = restante % 60;
    elTempo.textContent = m + ':' + (s < 10 ? '0' : '') + s;
  }
  mostrarTempo();

  function buscarRelatorio() {
    if (relatorioIniciado) { return; }
    relatorioIniciado = true;
    elCardRelatorio.style.display = 'block';
    elRelatorio.textContent = 'Consultando o Mercado Pago (pode levar alguns minutos)...';
    fetch('/relatorio').then(function (r) { return r.json(); }).then(function (dados) {
      elRelatorio.textContent = JSON.stringify(dados, null, 2);
    }).catch(function (e) {
      elRelatorio.textContent = 'Erro: ' + e;
    });
  }

  var timer = setInterval(function () {
    restante = Math.max(0, restante - 1);
    mostrarTempo();
    if (restante <= 0) {
      clearInterval(timer);
      elTempo.style.display = 'none';
      elExpirado.style.display = 'block';
      buscarRelatorio();
    }
  }, 1000);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    with _estado_lock:
        if _estado["inicio"] is None:
            _estado["inicio"] = datetime.now(timezone.utc)
    return render_template_string(
        PAGINA,
        qr=_qr_base64(PIX_COPIA_COLA),
        copia_cola=PIX_COPIA_COLA,
        segundos_restantes=_segundos_restantes(),
    )


@app.route("/relatorio")
def relatorio():
    with _estado_lock:
        if _estado["inicio"] is None:
            return jsonify(erro="Ainda nao abriu a tela principal (/).")
        if _segundos_restantes() > 0:
            return jsonify(erro="Ainda dentro da janela de 5min -- espera acabar.")
        if _estado["relatorio"] is None:
            _estado["relatorio"] = _rodar_relatorio()
    return jsonify(_estado["relatorio"])


if __name__ == "__main__":
    if not config.MERCADOPAGO_ACCESS_TOKEN:
        print("MERCADOPAGO_ACCESS_TOKEN nao configurado no .env -- abortando.")
        raise SystemExit(1)
    print("Abrindo em http://127.0.0.1:5091")
    app.run(host="127.0.0.1", port=5091, debug=False)
