"""Horror Night Party -- site de convite/venda de ingresso do usuário.
Projeto pessoal, banco Postgres próprio (Render), sem relação com nenhum
outro projeto/credencial de trabalho.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

from flask import Flask, render_template

import config
import db
from admin import admin_bp
from auth import auth_bp, current_user, oauth
from pedidos import pedidos_bp

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

oauth.init_app(app)
app.register_blueprint(auth_bp)
app.register_blueprint(pedidos_bp)
app.register_blueprint(admin_bp)


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
_TZ_BRASIL = ZoneInfo("America/Sao_Paulo")


@app.template_filter("data_hora_br")
def data_hora_br(valor):
    # Sem isso, `{{ p.data_pedido }}` no template imprime o repr cru do
    # datetime do Postgres (com microssegundos e timezone tipo
    # "2026-09-21 23:26:32.186000+00:00") -- formato estranho pro usuário
    # (achado real, na aba de pedidos). Formata como dd/mm/aaaa hh:mm.
    # O valor vem em UTC (TIMESTAMPTZ) -- sem converter, mostra 3h a mais do
    # que o horário real de Brasília (achado real, na tela de cadastros).
    if not valor:
        return "--"
    return valor.astimezone(_TZ_BRASIL).strftime("%d/%m/%Y %H:%M")


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
