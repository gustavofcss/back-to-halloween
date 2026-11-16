"""Rotas de autenticação -- cadastro/login por e-mail+senha e por Google,
mais os helpers de autorização (login_required, admin_required,
current_user) reaproveitados pelas fases seguintes (pedidos, admin)."""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timezone
from functools import wraps

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.flask_client import OAuth
from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import config
import db
from limitador import limitar
from email_utils import avisar_admins_cadastro_pendente, enviar_codigo_recuperacao, enviar_codigo_verificacao_email

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


def _hash_do_codigo(codigo: str) -> str:
    """Hash do código de 6 dígitos. O scrypt padrão (N=32768) custa ~190 ms e 32 MiB
    por chamada, e um código de 6 dígitos que vale 15 min não ganha nada com isso:
    N=2048 custa ~2 ms. O formato do hash carrega os parâmetros, então códigos já
    emitidos (com o custo antigo) continuam sendo conferidos normalmente."""
    return generate_password_hash(codigo, method="scrypt:2048:8:1")


def _codigo_bem_formado(codigo: str) -> bool:
    """6 dígitos ASCII. Só um código DIGITADO por inteiro conta como tentativa
    errada: um envio vazio ou pela metade (o <input hidden required> não é
    validado pelo navegador) gastava as 5 tentativas sem ninguém ter chutado."""
    return len(codigo) == 6 and codigo.isascii() and codigo.isdigit()


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        # Conta apagada pelo admin (ou sessão revogada) com o cookie ainda válido: era
        # `usuario["id_usuario"]` em None e erro 500; agora volta pro login.
        if not session.get("user_id") or current_user() is None:
            session.clear()
            return redirect(url_for("auth.login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login", next=request.path))
        user = current_user()
        if user is None:
            session.clear()
            return redirect(url_for("auth.login", next=request.path))
        if user["hierarquia"] not in ("adm", "superadm"):
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped


def current_user() -> dict | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    # Uma consulta por requisição: admin_required, a rota e o context processor
    # (app.py) chamavam isso, e cada chamada ia ao banco.
    guardado = getattr(g, "_usuario_atual", None)
    if guardado is not None and guardado[0] == user_id:
        return guardado[1]
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id_usuario, u.nome, u.email, u.data_atualizacao, h.hierarquia
                FROM login_usuario u
                JOIN hierarquia h ON h.id_usuario = u.id_usuario
                WHERE u.id_usuario = %s
                """,
                (user_id,),
            )
            usuario = cur.fetchone()
    versao = session.get("sv")
    if usuario is not None and versao is not None and versao != _versao_da_sessao(usuario["data_atualizacao"]):
        # A conta foi alterada depois deste login (senha redefinida): cookie antigo,
        # inclusive um copiado ou roubado, deixa de valer.
        usuario = None
        session.clear()
    g._usuario_atual = (user_id, usuario)
    return usuario


def _versao_da_sessao(data_atualizacao) -> int:
    return int(data_atualizacao.timestamp()) if data_atualizacao else 0


def _abrir_sessao(id_usuario: int) -> None:
    """Abre a sessão (14 dias) guardando a "versão" da conta: trocar a senha muda
    `data_atualizacao` e derruba as sessões abertas antes disso."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data_atualizacao FROM login_usuario WHERE id_usuario = %s", (id_usuario,))
            linha = cur.fetchone()
    session.permanent = True
    session["user_id"] = id_usuario
    session["sv"] = _versao_da_sessao(linha["data_atualizacao"] if linha else None)


_NEXT_SEGURO_RE = re.compile(r"/(?!/)[^\s\\\x00-\x1f\x7f]*")


def _next_seguro(next_url: str | None) -> str | None:
    """`next_url` vem sem nenhuma validação de `request.form.get('next')`
    (form de /login) ou `request.args.get('next')` (query string de
    /auth/google, repassada via session) -- ambos controlados por quem
    monta o link, não pelo servidor. Só aceita caminho interno: uma barra, depois
    qualquer coisa menos outra barra, espaço, barra invertida ou caractere de
    controle em QUALQUER posição. Antes só olhava o começo: `/<TAB>/evil.example`
    passava, o Werkzeug tirava o TAB ao montar o cabeçalho e o `Location` virava
    `//evil.example` (open redirect); com \r\n derrubava o login com erro 500."""
    if not next_url:
        return None
    next_url = next_url.strip()
    if len(next_url) > 300 or not _NEXT_SEGURO_RE.fullmatch(next_url):
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
                "SELECT 1 FROM v_pedido WHERE id_usuario = %s AND status_pedido != 'cancelado' LIMIT 1",
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
@limitar("signup", 5)
@limitar("signup-hora", 30, 3600)
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
            codigo_hash = _hash_do_codigo(codigo)
            senha_hash = generate_password_hash(senha)
            cur.execute(
                """
                INSERT INTO login_usuario (nome, email, senha_hash, email_confirmado, email_verificado)
                VALUES (%s, %s, %s, FALSE, FALSE)
                RETURNING id_usuario
                """,
                (nome, email, senha_hash),
            )
            id_usuario = cur.fetchone()["id_usuario"]
            cur.execute(
                "INSERT INTO codigo_verificacao (id_usuario, tipo, codigo_hash, expira_em) "
                "VALUES (%s, 'email', %s, now() + interval '15 minutes')",
                (id_usuario, codigo_hash),
            )
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
@limitar("codigo", 10)
def confirmar_email():
    if request.method == "GET":
        email = (request.args.get("email") or "").strip().lower()
        return render_template("verifique_email.html", email=email)

    email = (request.form.get("email") or "").strip().lower()
    codigo = (request.form.get("codigo") or "").strip()

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            usuario = None
            if _codigo_bem_formado(codigo):
                # A tentativa é reservada ANTES de conferir o código, num UPDATE só: lendo
                # o código e contando depois, requisições em paralelo (2 workers x 4
                # threads) liam o mesmo contador e passavam ~8 palpites em vez de 5.
                # Só código de 6 dígitos DIGITADO por inteiro conta como tentativa.
                cur.execute(
                    "UPDATE codigo_verificacao c SET tentativas = c.tentativas + 1 "
                    "FROM login_usuario u "
                    "WHERE u.id_usuario = c.id_usuario AND u.email = %s AND u.email_verificado = FALSE "
                    "AND c.tipo = 'email' AND c.tentativas < %s "
                    "RETURNING u.id_usuario, u.nome, c.codigo_hash, c.expira_em, c.tentativas",
                    (email, _MAX_TENTATIVAS_CODIGO),
                )
                usuario = cur.fetchone()

            valido = bool(
                usuario
                and usuario["expira_em"]
                and usuario["expira_em"] > datetime.now(timezone.utc)
                and check_password_hash(usuario["codigo_hash"], codigo)
            )

            if not valido:
                if usuario and usuario["tentativas"] >= _MAX_TENTATIVAS_CODIGO:
                    # esgotou as tentativas: o código deixa de existir (só pedindo outro)
                    cur.execute(
                        "DELETE FROM codigo_verificacao WHERE id_usuario = %s AND tipo = 'email'",
                        (usuario["id_usuario"],),
                    )
                flash("Código inválido ou expirado.")
                return render_template("verifique_email.html", email=email), 400

            cur.execute(
                "UPDATE login_usuario SET email_verificado = TRUE, data_atualizacao = now() WHERE id_usuario = %s",
                (usuario["id_usuario"],),
            )
            cur.execute(
                "DELETE FROM codigo_verificacao WHERE id_usuario = %s AND tipo = 'email'",
                (usuario["id_usuario"],),
            )

    # Só chega aqui com o código certo: a conta acabou de entrar na fila.
    avisar_admins_cadastro_pendente(usuario["nome"], email)
    return render_template("cadastro_pendente.html")


@auth_bp.route("/confirmar-email/reenviar", methods=["POST"])
@limitar("reenviar", 5)
@limitar("reenviar-hora", 30, 3600)
def confirmar_email_reenviar():
    email = (request.form.get("email") or "").strip().lower()
    codigo = None

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            # "recente": o código atual saiu há menos de 1 minuto (ele vale 15).
            # Sem esse intervalo, o endpoint (sem login) servia pra disparar
            # e-mail em massa pra qualquer endereço.
            cur.execute(
                "SELECT u.id_usuario, u.nome, "
                "COALESCE(c.expira_em > now() + interval '14 minutes', FALSE) AS recente "
                "FROM login_usuario u "
                "LEFT JOIN codigo_verificacao c ON c.id_usuario = u.id_usuario AND c.tipo = 'email' "
                "WHERE u.email = %s AND u.email_verificado = FALSE",
                (email,),
            )
            usuario = cur.fetchone()

            if usuario and not usuario["recente"]:
                codigo = f"{secrets.randbelow(1000000):06d}"
                codigo_hash = _hash_do_codigo(codigo)
                cur.execute(
                    "INSERT INTO codigo_verificacao (id_usuario, tipo, codigo_hash, expira_em, tentativas) "
                    "VALUES (%s, 'email', %s, now() + interval '15 minutes', 0) "
                    "ON CONFLICT (id_usuario, tipo) DO UPDATE SET "
                    "codigo_hash = EXCLUDED.codigo_hash, expira_em = EXCLUDED.expira_em, tentativas = 0",
                    (usuario["id_usuario"], codigo_hash),
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
@limitar("mudar-email", 5)
@limitar("mudar-email-hora", 20, 3600)
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
            codigo_hash = _hash_do_codigo(codigo)
            cur.execute(
                "UPDATE login_usuario SET email = %s, data_atualizacao = now() WHERE id_usuario = %s",
                (novo_email, usuario["id_usuario"]),
            )
            # Código novo SEM zerar as tentativas: trocar de e-mail não pode dar mais 5 palpites.
            cur.execute(
                "INSERT INTO codigo_verificacao (id_usuario, tipo, codigo_hash, expira_em) "
                "VALUES (%s, 'email', %s, now() + interval '15 minutes') "
                "ON CONFLICT (id_usuario, tipo) DO UPDATE SET "
                "codigo_hash = EXCLUDED.codigo_hash, expira_em = EXCLUDED.expira_em",
                (usuario["id_usuario"], codigo_hash),
            )
            nome = usuario["nome"]

    try:
        enviar_codigo_verificacao_email(novo_email, nome, codigo)
    except Exception:
        logger.warning("Falha ao enviar código de verificação pro e-mail novo", exc_info=True)

    flash("E-mail atualizado. Mandamos um código novo pra ele.")
    return redirect(url_for("auth.confirmar_email", email=novo_email))


@auth_bp.route("/login", methods=["GET", "POST"])
@limitar("login", 10)
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

    _abrir_sessao(usuario["id_usuario"])
    destino = _destino_pos_login(usuario["id_usuario"], request.form.get("next"))
    return redirect(destino)


@auth_bp.route("/esqueci-senha", methods=["GET", "POST"])
@limitar("esqueci", 5)
@limitar("esqueci-hora", 20, 3600)
def esqueci_senha():
    if request.method == "GET":
        return render_template("esqueci_senha.html")

    email = (request.form.get("email") or "").strip().lower()
    codigo = None

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT u.id_usuario, u.nome, "
                "COALESCE(c.expira_em > now() + interval '14 minutes', FALSE) AS recente "
                "FROM login_usuario u "
                "LEFT JOIN codigo_verificacao c ON c.id_usuario = u.id_usuario AND c.tipo = 'senha' "
                "WHERE u.email = %s",
                (email,),
            )
            usuario = cur.fetchone()

            if usuario and not usuario["recente"]:
                codigo = f"{secrets.randbelow(1000000):06d}"
                codigo_hash = _hash_do_codigo(codigo)
                cur.execute(
                    "INSERT INTO codigo_verificacao (id_usuario, tipo, codigo_hash, expira_em, tentativas) "
                    "VALUES (%s, 'senha', %s, now() + interval '15 minutes', 0) "
                    "ON CONFLICT (id_usuario, tipo) DO UPDATE SET "
                    "codigo_hash = EXCLUDED.codigo_hash, expira_em = EXCLUDED.expira_em, tentativas = 0",
                    (usuario["id_usuario"], codigo_hash),
                )

    if codigo:
        try:
            enviar_codigo_recuperacao(email, usuario["nome"], codigo)
        except Exception:
            logger.warning("Falha ao enviar código de recuperação por e-mail", exc_info=True)

    flash("Se esse e-mail existir, enviamos um código de recuperação.")
    return redirect(url_for("auth.redefinir_senha", email=email))


@auth_bp.route("/redefinir-senha", methods=["GET", "POST"])
@limitar("codigo", 10)
def redefinir_senha():
    if request.method == "GET":
        return render_template("redefinir_senha.html")

    email = (request.form.get("email") or "").strip().lower()
    codigo = request.form.get("codigo") or ""
    nova_senha = request.form.get("senha") or ""

    # Senha inválida é avisada à parte e NÃO gasta tentativa do código (antes virava
    # "Código inválido" mesmo com o código certo).
    if not 6 <= len(nova_senha) <= 128:
        flash("A senha nova precisa ter entre 6 e 128 caracteres.")
        return render_template("redefinir_senha.html"), 400

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            usuario = None
            if _codigo_bem_formado(codigo):
                # Reserva a tentativa ANTES de conferir (ver confirmar_email).
                cur.execute(
                    "UPDATE codigo_verificacao c SET tentativas = c.tentativas + 1 "
                    "FROM login_usuario u "
                    "WHERE u.id_usuario = c.id_usuario AND u.email = %s "
                    "AND c.tipo = 'senha' AND c.tentativas < %s "
                    "RETURNING u.id_usuario, c.codigo_hash, c.expira_em, c.tentativas",
                    (email, _MAX_TENTATIVAS_CODIGO),
                )
                usuario = cur.fetchone()

            valido = bool(
                usuario
                and usuario["expira_em"]
                and usuario["expira_em"] > datetime.now(timezone.utc)
                and check_password_hash(usuario["codigo_hash"], codigo)
            )

            if not valido:
                if usuario and usuario["tentativas"] >= _MAX_TENTATIVAS_CODIGO:
                    cur.execute(
                        "DELETE FROM codigo_verificacao WHERE id_usuario = %s AND tipo = 'senha'",
                        (usuario["id_usuario"],),
                    )
                flash("Código inválido ou expirado.")
                return render_template("redefinir_senha.html"), 400

            # data_atualizacao muda aqui: as sessões abertas antes disso deixam de valer
            cur.execute(
                "UPDATE login_usuario SET senha_hash = %s, data_atualizacao = now() WHERE id_usuario = %s",
                (generate_password_hash(nova_senha), usuario["id_usuario"]),
            )
            cur.execute(
                "DELETE FROM codigo_verificacao WHERE id_usuario = %s AND tipo = 'senha'",
                (usuario["id_usuario"],),
            )

    flash("Senha redefinida com sucesso. Faça login com a nova senha.")
    return redirect(url_for("auth.login"))


@auth_bp.route("/auth/google")
def auth_google():
    # Guarda pra onde o usuário ia (ex.: veio de /comprar via login_required)
    # -- sem isso, depois do login pelo Google ele sempre caía na tela
    # padrão em vez de continuar o que estava fazendo.
    session["login_next"] = _next_seguro(request.args.get("next"))
    redirect_uri = url_for("auth.auth_google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/auth/google/callback")
def auth_google_callback():
    try:
        token = oauth.google.authorize_access_token()
    except OAuthError:
        # state expirado/ausente (voltar pelo botão do celular, abrir a URL direto...)
        flash("Não deu pra concluir o login com o Google. Tente de novo.")
        return redirect(url_for("auth.login"))
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
                "SELECT id_usuario, email_confirmado, email_verificado FROM login_usuario "
                "WHERE google_id = %s OR email = %s "
                "ORDER BY COALESCE(google_id = %s, FALSE) DESC LIMIT 1",
                (google_id, email, google_id),
            )
            usuario = cur.fetchone()

            entrou_na_fila = False
            if usuario:
                id_usuario = usuario["id_usuario"]
                email_confirmado = usuario["email_confirmado"]
                # Conta que ainda não estava verificada e que o admin ainda não
                # aprovou: o login Google a coloca na fila agora.
                entrou_na_fila = not usuario["email_verificado"] and not email_confirmado
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
                    "email_verificado = TRUE, "
                    "data_atualizacao = now() WHERE id_usuario = %s",
                    (google_id, id_usuario),
                )
                cur.execute(
                    "DELETE FROM codigo_verificacao WHERE id_usuario = %s AND tipo = 'email'",
                    (id_usuario,),
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
                entrou_na_fila = True

    if entrou_na_fila:
        avisar_admins_cadastro_pendente(nome, email)

    if not email_confirmado:
        session.pop("login_next", None)
        return render_template("cadastro_pendente.html"), 401

    _abrir_sessao(id_usuario)
    destino = _destino_pos_login(id_usuario, session.pop("login_next", None))
    return redirect(destino)


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    # Aceita GET também -- achado real: recarregar/reabrir a página depois
    # de sair (ou o auto-refresh do admin pegando essa URL) reenviava um GET
    # pra /logout, que só aceitava POST e dava "Method Not Allowed".
    session.clear()
    return redirect(url_for("index"))
