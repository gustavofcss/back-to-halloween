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
from email_utils import enviar_codigo_recuperacao, enviar_codigo_verificacao_email

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

# Os códigos de 6 dígitos (confirmar e-mail, redefinir senha) têm só 1 milhão de
# combinações: sem limite de tentativas dava pra forçar o código e tomar a
# conta de qualquer e-mail. Depois de _MAX_TENTATIVAS_CODIGO erros o código é
# invalidado e a pessoa precisa pedir outro.
_MAX_TENTATIVAS_CODIGO = 5


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
        if not user or user["hierarquia"] not in ("adm", "superadm"):
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


def _next_seguro(next_url: str | None) -> str | None:
    """`next_url` vem sem nenhuma validação de `request.form.get('next')`
    (form de /login) ou `request.args.get('next')` (query string de
    /auth/google, repassada via session) -- ambos controlados por quem
    monta o link, não pelo servidor. Sem essa checagem, `/login?next=
    https://evil.example/phish` (ou `//evil.example`, redirect
    "protocol-relative") redireciona a vítima pra fora do site logo depois
    de um login bem-sucedido (open redirect, achado real nesta revisão de
    segurança) -- só aceita caminho interno, começando com uma única barra."""
    if not next_url:
        return None
    next_url = next_url.strip()
    if not next_url.startswith("/") or next_url.startswith("//") or next_url.startswith("/\\"):
        return None
    return next_url


def _destino_pos_login(id_usuario: int, next_url: str | None = None) -> str:
    """Admin cai direto na área de gestão, sempre -- mesmo se veio de um link
    com `next` (ex.: clicou em "Comprar ingresso" deslogado, que manda pra
    /login?next=/comprar; sem essa checagem de hierarquia vir PRIMEIRO, um
    admin também caía em /comprar, achado real). Cliente respeita o `next`
    quando existir; sem next, vai direto pra "Meus pedidos" (se já tiver
    pedido) ou "Seus dados"/comprar (se não tiver) -- nunca a tela inicial,
    pedido explícito do usuário."""
    next_url = _next_seguro(next_url)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT hierarquia FROM hierarquia WHERE id_usuario = %s", (id_usuario,))
            row = cur.fetchone()
    if row and row["hierarquia"] in ("adm", "superadm"):
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

    if (
        len(nome.split()) < 2
        or len(nome) > 120
        or len(email) > 160
        or not EMAIL_RE.match(email)
        or len(senha) < 6
        or len(senha) > 128
    ):
        flash("Preencha o nome completo (nome e sobrenome), e-mail válido e senha com pelo menos 6 caracteres.")
        return render_template("signup.html"), 400

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM login_usuario WHERE email = %s", (email,))
            if cur.fetchone():
                flash("Já existe uma conta com esse e-mail.")
                return render_template("signup.html"), 400

            codigo = f"{secrets.randbelow(1000000):06d}"
            codigo_hash = generate_password_hash(codigo)
            senha_hash = generate_password_hash(senha)
            cur.execute(
                """
                INSERT INTO login_usuario (
                    nome, email, senha_hash, email_confirmado,
                    email_verificado, email_verificacao_codigo_hash, email_verificacao_codigo_expira
                )
                VALUES (%s, %s, %s, FALSE, FALSE, %s, now() + interval '15 minutes')
                RETURNING id_usuario
                """,
                (nome, email, senha_hash, codigo_hash),
            )
            id_usuario = cur.fetchone()["id_usuario"]
            cur.execute(
                "INSERT INTO hierarquia (id_usuario, hierarquia) VALUES (%s, 'cliente')",
                (id_usuario,),
            )

    try:
        enviar_codigo_verificacao_email(email, nome, codigo)
    except Exception:
        logger.warning("Falha ao enviar código de verificação de e-mail", exc_info=True)

    return render_template("verifique_email.html", email=email)


@auth_bp.route("/confirmar-email", methods=["GET", "POST"])
def confirmar_email():
    if request.method == "GET":
        email = (request.args.get("email") or "").strip().lower()
        return render_template("verifique_email.html", email=email)

    email = (request.form.get("email") or "").strip().lower()
    codigo = (request.form.get("codigo") or "").strip()

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_usuario, email_verificado, email_verificacao_codigo_hash, "
                "email_verificacao_codigo_expira FROM login_usuario WHERE email = %s",
                (email,),
            )
            usuario = cur.fetchone()

            valido = (
                usuario
                and not usuario["email_verificado"]
                and usuario["email_verificacao_codigo_hash"]
                and usuario["email_verificacao_codigo_expira"]
                and usuario["email_verificacao_codigo_expira"] > datetime.now(timezone.utc)
                and check_password_hash(usuario["email_verificacao_codigo_hash"], codigo)
            )

            if not valido:
                if usuario and usuario["email_verificacao_codigo_hash"]:
                    cur.execute(
                        "UPDATE login_usuario SET "
                        "email_verificacao_tentativas = email_verificacao_tentativas + 1, "
                        "email_verificacao_codigo_hash = CASE WHEN email_verificacao_tentativas + 1 >= %s "
                        "THEN NULL ELSE email_verificacao_codigo_hash END "
                        "WHERE id_usuario = %s",
                        (_MAX_TENTATIVAS_CODIGO, usuario["id_usuario"]),
                    )
                flash("Código inválido ou expirado.")
                return render_template("verifique_email.html", email=email), 400

            cur.execute(
                "UPDATE login_usuario SET email_verificado = TRUE, email_verificacao_tentativas = 0, "
                "email_verificacao_codigo_hash = NULL, email_verificacao_codigo_expira = NULL, "
                "data_atualizacao = now() WHERE id_usuario = %s",
                (usuario["id_usuario"],),
            )

    return render_template("cadastro_pendente.html")


@auth_bp.route("/confirmar-email/reenviar", methods=["POST"])
def confirmar_email_reenviar():
    email = (request.form.get("email") or "").strip().lower()
    codigo = None

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            # "recente": o código atual saiu há menos de 1 minuto (ele vale 15).
            # Sem esse intervalo, o endpoint (sem login) servia pra disparar
            # e-mail em massa pra qualquer endereço.
            cur.execute(
                "SELECT id_usuario, nome, "
                "email_verificacao_codigo_expira > now() + interval '14 minutes' AS recente "
                "FROM login_usuario WHERE email = %s AND email_verificado = FALSE",
                (email,),
            )
            usuario = cur.fetchone()

            if usuario and not usuario["recente"]:
                codigo = f"{secrets.randbelow(1000000):06d}"
                codigo_hash = generate_password_hash(codigo)
                cur.execute(
                    "UPDATE login_usuario SET email_verificacao_codigo_hash = %s, "
                    "email_verificacao_tentativas = 0, "
                    "email_verificacao_codigo_expira = now() + interval '15 minutes' "
                    "WHERE id_usuario = %s",
                    (codigo_hash, usuario["id_usuario"]),
                )

    if codigo:
        try:
            enviar_codigo_verificacao_email(email, usuario["nome"], codigo)
        except Exception:
            logger.warning("Falha ao reenviar código de verificação de e-mail", exc_info=True)

    if usuario and usuario["recente"]:
        # Intervalo mínimo de 1 min entre códigos: avisar, senão a pessoa clica
        # em "Reenviar" achando que mandou e nada sai.
        flash("Aguarde 1 minuto antes de pedir outro código -- o último acabou de ser enviado. Olhe também a caixa de spam.")
    else:
        flash("Se esse e-mail existir e ainda não tiver sido confirmado, reenviamos o código. Olhe também a caixa de spam.")
    return render_template("verifique_email.html", email=email)


@auth_bp.route("/confirmar-email/mudar-email", methods=["POST"])
def confirmar_email_mudar():
    # "Errei o e-mail no cadastro" -- pedido explícito do usuário (a tela de
    # confirmação não tinha como corrigir isso, só dava pra voltar pro login
    # e recomeçar do zero). Só deixa mudar enquanto a conta ainda não foi
    # confirmada (email_verificado = FALSE) -- depois de confirmada, mudar
    # o e-mail é outra operação (edição de perfil, fora de escopo aqui) e
    # nunca sequestra uma conta já ativa.
    email_atual = (request.form.get("email_atual") or "").strip().lower()
    novo_email = (request.form.get("novo_email") or "").strip().lower()

    if not EMAIL_RE.match(novo_email) or len(novo_email) > 160:
        flash("Digite um e-mail válido.")
        return render_template("verifique_email.html", email=email_atual), 400

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_usuario, nome FROM login_usuario WHERE email = %s AND email_verificado = FALSE",
                (email_atual,),
            )
            usuario = cur.fetchone()

            if not usuario:
                flash("Não achamos esse cadastro pendente -- tente de novo pelo login.")
                return redirect(url_for("auth.login"))

            cur.execute("SELECT 1 FROM login_usuario WHERE email = %s", (novo_email,))
            if cur.fetchone():
                flash("Já existe uma conta com esse e-mail novo.")
                return render_template("verifique_email.html", email=email_atual), 400

            codigo = f"{secrets.randbelow(1000000):06d}"
            codigo_hash = generate_password_hash(codigo)
            cur.execute(
                "UPDATE login_usuario SET email = %s, email_verificacao_codigo_hash = %s, "
                "email_verificacao_tentativas = 0, "
                "email_verificacao_codigo_expira = now() + interval '15 minutes', "
                "data_atualizacao = now() WHERE id_usuario = %s",
                (novo_email, codigo_hash, usuario["id_usuario"]),
            )
            nome = usuario["nome"]

    try:
        enviar_codigo_verificacao_email(novo_email, nome, codigo)
    except Exception:
        logger.warning("Falha ao enviar código de verificação pro e-mail novo", exc_info=True)

    flash("E-mail atualizado. Mandamos um código novo pra ele.")
    return redirect(url_for("auth.confirmar_email", email=novo_email))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    email = (request.form.get("email") or "").strip().lower()
    senha = request.form.get("senha") or ""

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_usuario, senha_hash, email_confirmado, email_verificado FROM login_usuario "
                "WHERE email = %s",
                (email,),
            )
            usuario = cur.fetchone()

    if not usuario or not usuario["senha_hash"] or not check_password_hash(usuario["senha_hash"], senha):
        flash("E-mail ou senha inválidos.")
        return render_template("login.html"), 401

    if not usuario["email_verificado"]:
        # Confirmar o e-mail vem ANTES da aprovação do admin -- sem essa
        # checagem aqui, alguém que nunca confirmou o código cairia direto
        # na tela de "aguardando aprovação" (mensagem errada, e o admin nem
        # deveria estar vendo esse cadastro na fila ainda).
        return render_template("verifique_email.html", email=email), 401

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
    codigo = None

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_usuario, nome, "
                "reset_codigo_expira > now() + interval '14 minutes' AS recente "
                "FROM login_usuario WHERE email = %s",
                (email,),
            )
            usuario = cur.fetchone()

            if usuario and not usuario["recente"]:
                codigo = f"{secrets.randbelow(1000000):06d}"
                codigo_hash = generate_password_hash(codigo)
                cur.execute(
                    "UPDATE login_usuario SET reset_codigo_hash = %s, reset_tentativas = 0, "
                    "reset_codigo_expira = now() + interval '15 minutes' "
                    "WHERE id_usuario = %s",
                    (codigo_hash, usuario["id_usuario"]),
                )

    if codigo:
        try:
            enviar_codigo_recuperacao(email, usuario["nome"], codigo)
        except Exception:
            logger.warning("Falha ao enviar código de recuperação por e-mail", exc_info=True)

    flash("Se esse e-mail existir, enviamos um código de recuperação.")
    return redirect(url_for("auth.redefinir_senha", email=email))


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

            if not valido and usuario and usuario["reset_codigo_hash"]:
                cur.execute(
                    "UPDATE login_usuario SET reset_tentativas = reset_tentativas + 1, "
                    "reset_codigo_hash = CASE WHEN reset_tentativas + 1 >= %s "
                    "THEN NULL ELSE reset_codigo_hash END "
                    "WHERE id_usuario = %s",
                    (_MAX_TENTATIVAS_CODIGO, usuario["id_usuario"]),
                )

            if not valido or len(nova_senha) < 6 or len(nova_senha) > 128:
                flash("Código inválido ou expirado.")
                return render_template("redefinir_senha.html"), 400

            cur.execute(
                "UPDATE login_usuario SET senha_hash = %s, reset_tentativas = 0, reset_codigo_hash = NULL, "
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
    nome = (userinfo.get("name") or email)[:120]
    if not userinfo.get("email_verified") or len(email) > 160:
        flash("Não foi possível entrar com essa conta Google: o e-mail dela não está verificado.")
        return redirect(url_for("auth.login"))

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
                # email_verificado = TRUE aqui também -- se a conta já existia
                # (cadastro por e-mail/senha ainda não confirmado) e a pessoa
                # loga com Google usando o mesmo e-mail, o Google acabou de
                # provar que ela é dona dele; não faz sentido continuar
                # exigindo o código por e-mail depois disso.
                # Se a conta existia SEM e-mail verificado (cadastro por senha que
                # nunca confirmou o código), quem escolheu aquela senha pode não
                # ser o dono do e-mail -- alguém podia ter cadastrado o e-mail da
                # vítima antes dela. Nesse caso a senha antiga é descartada, senão
                # o login Google da vítima "verificava" a conta e a senha do
                # atacante passava a funcionar nela.
                cur.execute(
                    "UPDATE login_usuario SET google_id = %s, "
                    "senha_hash = CASE WHEN email_verificado THEN senha_hash ELSE NULL END, "
                    "email_verificado = TRUE, email_verificacao_codigo_hash = NULL, "
                    "email_verificacao_codigo_expira = NULL, "
                    "data_atualizacao = now() WHERE id_usuario = %s",
                    (google_id, id_usuario),
                )
            else:
                # Login com Google não isenta da aprovação manual do admin --
                # pedido explícito do usuário (achado real: cadastro via
                # Google entrava direto, sem passar pela fila de "Cadastros
                # pendentes"). Mesma regra do cadastro por e-mail/senha.
                # email_verificado já entra TRUE -- o Google já confirmou que
                # esse e-mail é do dono da conta como parte do próprio login
                # OAuth, não faz sentido mandar mais um código por e-mail.
                cur.execute(
                    """
                    INSERT INTO login_usuario (nome, email, senha_hash, google_id, email_confirmado, email_verificado)
                    VALUES (%s, %s, NULL, %s, FALSE, TRUE)
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
