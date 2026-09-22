"""Área administrativa -- gestão de pedidos, hierarquia, material e eventos
(admin_required em toda rota, decorator já existe desde a Fase 2)."""
from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, request, session, url_for

import db
from auth import admin_required, current_user

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
    return render_template("admin/pedidos.html", pedidos=lista)


@admin_bp.route("/pedidos/<int:id_pedido>/confirmar", methods=["POST"])
@admin_required
def pedido_confirmar(id_pedido: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pedido SET status_pedido = 'confirmado' WHERE id_pedido = %s",
                (id_pedido,),
            )
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
    return render_template("admin/material.html", materiais=materiais)


@admin_bp.route("/eventos", methods=["GET", "POST"])
@admin_required
def eventos():
    if request.method == "POST":
        nome_evento = (request.form.get("nome_evento") or "").strip()
        data_evento = request.form.get("data_evento") or None
        numero_lote = request.form.get("numero_lote") or "1"
        valor_entrada = request.form.get("valor_entrada") or "0"
        quantidade_max_ingressos = request.form.get("quantidade_max_ingressos") or "2"
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO evento
                        (nome_evento, data_evento, numero_lote, valor_entrada,
                         quantidade_max_ingressos, usuario_cadastro)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        nome_evento,
                        data_evento,
                        numero_lote,
                        valor_entrada,
                        quantidade_max_ingressos,
                        current_user()["id_usuario"],
                    ),
                )
        return redirect(url_for("admin.eventos"))

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id_evento, e.nome_evento, e.data_evento, e.numero_lote,
                       e.valor_entrada, e.quantidade_max_ingressos, e.data_cadastro,
                       u.nome AS cadastrado_por
                FROM evento e
                LEFT JOIN login_usuario u ON u.id_usuario = e.usuario_cadastro
                ORDER BY e.data_cadastro DESC
                """
            )
            lista = cur.fetchall()
    return render_template("admin/eventos.html", eventos=lista)
