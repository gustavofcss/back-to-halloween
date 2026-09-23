"""Fluxo de compra de ingresso -- área do cliente (login_required)."""
from __future__ import annotations

import logging
import re

import mercadopago
import psycopg2
from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for

import config
import db
from auth import EMAIL_RE, current_user, login_required
from email_utils import enviar_confirmacao_pedido

pedidos_bp = Blueprint("pedidos", __name__)
logger = logging.getLogger(__name__)

# RG varia de formato por estado (sem dígito verificador padrão nacional,
# diferente do CPF) -- só números, com um X opcional no fim (dígito
# verificador usado em alguns estados, ex. SP). WhatsApp brasileiro: DDD (2)
# + número (8 ou 9) = 10 ou 11 dígitos. Antes não validava nada além de
# "não vazio" -- aceitava letra em qualquer posição e qualquer tamanho
# (achado real, usuário reportou).
#
# Duas etapas, não uma só: primeiro confere que o valor CRU só tem dígito +
# pontuação de formatação (nada de letra escondida no meio -- ex.
# "abc11987654321" não pode virar válido só porque, depois de tirar as
# letras, sobra um número de 11 dígitos); só DEPOIS de passar nisso é que
# tira a pontuação e confere o tamanho final.
#
# re.ASCII é obrigatório nos quatro: sem ele, \d no Python 3 casa qualquer
# dígito Unicode (categoria Nd) -- não só 0-9 ASCII. Dígito fullwidth
# ("１２３４５６７８９") ou arábico-índico ("٩") passava as duas etapas inteiro e
# ia pro banco como caractere não-ASCII, quebrando o "só números" que o
# validador promete (achado real em QA, verificado executando o validador).
_RG_CARACTERES_RE = re.compile(r"^[\d.\-/\sXx]+$", re.ASCII)
_RG_RE = re.compile(r"^\d{5,12}X?$", re.ASCII)
_WHATSAPP_CARACTERES_RE = re.compile(r"^[\d()\-+\s]+$", re.ASCII)
_WHATSAPP_RE = re.compile(r"^\d{10,11}$", re.ASCII)


def _validar_rg(bruto: str | None) -> str | None:
    bruto = (bruto or "").strip()
    if not bruto or not _RG_CARACTERES_RE.match(bruto):
        return None
    limpo = re.sub(r"[.\-/\s]", "", bruto).upper()
    return limpo if _RG_RE.match(limpo) else None


def _validar_whatsapp(bruto: str | None) -> str | None:
    bruto = (bruto or "").strip()
    if not bruto or not _WHATSAPP_CARACTERES_RE.match(bruto):
        return None
    limpo = re.sub(r"\D", "", bruto)
    return limpo if _WHATSAPP_RE.match(limpo) else None


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


def _confirmar_se_aprovado(cur, pedido: dict, usuario: dict, pagamento_mp: dict) -> str:
    """Dado um resultado JÁ CONSULTADO na API do Mercado Pago, confirma o
    pedido no banco se status == approved. Devolve o status do MP (ou o
    status atual do pedido, se o MP não devolveu nada útil). Centraliza a
    trava "AND status_pedido != 'confirmado'" + rowcount (nunca otimismo) e
    o try/except no envio de e-mail -- sem o try/except, uma falha
    transitória no Brevo estourava a exceção DENTRO do `with db.get_conn()`
    e desfazia (rollback) a própria confirmação do pagamento que já tinha
    acabado de ser aprovada pelo Mercado Pago (achado real nesta revisão)."""
    status_mp = pagamento_mp.get("status")
    if status_mp != "approved":
        return status_mp or pedido["status_pedido"]

    cur.execute(
        "UPDATE pedido SET status_pedido = 'confirmado' WHERE id_pedido = %s AND status_pedido != 'confirmado'",
        (pedido["id_pedido"],),
    )
    if cur.rowcount > 0:
        try:
            enviar_confirmacao_pedido(usuario["email"], usuario["nome"], pedido)
        except Exception:
            logger.warning("Falha ao enviar e-mail de confirmação de pedido", exc_info=True)
    return "confirmado"


def _verificar_e_confirmar_pagamento(cur, pedido: dict, usuario: dict) -> str:
    """Consulta o pagamento de verdade na API do Mercado Pago (não confia
    num clique) e confirma via _confirmar_se_aprovado. Usado tanto pelo
    botão "Já paguei, confirmar" quanto pelo polling automático da tela de
    pagamento -- nenhum dos dois marca como confirmado sem checar a API."""
    status_atual = pedido["status_pedido"]
    if status_atual == "confirmado" or not config.MERCADOPAGO_ACCESS_TOKEN or not pedido["mp_payment_id"]:
        return status_atual

    try:
        pagamento_mp = _consultar_pagamento(pedido["mp_payment_id"])
    except Exception:
        logger.warning("Falha ao verificar pagamento no Mercado Pago", exc_info=True)
        return status_atual

    return _confirmar_se_aprovado(cur, pedido, usuario, pagamento_mp)


@pedidos_bp.route("/comprar", methods=["GET", "POST"])
@login_required
def comprar():
    usuario = current_user()

    try:
        return _comprar_impl(usuario)
    except psycopg2.errors.UniqueViolation:
        # Rede de segurança contra a condição de corrida: o SELECT de
        # "ja_comprados" acima e o INSERT abaixo não são atômicos -- dois
        # envios concorrentes do MESMO usuário (duplo clique, duas abas)
        # podem os dois ler "zero pedidos" antes de qualquer um dos dois
        # commitar. O índice único parcial em schema.sql
        # (ux_pedido_usuario_evento_ativo) faz o segundo INSERT estourar
        # essa exceção em vez de criar um pedido duplicado -- capturada
        # aqui pra virar a mesma tela de erro amigável, não um 500
        # (achado real nesta revisão de segurança).
        return render_template("comprar.html", ja_tem_pedido=True)


def _comprar_impl(usuario: dict):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            evento = _evento_ativo(cur)
            if not evento:
                return render_template("comprar.html", sem_evento=True)

            # Um pedido por pessoa -- não é "até quantidade_max_ingressos ao
            # longo do tempo" (isso já tinha sido comprado antes e deixava
            # comprar de novo depois, achado real, usuário reportou). O
            # limite de quantidade_max_ingressos vale só DENTRO de um mesmo
            # pedido (você + 1 acompanhante no mesmo checkout).
            ja_comprados = _ingressos_comprados(cur, usuario["id_usuario"], evento["id_evento"])
            if ja_comprados > 0:
                return render_template("comprar.html", ja_tem_pedido=True)

            max_permitido = evento["quantidade_max_ingressos"]

            vagas_restantes = evento["capacidade_maxima"] - _ingressos_vendidos_total(cur)
            max_permitido = max(0, min(max_permitido, vagas_restantes))

            if max_permitido <= 0:
                return render_template("comprar.html", esgotado=True)

            if request.method == "GET":
                return render_template(
                    "comprar.html", usuario=usuario, evento=evento, max_permitido=max_permitido
                )

            rg = _validar_rg(request.form.get("rg"))
            whatsapp = _validar_whatsapp(request.form.get("whatsapp"))
            try:
                quantidade = int(request.form.get("quantidade") or 0)
            except ValueError:
                quantidade = 0

            vagas_restantes_atual = evento["capacidade_maxima"] - _ingressos_vendidos_total(cur)

            erro = None
            if (
                not rg
                or not whatsapp
                or quantidade < 1
                or quantidade > max_permitido
                or quantidade > vagas_restantes_atual
            ):
                erro = "Preencha RG (só números) e WhatsApp (DDD + número, só números) válidos, e uma quantidade válida."

            # O 2º ingresso pode ser pra um acompanhante -- não precisa ser
            # a mesma pessoa logada comprando. Só exige esses dados quando
            # quantidade = 2; com quantidade = 1 eles ficam NULL mesmo.
            acompanhante_nome = acompanhante_email = acompanhante_rg = acompanhante_whatsapp = None
            if not erro and quantidade == 2:
                acompanhante_nome = (request.form.get("acompanhante_nome") or "").strip()
                acompanhante_email = (request.form.get("acompanhante_email") or "").strip().lower()
                acompanhante_rg = _validar_rg(request.form.get("acompanhante_rg"))
                acompanhante_whatsapp = _validar_whatsapp(request.form.get("acompanhante_whatsapp"))
                if (
                    not acompanhante_nome
                    or not EMAIL_RE.match(acompanhante_email)
                    or not acompanhante_rg
                    or not acompanhante_whatsapp
                ):
                    erro = "Preencha nome, e-mail, RG (só números) e WhatsApp (só números) válidos do acompanhante."

            if erro:
                return render_template(
                    "comprar.html",
                    usuario=usuario,
                    evento=evento,
                    max_permitido=max_permitido,
                    erro=erro,
                ), 400

            valor = evento["valor_entrada"] * quantidade
            cur.execute(
                """
                INSERT INTO pedido (
                    id_usuario, id_evento, rg, whatsapp, valor, quantidade, status_pedido,
                    acompanhante_nome, acompanhante_email, acompanhante_rg, acompanhante_whatsapp
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'pendente', %s, %s, %s, %s)
                RETURNING id_pedido
                """,
                (
                    usuario["id_usuario"], evento["id_evento"], rg, whatsapp, valor, quantidade,
                    acompanhante_nome, acompanhante_email, acompanhante_rg, acompanhante_whatsapp,
                ),
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

            if pedido["status_pedido"] == "pendente":
                try:
                    if pedido["mp_payment_id"]:
                        pagamento_mp = _consultar_pagamento(pedido["mp_payment_id"])
                    else:
                        pagamento_mp = _criar_pagamento_pix(pedido, usuario)
                        cur.execute(
                            "UPDATE pedido SET mp_payment_id = %s WHERE id_pedido = %s",
                            (str(pagamento_mp["id"]), id_pedido),
                        )

                    # Reconsulta e confirma aqui também (não só no botão/
                    # polling) -- sem isso, quem saísse da tela de pagamento
                    # e voltasse depois de 5+ minutos (o polling em JS já
                    # tinha parado) continuava vendo o QR Code de um pedido
                    # que na verdade já tinha sido aprovado pelo Mercado
                    # Pago (achado real nesta revisão).
                    status_atual = _confirmar_se_aprovado(cur, pedido, usuario, pagamento_mp)
                    if status_atual == "confirmado":
                        pedido = dict(pedido)
                        pedido["status_pedido"] = "confirmado"
                    else:
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
    # "Já paguei, confirmar" consulta a API do Mercado Pago na hora -- não é
    # só um botão que mostra "aguardando" e espera o polling em background
    # pegar depois (achado real, usuário pediu confirmação de verdade pela
    # API aqui, não só client-side).
    usuario = current_user()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            pedido = _pedido_do_usuario(cur, id_pedido, usuario["id_usuario"])
            if not pedido:
                abort(404)
            status_atual = _verificar_e_confirmar_pagamento(cur, pedido, usuario)
            # Só troca o dict quando o MP realmente aprovou -- mesmo padrão do
            # GET /pagamento/<id>. status_atual pode vir no vocabulário do
            # Mercado Pago (ex. "rejected", "in_process", "pending"), que não
            # é o enum da coluna status_pedido ('pendente'/'confirmado'/
            # 'cancelado'); gravar esse valor bruto em pedido["status_pedido"]
            # contaminaria a condição do template (achado real nesta revisão).
            if status_atual == "confirmado":
                pedido = dict(pedido)
                pedido["status_pedido"] = "confirmado"

    return render_template(
        "pagamento.html",
        pedido=pedido,
        pix_key=config.PIX_KEY,
        aguardando=(status_atual != "confirmado"),
    )


@pedidos_bp.route("/pagamento/<int:id_pedido>/verificar", methods=["POST"])
@login_required
def pagamento_verificar(id_pedido: int):
    usuario = current_user()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            pedido = _pedido_do_usuario(cur, id_pedido, usuario["id_usuario"])
            if not pedido:
                abort(404)
            status_atual = _verificar_e_confirmar_pagamento(cur, pedido, usuario)

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

            # Mesma regra de "um pedido por pessoa" do /comprar: só mostra o
            # link de comprar mais quando o usuário ainda não tem NENHUM
            # pedido ativo (não é mais quantidade_max_ingressos - ja_comprados,
            # que reabria a compra depois de já ter um pedido).
            pode_comprar_mais = 0
            evento = _evento_ativo(cur)
            if evento:
                ja_comprados = _ingressos_comprados(cur, usuario["id_usuario"], evento["id_evento"])
                if ja_comprados == 0:
                    vagas_restantes = evento["capacidade_maxima"] - _ingressos_vendidos_total(cur)
                    pode_comprar_mais = max(0, min(evento["quantidade_max_ingressos"], vagas_restantes))

    return render_template("meus_pedidos.html", pedidos=pedidos, pode_comprar_mais=pode_comprar_mais)
