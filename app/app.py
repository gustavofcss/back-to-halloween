"""Horror Night Party -- site de convite/venda de ingresso do usuário.
Projeto pessoal, banco Postgres próprio (Render), sem relação com nenhum
outro projeto/credencial de trabalho.
"""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlsplit

from flask import Flask, abort, render_template, request

import config
import db
from formatacao import data_hora_br
from admin import admin_bp
from auth import auth_bp, current_user, oauth
from pedidos import pedidos_bp

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config.update(
    # Lax: o navegador não manda o cookie de sessão em POST vindo de outro site.
    SESSION_COOKIE_SAMESITE="Lax",
    # Login vale por 14 dias (session.permanent em auth.py); sem isso o cookie
    # morria ao fechar o navegador e o admin no celular logava toda hora.
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    # Secure só quando o site é https de verdade (em dev local é http).
    SESSION_COOKIE_SECURE=config.BASE_URL.startswith("https://"),
)

oauth.init_app(app)
app.register_blueprint(auth_bp)
app.register_blueprint(pedidos_bp)
app.register_blueprint(admin_bp)


@app.before_request
def _bloquear_post_de_outro_site():
    """Defesa contra CSRF: um site de fora não consegue fazer o navegador do
    admin/cliente mandar um formulário pra cá. Navegador sempre manda `Origin`
    em POST/PUT/PATCH/DELETE; se ele aponta pra outro endereço (ou é "null"), a
    requisição é recusada. Sem `Origin` (curl, webhook do Mercado Pago, que é
    servidor-pra-servidor) passa -- o webhook nunca confia no corpo mesmo."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origem = request.headers.get("Origin")
        if origem is not None and urlsplit(origem).netloc != request.host:
            abort(403)


@app.context_processor
def inject_usuario():
    # Sem isso, cada rota que renderiza uma página precisaria lembrar de
    # passar usuario=current_user() na mão -- a rota "/" esqueceu isso (achado
    # real: login funcionava de verdade, sessão era criada certinho, mas a
    # página inicial nunca sabia disso, então o botão "Comprar ingresso"
    # continuava mudo e o "Sair" nunca aparecia -- parecia que o login não
    # tinha feito nada). Injetar aqui, uma vez, cobre TODAS as rotas/telas.
    usuario = current_user()
    tem_pedido = False
    if usuario:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pedido WHERE id_usuario = %s AND status_pedido != 'cancelado' LIMIT 1",
                    (usuario["id_usuario"],),
                )
                tem_pedido = cur.fetchone() is not None
    return {"usuario": usuario, "tem_pedido": tem_pedido}


_DIAS_SEMANA = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
_MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


app.add_template_filter(data_hora_br, "data_hora_br")


@app.route("/")
def index():
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM evento ORDER BY data_cadastro DESC LIMIT 1")
            evento = cur.fetchone()

    data_formatada = None
    if evento and evento.get("data_evento"):
        d = evento["data_evento"]
        data_formatada = f"{d.day} {_MESES[d.month - 1]} | {_DIAS_SEMANA[d.weekday()]}"
        if evento.get("hora_evento"):
            data_formatada += f" | {evento['hora_evento'].strftime('%H:%M')}"

    return render_template(
        "convite.html", evento=evento, data_formatada=data_formatada,
        promo_casadinha_valor=int(config.PROMO_CASADINHA_VALOR),
    )


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
