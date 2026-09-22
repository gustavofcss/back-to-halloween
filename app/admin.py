"""Área administrativa -- gestão de pedidos, hierarquia, material e eventos
(admin_required em toda rota, decorator já existe desde a Fase 2)."""
from __future__ import annotations

import logging

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

import db
from auth import admin_required, current_user
from email_utils import enviar_cadastro_aprovado, enviar_confirmacao_pedido

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("")
@admin_required
def index():
    return render_template("admin/index.html")


@admin_bp.route("/pedidos")
@admin_required
def pedidos():
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id_pedido, p.quantidade, p.valor, p.status_pedido, p.data_pedido,
                       u.nome, u.email
                FROM pedido p
                JOIN login_usuario u ON u.id_usuario = p.id_usuario
                ORDER BY p.data_pedido DESC
                """
            )
            lista = cur.fetchall()

            cur.execute("SELECT capacidade_maxima FROM evento ORDER BY data_cadastro DESC LIMIT 1")
            evento_atual = cur.fetchone()
            capacidade_maxima = evento_atual["capacidade_maxima"] if evento_atual else 60

            cur.execute(
                "SELECT COALESCE(SUM(quantidade), 0) AS total FROM pedido WHERE status_pedido != 'cancelado'"
            )
            ingressos_vendidos_total = cur.fetchone()["total"]

    return render_template(
        "admin/pedidos.html",
        pedidos=lista,
        capacidade_maxima=capacidade_maxima,
        ingressos_vendidos_total=ingressos_vendidos_total,
    )


@admin_bp.route("/pedidos/<int:id_pedido>/confirmar", methods=["POST"])
@admin_required
def pedido_confirmar(id_pedido: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.*, u.nome, u.email
                FROM pedido p
                JOIN login_usuario u ON u.id_usuario = p.id_usuario
                WHERE p.id_pedido = %s
                """,
                (id_pedido,),
            )
            pedido = cur.fetchone()
            if not pedido:
                abort(404)

            ja_confirmado = pedido["status_pedido"] == "confirmado"
            cur.execute(
                "UPDATE pedido SET status_pedido = 'confirmado' WHERE id_pedido = %s",
                (id_pedido,),
            )

    if not ja_confirmado:
        try:
            enviar_confirmacao_pedido(pedido["email"], pedido["nome"], pedido)
        except Exception:
            logger.warning("Falha ao enviar e-mail de confirmação de pedido", exc_info=True)
            flash("Pedido confirmado, mas não consegui enviar o e-mail pro cliente. Avise ele manualmente.")

    return redirect(url_for("admin.pedidos"))


@admin_bp.route("/pedidos/<int:id_pedido>/cancelar", methods=["POST"])
@admin_required
def pedido_cancelar(id_pedido: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pedido SET status_pedido = 'cancelado' WHERE id_pedido = %s",
                (id_pedido,),
            )
    return redirect(url_for("admin.pedidos"))


@admin_bp.route("/hierarquia")
@admin_required
def hierarquia():
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id_usuario, u.nome, u.email, h.hierarquia
                FROM login_usuario u
                JOIN hierarquia h ON h.id_usuario = u.id_usuario
                ORDER BY u.nome
                """
            )
            usuarios = cur.fetchall()
    return render_template("admin/hierarquia.html", usuarios=usuarios, eu=current_user())


@admin_bp.route("/hierarquia/<int:id_usuario>/promover", methods=["POST"])
@admin_required
def hierarquia_promover(id_usuario: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE hierarquia SET hierarquia = 'adm' WHERE id_usuario = %s",
                (id_usuario,),
            )
    return redirect(url_for("admin.hierarquia"))


@admin_bp.route("/hierarquia/<int:id_usuario>/rebaixar", methods=["POST"])
@admin_required
def hierarquia_rebaixar(id_usuario: int):
    if id_usuario == session.get("user_id"):
        abort(400, "Não é possível rebaixar a si mesmo.")
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE hierarquia SET hierarquia = 'cliente' WHERE id_usuario = %s",
                (id_usuario,),
            )
    return redirect(url_for("admin.hierarquia"))


@admin_bp.route("/hierarquia/<int:id_usuario>/remover", methods=["POST"])
@admin_required
def hierarquia_remover(id_usuario: int):
    if id_usuario == session.get("user_id"):
        abort(400, "Não é possível apagar a si mesmo.")
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM login_usuario WHERE id_usuario = %s AND id_usuario IN "
                "(SELECT id_usuario FROM hierarquia WHERE hierarquia = 'cliente')",
                (id_usuario,),
            )
    return redirect(url_for("admin.hierarquia"))


@admin_bp.route("/cadastros")
@admin_required
def cadastros():
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_usuario, nome, email, data_cadastro FROM login_usuario "
                "WHERE email_confirmado = FALSE ORDER BY data_cadastro"
            )
            pendentes = cur.fetchall()
    return render_template("admin/cadastros.html", pendentes=pendentes)


@admin_bp.route("/cadastros/<int:id_usuario>/confirmar", methods=["POST"])
@admin_required
def cadastro_confirmar(id_usuario: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT nome, email FROM login_usuario WHERE id_usuario = %s AND email_confirmado = FALSE",
                (id_usuario,),
            )
            usuario = cur.fetchone()
            if not usuario:
                abort(404)

            cur.execute(
                "UPDATE login_usuario SET email_confirmado = TRUE, data_atualizacao = now() "
                "WHERE id_usuario = %s",
                (id_usuario,),
            )

    try:
        enviar_cadastro_aprovado(usuario["email"], usuario["nome"])
    except Exception:
        logger.warning("Falha ao enviar e-mail de cadastro aprovado", exc_info=True)
        flash("Cadastro confirmado, mas não consegui enviar o e-mail pro cliente. Avise ele manualmente.")
    return redirect(url_for("admin.cadastros"))


@admin_bp.route("/cadastros/<int:id_usuario>/recusar", methods=["POST"])
@admin_required
def cadastro_recusar(id_usuario: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM login_usuario WHERE id_usuario = %s AND email_confirmado = FALSE",
                (id_usuario,),
            )
    return redirect(url_for("admin.cadastros"))


@admin_bp.route("/material", methods=["GET", "POST"])
@admin_required
def material():
    if request.method == "POST":
        nome_material = (request.form.get("nome_material") or "").strip()
        valor = request.form.get("valor") or "0"
        descricao = (request.form.get("descricao") or "").strip()
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO despesa_material (nome_material, valor, descricao, usuario_cadastro)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (nome_material, valor, descricao, current_user()["id_usuario"]),
                )
        return redirect(url_for("admin.material"))

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.id, m.nome_material, m.valor, m.descricao, m.data_compra, u.nome AS cadastrado_por
                FROM despesa_material m
                LEFT JOIN login_usuario u ON u.id_usuario = m.usuario_cadastro
                ORDER BY m.data_compra DESC
                """
            )
            materiais = cur.fetchall()

            cur.execute(
                "SELECT COALESCE(SUM(valor), 0) AS total FROM pedido WHERE status_pedido = 'confirmado'"
            )
            total_arrecadado = cur.fetchone()["total"]

            cur.execute("SELECT COALESCE(SUM(valor), 0) AS total FROM despesa_material")
            total_gasto = cur.fetchone()["total"]

    saldo = total_arrecadado - total_gasto
    return render_template(
        "admin/material.html",
        materiais=materiais,
        total_arrecadado=total_arrecadado,
        total_gasto=total_gasto,
        saldo=saldo,
    )


@admin_bp.route("/material/<int:id_material>/apagar", methods=["POST"])
@admin_required
def material_apagar(id_material: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM despesa_material WHERE id = %s", (id_material,))
    return redirect(url_for("admin.material"))


@admin_bp.route("/eventos", methods=["GET", "POST"])
@admin_required
def eventos():
    # O nome, a data e a hora do evento são sempre os mesmos (é a MESMA
    # festa) -- só o preço (e o número do lote, calculado sozinho) mudam a
    # cada lote novo. Por isso o form de "cadastrar novo lote" não pede mais
    # nome/data/hora/número: eles são herdados do último lote cadastrado
    # (achado real: não fazia sentido dar pra editar a data do evento ao
    # cadastrar um lote novo, nem sempre sugerir "1" quando o lote 1 já
    # existia).
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM evento ORDER BY data_cadastro DESC LIMIT 1")
            ultimo = cur.fetchone()

    if request.method == "POST":
        valor_entrada = request.form.get("valor_entrada") or "0"
        quantidade_max_ingressos = request.form.get("quantidade_max_ingressos") or (
            str(ultimo["quantidade_max_ingressos"]) if ultimo else "2"
        )
        nome_evento = ultimo["nome_evento"] if ultimo else "Halloween"
        data_evento = ultimo["data_evento"] if ultimo else None
        hora_evento = ultimo["hora_evento"] if ultimo else None
        capacidade_maxima = ultimo["capacidade_maxima"] if ultimo else 60
        numero_lote = (ultimo["numero_lote"] + 1) if ultimo else 1

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO evento
                        (nome_evento, data_evento, hora_evento, numero_lote, valor_entrada,
                         quantidade_max_ingressos, capacidade_maxima, usuario_cadastro)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        nome_evento,
                        data_evento,
                        hora_evento,
                        numero_lote,
                        valor_entrada,
                        quantidade_max_ingressos,
                        capacidade_maxima,
                        current_user()["id_usuario"],
                    ),
                )
        return redirect(url_for("admin.eventos"))

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id_evento, e.nome_evento, e.data_evento, e.hora_evento, e.numero_lote,
                       e.valor_entrada, e.quantidade_max_ingressos, e.capacidade_maxima, e.data_cadastro,
                       u.nome AS cadastrado_por
                FROM evento e
                LEFT JOIN login_usuario u ON u.id_usuario = e.usuario_cadastro
                ORDER BY e.data_cadastro DESC
                """
            )
            lista = cur.fetchall()

            cur.execute(
                "SELECT COALESCE(SUM(quantidade), 0) AS total FROM pedido WHERE status_pedido != 'cancelado'"
            )
            ingressos_vendidos_total = cur.fetchone()["total"]

    proximo_lote = (ultimo["numero_lote"] + 1) if ultimo else 1
    capacidade_maxima = ultimo["capacidade_maxima"] if ultimo else 60
    return render_template(
        "admin/eventos.html",
        eventos=lista,
        ultimo=ultimo,
        proximo_lote=proximo_lote,
        capacidade_maxima=capacidade_maxima,
        ingressos_vendidos_total=ingressos_vendidos_total,
    )


@admin_bp.route("/eventos/capacidade", methods=["POST"])
@admin_required
def eventos_capacidade():
    capacidade_maxima = request.form.get("capacidade_maxima") or "60"
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_evento FROM evento ORDER BY data_cadastro DESC LIMIT 1")
            ultimo = cur.fetchone()
            if ultimo:
                cur.execute(
                    "UPDATE evento SET capacidade_maxima = %s WHERE id_evento = %s",
                    (capacidade_maxima, ultimo["id_evento"]),
                )
    return redirect(url_for("admin.eventos"))
