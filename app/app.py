"""Horror Night Party -- site de convite/venda de ingresso do usuário.
Projeto pessoal, banco Postgres próprio (Render), sem relação com nenhum
outro projeto/credencial de trabalho.
"""
from __future__ import annotations

from flask import Flask, render_template

import config

app = Flask(__name__)
app.secret_key = config.SECRET_KEY


@app.route("/")
def index():
    return render_template("convite.html")


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
