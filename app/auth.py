"""Rotas de autenticação -- cadastro/login por e-mail+senha e por Google,
mais os helpers de autorização (login_required, admin_required,
current_user) reaproveitados pelas fases seguintes (pedidos, admin)."""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timezone
from functools import wraps

from authlib.integrations.flask_client import OAuth
from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import config
import db
from email_utils import enviar_codigo_recuperacao

auth_bp = Blueprint("auth", __name__)
logger = logging.getLogger(__name__)

oauth = OAuth()
oauth.register(
    name="google",
    client_id=config.GOOGLE_CLIENT_ID,
    client_secret=config.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login", next=request.path))
        user = current_user()
        if not user or user["hierarquia"] != "adm":
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped


def current_user() -> dict | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id_usuario, u.nome, u.email, h.hierarquia
                FROM login_usuario u
                JOIN hierarquia h ON h.id_usuario = u.id_usuario
                WHERE u.id_usuario = %s
                """,
                (user_id,),
            )
            return cur.fetchone()


def _destino_pos_login(id_usuario: int, next_url: str | None = None) -> str:
    """Admin cai direto na área de gestão, sempre -- mesmo se veio de um link
    com `next` (ex.: clicou em "Comprar ingresso" deslogado, que manda pra
    /login?next=/comprar; sem essa checagem de hierarquia vir PRIMEIRO, um
    admin também caía em /comprar, achado real). Cliente respeita o `next`
    quando existir; sem next, vai direto pra "Meus pedidos" (se já tiver
    pedido) ou "Seus dados"/comprar (se não tiver) -- nunca a tela inicial,
    pedido explícito do usuário."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT hierarquia FROM hierarquia WHERE id_usuario = %s", (id_usuario,))
            row = cur.fetchone()
    if row and row["hierarquia"] == "adm":
        return url_for("admin.index")

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pedido WHERE id_usuario = %s AND status_pedido != 'cancelado' LIMIT 1",
                (id_usuario,),
            )
            tem_pedido = cur.fetchone() is not None

    # Quem já tem pedido nunca deve cair em /comprar, nem por um `next`
    # antigo -- sem essa checagem, alguém que clicou "Comprar ingresso"
    # deslogado (next=/comprar), fez login e JÁ tinha um pedido de antes
    # continuava caindo direto na tela de comprar de novo (achado real,
    # usuário reportou; /comprar em si também bloqueia, mas cair já na tela
    # certa evita o "quique" visual). Qualquer outro `next` (ex.: voltar
    # pra um /pagamento/<id> específico) continua respeitado normalmente.
    comprar_url = url_for("pedidos.comprar")
    if next_url and not (tem_pedido and next_url.split("?", 1)[0] == comprar_url):
        return next_url

    return url_for("pedidos.meus_pedidos") if tem_pedido else url_for("pedidos.comprar")


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return render_template("signup.html")

    nome = (request.form.get("nome") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    senha = request.form.get("senha") or ""

    if not nome or not EMAIL_RE.match(email) or len(senha) < 6:
        flash("Preencha nome, e-mail válido e senha com pelo menos 6 caracteres.")
        return render_template("signup.html"), 400

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM login_usuario WHERE email = %s", (email,))
            if cur.fetchone():
                flash("Já existe uma conta com esse e-mail.")
                return render_template("signup.html"), 400

            senha_hash = generate_password_hash(senha)
            cur.execute(
                """
                INSERT INTO login_usuario (nome, email, senha_hash, email_confirmado)
                VALUES (%s, %s, %s, FALSE)
                RETURNING id_usuario
                """,
                (nome, email, senha_hash),
            )
            id_usuario = cur.fetchone()["id_usuario"]
            cur.execute(
                "INSERT INTO hierarquia (id_usuario, hierarquia) VALUES (%s, 'cliente')",
                (id_usuario,),
            )

    return render_template("verifique_email.html", email=email)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    email = (request.form.get("email") or "").strip().lower()
    senha = request.form.get("senha") or ""

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_usuario, senha_hash, email_confirmado FROM login_usuario "
                "WHERE email = %s",
                (email,),
            )
            usuario = cur.fetchone()

    if not usuario or not usuario["senha_hash"] or not check_password_hash(usuario["senha_hash"], senha):
        flash("E-mail ou senha inválidos.")
        return render_template("login.html"), 401

    if not usuario["email_confirmado"]:
        # Antes só reexibia o form de login com um aviso em cima -- usuário
        # achou confuso/poluído (spiders, campo de senha etc. no meio de um
        # aviso de "aguarde"). Reaproveita o estilo limpo do
        # "Verifique seu e-mail", mesma tela que aparece logo após cadastrar.
        return render_template("cadastro_pendente.html"), 401

    session["user_id"] = usuario["id_usuario"]
    destino = _destino_pos_login(usuario["id_usuario"], request.form.get("next"))
    return redirect(destino)


@auth_bp.route("/esqueci-senha", methods=["GET", "POST"])
def esqueci_senha():
    if request.method == "GET":
        return render_template("esqueci_senha.html")

    email = (request.form.get("email") or "").strip().lower()

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_usuario, nome FROM login_usuario WHERE email = %s",
                (email,),
            )
            usuario = cur.fetchone()

            if usuario:
                codigo = f"{secrets.randbelow(1000000):06d}"
                codigo_hash = generate_password_hash(codigo)
                cur.execute(
                    "UPDATE login_usuario SET reset_codigo_hash = %s, "
                    "reset_codigo_expira = now() + interval '15 minutes' "
                    "WHERE id_usuario = %s",
                    (codigo_hash, usuario["id_usuario"]),
                )

    if usuario:
        try:
            enviar_codigo_recuperacao(email, usuario["nome"], codigo)
        except Exception:
            logger.warning("Falha ao enviar código de recuperação por e-mail", exc_info=True)

    flash("Se esse e-mail existir, enviamos um código de recuperação.")
    return render_template("esqueci_senha.html")


@auth_bp.route("/redefinir-senha", methods=["GET", "POST"])
def redefinir_senha():
    if request.method == "GET":
        return render_template("redefinir_senha.html")

    email = (request.form.get("email") or "").strip().lower()
    codigo = request.form.get("codigo") or ""
    nova_senha = request.form.get("senha") or ""

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_usuario, reset_codigo_hash, reset_codigo_expira FROM login_usuario "
                "WHERE email = %s",
                (email,),
            )
            usuario = cur.fetchone()

            valido = (
                usuario
                and usuario["reset_codigo_hash"]
                and usuario["reset_codigo_expira"]
                and usuario["reset_codigo_expira"] > datetime.now(timezone.utc)
                and check_password_hash(usuario["reset_codigo_hash"], codigo)
            )

            if not valido or len(nova_senha) < 6:
                flash("Código inválido ou expirado.")
                return render_template("redefinir_senha.html"), 400

            cur.execute(
                "UPDATE login_usuario SET senha_hash = %s, reset_codigo_hash = NULL, "
                "reset_codigo_expira = NULL, data_atualizacao = now() WHERE id_usuario = %s",
                (generate_password_hash(nova_senha), usuario["id_usuario"]),
            )

    flash("Senha redefinida com sucesso. Faça login com a nova senha.")
    return redirect(url_for("auth.login"))


@auth_bp.route("/auth/google")
def auth_google():
    # Guarda pra onde o usuário ia (ex.: veio de /comprar via login_required)
    # -- sem isso, depois do login pelo Google ele sempre caía na tela
    # padrão em vez de continuar o que estava fazendo.
    session["login_next"] = request.args.get("next")
    redirect_uri = url_for("auth.auth_google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/auth/google/callback")
def auth_google_callback():
    token = oauth.google.authorize_access_token()
    userinfo = token["userinfo"]
    google_id = userinfo["sub"]
    email = userinfo["email"].strip().lower()
    nome = userinfo.get("name") or email

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_usuario, email_confirmado FROM login_usuario WHERE google_id = %s OR email = %s",
                (google_id, email),
            )
            usuario = cur.fetchone()

            if usuario:
                id_usuario = usuario["id_usuario"]
                email_confirmado = usuario["email_confirmado"]
                cur.execute(
                    "UPDATE login_usuario SET google_id = %s, data_atualizacao = now() "
                    "WHERE id_usuario = %s",
                    (google_id, id_usuario),
                )
            else:
                # Login com Google não isenta da aprovação manual do admin --
                # pedido explícito do usuário (achado real: cadastro via
                # Google entrava direto, sem passar pela fila de "Cadastros
                # pendentes"). Mesma regra do cadastro por e-mail/senha.
                cur.execute(
                    """
                    INSERT INTO login_usuario (nome, email, senha_hash, google_id, email_confirmado)
                    VALUES (%s, %s, NULL, %s, FALSE)
                    RETURNING id_usuario
                    """,
                    (nome, email, google_id),
                )
                id_usuario = cur.fetchone()["id_usuario"]
                cur.execute(
                    "INSERT INTO hierarquia (id_usuario, hierarquia) VALUES (%s, 'cliente')",
                    (id_usuario,),
                )
                email_confirmado = False

    if not email_confirmado:
        session.pop("login_next", None)
        return render_template("cadastro_pendente.html"), 401

    session["user_id"] = id_usuario
    destino = _destino_pos_login(id_usuario, session.pop("login_next", None))
    return redirect(destino)


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    # Aceita GET também -- achado real: recarregar/reabrir a página depois
    # de sair (ou o auto-refresh do admin pegando essa URL) reenviava um GET
    # pra /logout, que só aceitava POST e dava "Method Not Allowed".
    session.clear()
    return redirect(url_for("index"))
