"""Horror Night Party -- site de convite/venda de ingresso do usuário.
Projeto pessoal, banco Postgres próprio (Render), sem relação com nenhum
outro projeto/credencial de trabalho.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
from datetime import timedelta
from urllib.parse import urlsplit

from flask import Flask, abort, render_template, request

import config
import db
from formatacao import data_hora_br
from admin import admin_bp
from auth import auth_bp, current_user, oauth
from pedidos import pedidos_bp

# Nem todo sistema conhece .woff2 (no Windows sai application/octet-stream).
mimetypes.add_type("font/woff2", ".woff2")

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config.update(
    # Lax: o navegador não manda o cookie de sessão em POST vindo de outro site.
    SESSION_COOKIE_SAMESITE="Lax",
    # Login vale por 14 dias (session.permanent em auth.py); sem isso o cookie
    # morria ao fechar o navegador e o admin no celular logava toda hora.
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    # Todo formulário/JSON do site é pequeno: sem teto, um POST anônimo de centenas de
    # MB (o webhook do Mercado Pago não exige login) era lido inteiro na memória
    # do worker (512 MB no plano gratuito).
    MAX_CONTENT_LENGTH=1_000_000,
    # Secure só quando o site é https de verdade (em dev local é http).
    SESSION_COOKIE_SECURE=config.BASE_URL.startswith("https://"),
)

oauth.init_app(app)
app.register_blueprint(auth_bp)
app.register_blueprint(pedidos_bp)
app.register_blueprint(admin_bp)


class _TemPedido:
    """Vale `True` se a conta tem algum pedido não cancelado. A consulta só roda
    quando um template de fato usa o valor (convite e compra): as páginas do
    admin, o login e o resto não pagam essa ida ao banco."""

    def __init__(self, id_usuario: int):
        self._id_usuario = id_usuario
        self._valor: bool | None = None

    def __bool__(self) -> bool:
        if self._valor is None:
            with db.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM pedido WHERE id_usuario = %s AND status_pedido != 'cancelado' LIMIT 1",
                        (self._id_usuario,),
                    )
                    self._valor = cur.fetchone() is not None
        return self._valor


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


_versoes_estaticos: dict[str, str] = {}


@app.url_defaults
def _versao_do_estatico(endpoint, values):
    """url_for('static', ...) ganha ?v=<hash do conteúdo>. Assim o navegador pode
    guardar CSS/JS/imagens por 1 ano (ver _cache_longo_dos_estaticos) e, quando o
    arquivo muda, a URL muda junto. Antes eles vinham com `no-cache`: toda troca
    de página revalidava o CSS (que trava a renderização) e o JS, e cada
    revalidação custava de 0,5 a 1 s de ida e volta até o servidor."""
    if endpoint != "static" or "v" in values:
        return
    nome = values.get("filename")
    if not nome:
        return
    versao = _versoes_estaticos.get(nome)
    if versao is None:
        try:
            with open(os.path.join(app.static_folder, nome), "rb") as f:
                versao = hashlib.md5(f.read()).hexdigest()[:10]
        except OSError:
            return
        _versoes_estaticos[nome] = versao
    values["v"] = versao


@app.after_request
def _cabecalhos_de_seguranca(resposta):
    """Endurecimento: impede o site de ser colocado num <iframe> de outro endereço
    (clickjacking do admin), de ter o tipo de arquivo "adivinhado" e de vazar o
    caminho completo no Referer. CSP completa exigiria tirar os <script>/onclick
    inline, então só `frame-ancestors`."""
    resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
    resposta.headers.setdefault("X-Frame-Options", "DENY")
    resposta.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    resposta.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if config.BASE_URL.startswith("https://"):
        resposta.headers.setdefault("Strict-Transport-Security", "max-age=15552000")
    return resposta


@app.after_request
def _cache_longo_dos_estaticos(resposta):
    """Estático com ?v= (a URL muda quando o conteúdo muda) e as fontes (nome fixo,
    conteúdo que nunca muda) ficam 1 ano no cache do navegador. Se um dia trocar
    o ARQUIVO de uma fonte, troque também o NOME dele."""
    if request.path.startswith("/static/") and ("v" in request.args or request.path.startswith("/static/fonts/")):
        resposta.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resposta


@app.context_processor
def inject_usuario():
    # Sem isso, cada rota que renderiza uma página precisaria lembrar de
    # passar usuario=current_user() na mão -- a rota "/" esqueceu isso (achado
    # real: login funcionava de verdade, sessão era criada certinho, mas a
    # página inicial nunca sabia disso, então o botão "Comprar ingresso"
    # continuava mudo e o "Sair" nunca aparecia -- parecia que o login não
    # tinha feito nada). Injetar aqui, uma vez, cobre TODAS as rotas/telas.
    usuario = current_user()
    return {"usuario": usuario, "tem_pedido": _TemPedido(usuario["id_usuario"]) if usuario else False}


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
        base_url=config.BASE_URL.rstrip("/"),
        promo_casadinha_valor=int(config.PROMO_CASADINHA_VALOR),
    )


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
