"""Fluxo de compra de ingresso -- área do cliente (login_required)."""
from __future__ import annotations

import logging

import mercadopago
from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for

import config
import db
from auth import current_user, login_required
from email_utils import enviar_confirmacao_pedido

pedidos_bp = Blueprint("pedidos", __name__)
logger = logging.getLogger(__name__)


def _sdk() -> "mercadopago.SDK":
    return mercadopago.SDK(config.MERCADOPAGO_ACCESS_TOKEN)


def _criar_pagamento_pix(pedido, usuario) -> dict:
    payment_data = {
        "transaction_amount": float(pedido["valor"]),
        "description": f"Ingresso Horror Night Party -- pedido #{pedido['id_pedido']}",
        "payment_method_id": "pix",
        "payer": {"email": usuario["email"], "first_name": usuario["nome"]},
    }
    resultado = _sdk().payment().create(payment_data)
    return resultado["response"]


def _consultar_pagamento(mp_payment_id: str) -> dict:
    resultado = _sdk().payment().get(mp_payment_id)
    return resultado["response"]


def _evento_ativo(cur):
    cur.execute("SELECT * FROM evento ORDER BY data_cadastro DESC LIMIT 1")
    return cur.fetchone()


def _ingressos_comprados(cur, id_usuario: int, id_evento: int) -> int:
    cur.execute(
        "SELECT COALESCE(SUM(quantidade), 0) AS total FROM pedido "
        "WHERE id_usuario = %s AND id_evento = %s AND status_pedido != 'cancelado'",
        (id_usuario, id_evento),
    )
    return cur.fetchone()["total"]


def _ingressos_vendidos_total(cur) -> int:
    cur.execute(
        "SELECT COALESCE(SUM(quantidade), 0) AS total FROM pedido WHERE status_pedido != 'cancelado'"
    )
    return cur.fetchone()["total"]


def _pedido_do_usuario(cur, id_pedido: int, id_usuario: int):
    cur.execute(
        "SELECT * FROM pedido WHERE id_pedido = %s AND id_usuario = %s",
        (id_pedido, id_usuario),
    )
    return cur.fetchone()


@pedidos_bp.route("/comprar", methods=["GET", "POST"])
@login_required
def comprar():
    usuario = current_user()

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            evento = _evento_ativo(cur)
            if not evento:
                return render_template("comprar.html", sem_evento=True)

            ja_comprados = _ingressos_comprados(cur, usuario["id_usuario"], evento["id_evento"])
            max_permitido = evento["quantidade_max_ingressos"] - ja_comprados

            if max_permitido <= 0:
                return render_template("comprar.html", limite_atingido=True)

            vagas_restantes = evento["capacidade_maxima"] - _ingressos_vendidos_total(cur)
            max_permitido = max(0, min(max_permitido, vagas_restantes))

            if max_permitido <= 0:
                return render_template("comprar.html", esgotado=True)

            if request.method == "GET":
                return render_template(
                    "comprar.html", usuario=usuario, evento=evento, max_permitido=max_permitido
                )

            rg = (request.form.get("rg") or "").strip()
            whatsapp = (request.form.get("whatsapp") or "").strip()
            try:
                quantidade = int(request.form.get("quantidade") or 0)
            except ValueError:
                quantidade = 0

            vagas_restantes_atual = evento["capacidade_maxima"] - _ingressos_vendidos_total(cur)

            if (
                not rg
                or not whatsapp
                or quantidade < 1
                or quantidade > max_permitido
                or quantidade > vagas_restantes_atual
            ):
                return render_template(
                    "comprar.html",
                    usuario=usuario,
                    evento=evento,
                    max_permitido=max_permitido,
                    erro="Preencha RG, WhatsApp e uma quantidade válida.",
                ), 400

            valor = evento["valor_entrada"] * quantidade
            cur.execute(
                """
                INSERT INTO pedido (id_usuario, id_evento, rg, whatsapp, valor, quantidade, status_pedido)
                VALUES (%s, %s, %s, %s, %s, %s, 'pendente')
                RETURNING id_pedido
                """,
                (usuario["id_usuario"], evento["id_evento"], rg, whatsapp, valor, quantidade),
            )
            id_pedido = cur.fetchone()["id_pedido"]

    return redirect(url_for("pedidos.pagamento", id_pedido=id_pedido))


@pedidos_bp.route("/pagamento/<int:id_pedido>")
@login_required
def pagamento(id_pedido: int):
    usuario = current_user()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            pedido = _pedido_do_usuario(cur, id_pedido, usuario["id_usuario"])
            if not pedido:
                abort(404)

            if not config.MERCADOPAGO_ACCESS_TOKEN:
                return render_template(
                    "pagamento.html", pedido=pedido, pix_key=config.PIX_KEY, mp_configurado=False
                )

            qr_code_base64 = qr_code = mp_erro = None

            if pedido["status_pedido"] != "confirmado":
                try:
                    if pedido["mp_payment_id"]:
                        pagamento_mp = _consultar_pagamento(pedido["mp_payment_id"])
                    else:
                        pagamento_mp = _criar_pagamento_pix(pedido, usuario)
                        cur.execute(
                            "UPDATE pedido SET mp_payment_id = %s WHERE id_pedido = %s",
                            (str(pagamento_mp["id"]), id_pedido),
                        )
                    transacao = (pagamento_mp.get("point_of_interaction") or {}).get(
                        "transaction_data"
                    ) or {}
                    qr_code_base64 = transacao.get("qr_code_base64")
                    qr_code = transacao.get("qr_code")
                except Exception:
                    logger.warning(
                        "Falha ao criar/consultar pagamento Pix no Mercado Pago", exc_info=True
                    )
                    mp_erro = "Não foi possível gerar o Pix agora. Tente novamente em instantes."

    return render_template(
        "pagamento.html",
        pedido=pedido,
        pix_key=config.PIX_KEY,
        mp_configurado=True,
        qr_code_base64=qr_code_base64,
        qr_code=qr_code,
        mp_erro=mp_erro,
    )


@pedidos_bp.route("/pagamento/<int:id_pedido>/confirmar", methods=["POST"])
@login_required
def pagamento_confirmar(id_pedido: int):
    usuario = current_user()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            pedido = _pedido_do_usuario(cur, id_pedido, usuario["id_usuario"])
    if not pedido:
        abort(404)
    return render_template("pagamento.html", pedido=pedido, pix_key=config.PIX_KEY, aguardando=True)


@pedidos_bp.route("/pagamento/<int:id_pedido>/verificar", methods=["POST"])
@login_required
def pagamento_verificar(id_pedido: int):
    usuario = current_user()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            pedido = _pedido_do_usuario(cur, id_pedido, usuario["id_usuario"])
            if not pedido:
                abort(404)

            status_atual = pedido["status_pedido"]

            if (
                status_atual != "confirmado"
                and config.MERCADOPAGO_ACCESS_TOKEN
                and pedido["mp_payment_id"]
            ):
                try:
                    resultado = _consultar_pagamento(pedido["mp_payment_id"])
                    status_mp = resultado.get("status")
                    if status_mp == "approved":
                        cur.execute(
                            "UPDATE pedido SET status_pedido = 'confirmado' WHERE id_pedido = %s",
                            (id_pedido,),
                        )
                        enviar_confirmacao_pedido(usuario["email"], usuario["nome"], pedido)
                        status_atual = "confirmado"
                    else:
                        status_atual = status_mp or status_atual
                except Exception:
                    logger.warning("Falha ao verificar pagamento no Mercado Pago", exc_info=True)

    return jsonify({"status": status_atual})


@pedidos_bp.route("/pagamento/<int:id_pedido>/cartao", methods=["POST"])
@login_required
def pagamento_cartao(id_pedido: int):
    usuario = current_user()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            pedido = _pedido_do_usuario(cur, id_pedido, usuario["id_usuario"])
            if not pedido:
                abort(404)

    # Link estático de Checkout Pro (criado manualmente no painel do Mercado
    # Pago para cada valor possível) em vez de uma preference dinâmica --
    # pedido explícito do usuário.
    link = config.LINKS_PAGAMENTO_CARTAO.get(round(float(pedido["valor"]), 2))
    if not link:
        logger.warning("Sem link de pagamento por cartão cadastrado para o valor %s", pedido["valor"])
        return redirect(url_for("pedidos.pagamento", id_pedido=id_pedido))

    return redirect(link)


@pedidos_bp.route("/meus-pedidos")
@login_required
def meus_pedidos():
    usuario = current_user()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM pedido WHERE id_usuario = %s ORDER BY data_pedido DESC",
                (usuario["id_usuario"],),
            )
            pedidos = cur.fetchall()

            pode_comprar_mais = 0
            evento = _evento_ativo(cur)
            if evento:
                ja_comprados = _ingressos_comprados(cur, usuario["id_usuario"], evento["id_evento"])
                vagas_restantes = evento["capacidade_maxima"] - _ingressos_vendidos_total(cur)
                pode_comprar_mais = max(
                    0, min(evento["quantidade_max_ingressos"] - ja_comprados, vagas_restantes)
                )

    return render_template("meus_pedidos.html", pedidos=pedidos, pode_comprar_mais=pode_comprar_mais)
