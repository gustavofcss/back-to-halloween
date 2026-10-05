"""Fluxo de compra de ingresso -- área do cliente (login_required)."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import mercadopago
import psycopg2
import requests
from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for

import config
import db
from auth import EMAIL_RE, current_user, login_required
from email_utils import enviar_confirmacao_pedido
from pix_copia_cola import gerar_copia_cola, gerar_qr_base64

pedidos_bp = Blueprint("pedidos", __name__)
logger = logging.getLogger(__name__)

# Janela pra pagar o Pix -- depois disso o pedido expira sozinho e libera a
# vaga, em vez de ficar "pendente" pra sempre esperando alguém que talvez
# nunca pague (pedido explícito do usuário: forçar o cliente a pagar dentro
# de uma margem de tempo). Só vale quando o Mercado Pago está configurado
# (o modo manual de chave Pix -- config.PIX_KEYS sem token -- não expira
# sozinho, porque não tem como confirmar automaticamente rápido o
# suficiente pra isso ser justo com quem pagou e só não foi conferido a
# tempo pelo admin).
_JANELA_PAGAMENTO = timedelta(minutes=5)

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


def _consultar_pagamento(mp_payment_id: str) -> dict:
    resultado = _sdk().payment().get(mp_payment_id)
    return resultado["response"]


def _buscar_pagamentos_pix_aprovados(desde: datetime) -> list[dict]:
    """Consulta a API de Pagamentos (não a de relatório de liquidação --
    essa só cobre reserva de venda e nunca mostrou um Pix pago direto na
    chave, mesmo horas depois, achado real testado com dinheiro de
    verdade) por Pix aprovados desde `desde`. 30s de folga pra trás contra
    desalinhamento de relógio entre nosso servidor e o Mercado Pago -- a
    filtragem de verdade (achar o pedido certo) é por valor + reivindicação
    única em `_tentar_conciliar_pix_direto`, não por essa janela sozinha."""
    params = {
        "begin_date": (desde - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.000-00:00"),
        "end_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000-00:00"),
        "range": "date_approved",
        "sort": "date_approved",
        "criteria": "desc",
    }
    resp = requests.get(
        "https://api.mercadopago.com/v1/payments/search",
        headers={"Authorization": f"Bearer {config.MERCADOPAGO_ACCESS_TOKEN}"},
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def _tentar_conciliar_pix_direto(cur, pedido: dict, usuario: dict) -> str:
    """Pix pago DIRETO na chave (config.PIX_KEYS, sem passar pela API de
    Checkout -- evita a taxa de ~1%) não tem um mp_payment_id conhecido de
    antemão, porque a gente nunca chamou o Mercado Pago pra criar nada; o
    QR mostrado ao cliente é só um payload Pix comum, gerado localmente
    (ver pix_copia_cola.py). Então, em vez de reconsultar um pagamento
    específico, procura na API de Pagamentos um Pix aprovado que bata em
    VALOR (não tem nome do pagador disponível pra esse tipo de Pix --
    achado real, testado -- valor é o que temos) e que ainda não tenha
    sido usado por nenhum outro pedido.

    Reivindica o pagamento de forma atômica: o UPDATE só confirma se
    "status_pedido = 'pendente'" (rowcount, nunca otimismo, mesmo padrão
    de todo o arquivo) E o índice único em mp_payment_id (schema.sql)
    garante que o MESMO pagamento nunca é usado duas vezes -- se dois
    pedidos pendentes do mesmo valor caírem na mesma consulta (2 pessoas
    pagando quase juntas), o segundo estoura UniqueViolation aqui, não
    confirma nada, e tenta de novo sozinho na próxima consulta (5s depois,
    ver pagamento_verificar) com os candidatos que sobraram. Sem essa
    trava, uma corrida podia confirmar dois pedidos com o MESMO pagamento
    real (dinheiro contado duas vezes)."""
    cur.execute("SELECT mp_payment_id FROM pedido WHERE mp_payment_id IS NOT NULL")
    ja_usados = {r["mp_payment_id"] for r in cur.fetchall()}

    try:
        candidatos = _buscar_pagamentos_pix_aprovados(pedido["data_pedido"])
    except Exception:
        logger.warning("Falha ao buscar pagamentos Pix no Mercado Pago", exc_info=True)
        return pedido["status_pedido"]

    valor_pedido = Decimal(str(pedido["valor"]))
    for pagamento in candidatos:
        mp_payment_id = str(pagamento.get("id") or "")
        if (
            not mp_payment_id
            or mp_payment_id in ja_usados
            or pagamento.get("status") != "approved"
            or pagamento.get("payment_method_id") != "pix"
            or Decimal(str(pagamento.get("transaction_amount", "0"))) != valor_pedido
        ):
            continue

        try:
            cur.execute(
                "UPDATE pedido SET mp_payment_id = %s, status_pedido = 'confirmado' "
                "WHERE id_pedido = %s AND status_pedido = 'pendente'",
                (mp_payment_id, pedido["id_pedido"]),
            )
        except psycopg2.errors.UniqueViolation:
            # Outro pedido reivindicou esse MESMO pagamento entre o SELECT
            # de ja_usados e este UPDATE (corrida real) -- precisa de
            # rollback explícito antes de continuar usando o cursor, senão
            # Postgres rejeita qualquer comando seguinte até um rollback
            # (mesmo padrão de admin.py eventos_apagar/ForeignKeyViolation).
            cur.connection.rollback()
            continue

        if cur.rowcount > 0:
            try:
                enviar_confirmacao_pedido(usuario["email"], usuario["nome"], pedido)
            except Exception:
                logger.warning("Falha ao enviar e-mail de confirmação de pedido", exc_info=True)
            return "confirmado"

    return pedido["status_pedido"]


def _evento_ativo(cur):
    cur.execute("SELECT * FROM evento ORDER BY data_cadastro DESC LIMIT 1")
    return cur.fetchone()


def _ingressos_vendidos_total(cur) -> int:
    cur.execute(
        "SELECT COALESCE(SUM(quantidade), 0) AS total FROM pedido WHERE status_pedido != 'cancelado'"
    )
    return cur.fetchone()["total"]


def _valor_do_pedido(evento: dict, slot: int, quantidade: int) -> Decimal:
    """Casadinha: com a promoção ligada pelo admin, 2 ingressos no mesmo
    pedido (slot 1, você + acompanhante) saem por config.PROMO_CASADINHA_VALOR.
    O 2º pedido separado (slot 2, 1 ingresso) nunca entra na promoção."""
    if evento.get("promo_casadinha") and slot == 1 and quantidade == 2:
        return Decimal(str(config.PROMO_CASADINHA_VALOR))
    return evento["valor_entrada"] * quantidade


def _pedido_do_usuario(cur, id_pedido: int, id_usuario: int):
    cur.execute(
        "SELECT * FROM pedido WHERE id_pedido = %s AND id_usuario = %s",
        (id_pedido, id_usuario),
    )
    return cur.fetchone()


def _pedidos_ativos(cur, id_usuario: int, id_evento: int) -> list:
    cur.execute(
        "SELECT * FROM pedido WHERE id_usuario = %s AND id_evento = %s "
        "AND status_pedido != 'cancelado' ORDER BY slot",
        (id_usuario, id_evento),
    )
    return cur.fetchall()


def _titular_do_pedido(pedido: dict, nome_conta: str, email_conta: str) -> dict:
    """Nome/e-mail de quem vai USAR esse pedido -- titular_nome/titular_email
    preenchidos (pedido de slot 2, comprado pra outra pessoa) têm
    prioridade; sem isso (slot 1, o padrão desde sempre), é a própria
    conta logada. Usado em todo lugar que manda e-mail de confirmação ou
    cria o pagamento no Mercado Pago, pra chegar na pessoa certa."""
    return {
        "nome": pedido.get("titular_nome") or nome_conta,
        "email": pedido.get("titular_email") or email_conta,
    }


def _chave_pix_do_pedido(pedido: dict) -> str | None:
    """Uma chave Pix DIFERENTE por valor (config.PIX_KEYS) -- mesmo padrão
    de config.LINKS_PAGAMENTO_CARTAO/pagamento_cartao. `round(...)` porque
    valor vem do Postgres como Decimal, e a chave do dict é float."""
    return config.PIX_KEYS.get(round(float(pedido["valor"]), 2))


def _confirmar_se_aprovado(cur, pedido: dict, usuario: dict, pagamento_mp: dict) -> str:
    """Dado um resultado JÁ CONSULTADO na API do Mercado Pago, confirma o
    pedido no banco se status == approved. Devolve o status do MP (ou o
    status atual do pedido, se o MP não devolveu nada útil). Centraliza a
    trava "AND status_pedido = 'pendente'" + rowcount (nunca otimismo) e o
    try/except no envio de e-mail -- sem o try/except, uma falha transitória
    no Brevo estourava a exceção DENTRO do `with db.get_conn()` e desfazia
    (rollback) a própria confirmação do pagamento que já tinha acabado de
    ser aprovada pelo Mercado Pago (achado real nesta revisão).

    Guarda "= 'pendente'" (não "!= 'confirmado'"): um Pix que só aprova
    DEPOIS do pedido já ter sido cancelado (pelo admin, ou expirado sozinho
    depois dos 5 minutos, ver _expirar_pedido) não pode ressuscitar um
    pedido cancelado -- a vaga já pode ter sido liberada/realocada."""
    status_mp = pagamento_mp.get("status")
    if status_mp != "approved":
        return status_mp or pedido["status_pedido"]

    cur.execute(
        "UPDATE pedido SET status_pedido = 'confirmado' WHERE id_pedido = %s AND status_pedido = 'pendente'",
        (pedido["id_pedido"],),
    )
    if cur.rowcount > 0:
        try:
            enviar_confirmacao_pedido(usuario["email"], usuario["nome"], pedido)
        except Exception:
            logger.warning("Falha ao enviar e-mail de confirmação de pedido", exc_info=True)
    return "confirmado"


def _verificar_e_confirmar_pagamento(cur, pedido: dict, usuario: dict) -> str:
    """Confirma o pagamento de verdade (não confia num clique) -- usado
    tanto pelo botão "Já paguei, confirmar" quanto pelo polling automático
    da tela de pagamento. Dois caminhos: se o pedido JÁ tem um
    mp_payment_id reivindicado (Pix direto já conciliado antes, ou pedido
    antigo do fluxo de Checkout), reconsulta esse pagamento específico. Se
    ainda não tem (Pix direto, ninguém pagou ainda ou ainda não achamos o
    pagamento), tenta conciliar por valor -- ver
    _tentar_conciliar_pix_direto."""
    status_atual = pedido["status_pedido"]
    if status_atual != "pendente" or not config.MERCADOPAGO_ACCESS_TOKEN:
        return status_atual

    if not pedido["mp_payment_id"]:
        return _tentar_conciliar_pix_direto(cur, pedido, usuario)

    try:
        pagamento_mp = _consultar_pagamento(pedido["mp_payment_id"])
    except Exception:
        logger.warning("Falha ao verificar pagamento no Mercado Pago", exc_info=True)
        return status_atual

    return _confirmar_se_aprovado(cur, pedido, usuario, pagamento_mp)


def _pedido_expirado(pedido: dict) -> bool:
    return datetime.now(timezone.utc) - pedido["data_pedido"] > _JANELA_PAGAMENTO


def _segundos_restantes(pedido: dict) -> int:
    decorrido = (datetime.now(timezone.utc) - pedido["data_pedido"]).total_seconds()
    return max(0, int(_JANELA_PAGAMENTO.total_seconds() - decorrido))


def _expirar_pedido(cur, pedido: dict) -> str:
    """Só chega aqui depois de reconsultar o Mercado Pago (nunca expira um
    pedido sem checar antes se ele não tinha acabado de ser aprovado bem
    na hora do timeout -- mesma trava "AND status_pedido != alvo" +
    rowcount de toda mutação de pedido neste arquivo, nunca otimismo)."""
    cur.execute(
        "UPDATE pedido SET status_pedido = 'cancelado', expirado = TRUE "
        "WHERE id_pedido = %s AND status_pedido = 'pendente'",
        (pedido["id_pedido"],),
    )
    return "cancelado" if cur.rowcount > 0 else pedido["status_pedido"]


def _verificar_com_expiracao(cur, pedido: dict, titular: dict) -> str:
    """Reconsulta o pagamento e, se ainda não foi aprovado E já passou da
    janela de 5 minutos, expira o pedido (libera a vaga). Usado nas 3 rotas
    que podem encerrar o ciclo de vida do pedido pendente (GET da tela de
    pagamento, "Já paguei" e o polling automático) -- nenhuma marca como
    expirado sem reconsultar a API primeiro."""
    status_atual = _verificar_e_confirmar_pagamento(cur, pedido, titular)
    if status_atual != "confirmado" and config.MERCADOPAGO_ACCESS_TOKEN and _pedido_expirado(pedido):
        status_atual = _expirar_pedido(cur, pedido)
    return status_atual


@pedidos_bp.route("/comprar", methods=["GET", "POST"])
@login_required
def comprar():
    usuario = current_user()

    try:
        return _comprar_impl(usuario)
    except psycopg2.errors.UniqueViolation:
        # Rede de segurança contra a condição de corrida: o SELECT de
        # "existentes"/slot acima e o INSERT abaixo não são atômicos -- dois
        # envios concorrentes do MESMO usuário pro MESMO slot (duplo clique,
        # duas abas) podem os dois ler o mesmo slot livre antes de qualquer
        # um dos dois commitar. O índice único parcial em schema.sql
        # (ux_pedido_usuario_evento_slot_ativo, agora incluindo "slot") faz
        # o segundo INSERT estourar essa exceção em vez de criar um pedido
        # duplicado no mesmo slot -- capturada aqui pra virar a mesma tela
        # de erro amigável, não um 500 (achado real nesta revisão de
        # segurança).
        return render_template("comprar.html", ja_tem_pedido=True)


def _comprar_impl(usuario: dict):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            evento = _evento_ativo(cur)
            if not evento:
                return render_template("comprar.html", sem_evento=True)

            # Até 2 PEDIDOS por conta+evento (não é "até quantidade_max_
            # ingressos ao longo do tempo", achado real de antes que deixava
            # comprar de novo depois de já ter pedido). O 1º (slot 1) é
            # sempre o titular = a própria conta, com o limite de
            # quantidade_max_ingressos valendo DENTRO dele (você + 1
            # acompanhante no mesmo checkout). O 2º (slot 2) é um pedido À
            # PARTE pra OUTRA pessoa -- pedido explícito do usuário: quem já
            # comprou pode comprar mais 1 ingresso, mas só se for pra
            # alguém diferente (nome/e-mail/RG/WhatsApp não podem ser os
            # mesmos já usados pela conta).
            existentes = _pedidos_ativos(cur, usuario["id_usuario"], evento["id_evento"])
            if len(existentes) >= 2:
                return render_template("comprar.html", limite_atingido=True)

            slot = 2 if existentes else 1
            max_permitido = evento["quantidade_max_ingressos"] if slot == 1 else 1

            vagas_restantes = evento["capacidade_maxima"] - _ingressos_vendidos_total(cur)
            max_permitido = max(0, min(max_permitido, vagas_restantes))

            if max_permitido <= 0:
                return render_template("comprar.html", esgotado=True)

            if request.method == "GET":
                return render_template(
                    "comprar.html", usuario=usuario, evento=evento,
                    max_permitido=max_permitido, slot=slot,
                    promo_casadinha_valor=int(config.PROMO_CASADINHA_VALOR),
                )

            rg = _validar_rg(request.form.get("rg"))
            whatsapp = _validar_whatsapp(request.form.get("whatsapp"))
            if slot == 1:
                try:
                    quantidade = int(request.form.get("quantidade") or 0)
                except ValueError:
                    quantidade = 0
            else:
                # Slot 2 é sempre 1 ingresso -- já É o pedido de uma outra
                # pessoa, não tem "acompanhante do acompanhante".
                quantidade = 1

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

            titular_nome = titular_email = None
            if not erro and slot == 2:
                titular_nome = (request.form.get("titular_nome") or "").strip()
                titular_email = (request.form.get("titular_email") or "").strip().lower()
                if not titular_nome or not EMAIL_RE.match(titular_email):
                    erro = "Preencha nome e e-mail válidos de quem vai usar esse ingresso."
                elif (
                    titular_nome.casefold() == usuario["nome"].strip().casefold()
                    or titular_email == usuario["email"]
                ):
                    erro = "Esse ingresso é pra OUTRA pessoa -- use o nome e e-mail dela, não os seus."
                else:
                    # RG/WhatsApp também não podem repetir o que a própria
                    # conta já usou (nos dados dela ou do acompanhante do
                    # slot 1) -- sem isso, dava pra "disfarçar" um 2º
                    # ingresso pra si mesmo só trocando nome/e-mail.
                    usados_rg = set()
                    usados_whatsapp = set()
                    for p in existentes:
                        for campo, destino in (
                            ("rg", usados_rg), ("acompanhante_rg", usados_rg),
                            ("whatsapp", usados_whatsapp), ("acompanhante_whatsapp", usados_whatsapp),
                        ):
                            if p[campo]:
                                destino.add(p[campo])
                    if rg in usados_rg or whatsapp in usados_whatsapp:
                        erro = "Esse RG ou WhatsApp já foi usado num pedido seu -- use os dados de quem vai usar esse ingresso."

            # O 2º ingresso DENTRO do pedido de slot 1 pode ser pra um
            # acompanhante -- não precisa ser a mesma pessoa logada
            # comprando. Só exige esses dados quando quantidade = 2; com
            # quantidade = 1 eles ficam NULL mesmo. Não existe no slot 2
            # (que já É o pedido de outra pessoa, sempre quantidade 1).
            acompanhante_nome = acompanhante_email = acompanhante_rg = acompanhante_whatsapp = None
            if not erro and slot == 1 and quantidade == 2:
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
                    slot=slot,
                    promo_casadinha_valor=int(config.PROMO_CASADINHA_VALOR),
                    erro=erro,
                ), 400

            valor = _valor_do_pedido(evento, slot, quantidade)
            cur.execute(
                """
                INSERT INTO pedido (
                    id_usuario, id_evento, slot, titular_nome, titular_email,
                    rg, whatsapp, valor, quantidade, status_pedido,
                    acompanhante_nome, acompanhante_email, acompanhante_rg, acompanhante_whatsapp
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pendente', %s, %s, %s, %s)
                RETURNING id_pedido
                """,
                (
                    usuario["id_usuario"], evento["id_evento"], slot, titular_nome, titular_email,
                    rg, whatsapp, valor, quantidade,
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
                    "pagamento.html", pedido=pedido, pix_key=_chave_pix_do_pedido(pedido) or "",
                    mp_configurado=False,
                )

            qr_code_base64 = qr_code = mp_erro = None
            segundos_restantes = 0

            if pedido["status_pedido"] == "pendente":
                # Titular deste pedido -- pode ser outra pessoa (slot 2),
                # não necessariamente a conta logada (ver _titular_do_pedido).
                titular = _titular_do_pedido(pedido, usuario["nome"], usuario["email"])

                if _pedido_expirado(pedido):
                    # Já passou da janela de pagamento -- nem tenta criar/
                    # reconsultar um Pix novo pra algo que vai expirar de
                    # qualquer forma; só reconsulta uma última vez (nunca
                    # expira sem checar se não tinha acabado de aprovar) e
                    # resolve o pedido.
                    status_atual = _verificar_com_expiracao(cur, pedido, titular)
                    pedido = dict(pedido)
                    pedido["status_pedido"] = status_atual
                elif not _chave_pix_do_pedido(pedido):
                    logger.warning(
                        "Sem chave Pix cadastrada pro valor %s -- não dá pra gerar o Pix direto",
                        pedido["valor"],
                    )
                    mp_erro = "Não foi possível gerar o Pix agora. Tente novamente em instantes."
                    segundos_restantes = _segundos_restantes(pedido)
                else:
                    # QR/Copia-e-Cola gerado LOCALMENTE (pix_copia_cola.py),
                    # sem chamar a API pra criar nada -- é assim que evita a
                    # taxa do Checkout, ver Horror_Night_Party_Pix_Mercado_
                    # Pago(1).md. Quem confirma o pagamento de verdade é o
                    # Pix da própria chave; a gente só concilia depois (ver
                    # _tentar_conciliar_pix_direto).
                    qr_code = gerar_copia_cola(
                        _chave_pix_do_pedido(pedido), pedido["valor"], txid=f"HNP{id_pedido}"
                    )
                    qr_code_base64 = gerar_qr_base64(qr_code)

                    # Reconsulta/concilia aqui também (não só no botão/
                    # polling) -- sem isso, quem saísse da tela de pagamento
                    # e voltasse depois de 5+ minutos (o polling em JS já
                    # tinha parado) continuava vendo o QR Code de um pedido
                    # que na verdade já tinha sido pago (achado real nesta
                    # revisão, do fluxo antigo -- mesmo risco existe aqui).
                    status_atual = _verificar_e_confirmar_pagamento(cur, pedido, titular)
                    if status_atual == "confirmado":
                        pedido = dict(pedido)
                        pedido["status_pedido"] = "confirmado"
                    else:
                        segundos_restantes = _segundos_restantes(pedido)

    return render_template(
        "pagamento.html",
        pedido=pedido,
        pix_key=_chave_pix_do_pedido(pedido) or "",
        mp_configurado=True,
        qr_code_base64=qr_code_base64,
        qr_code=qr_code,
        mp_erro=mp_erro,
        segundos_restantes=segundos_restantes,
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
            titular = _titular_do_pedido(pedido, usuario["nome"], usuario["email"])
            status_atual = _verificar_com_expiracao(cur, pedido, titular)

    return jsonify({"status": status_atual})


@pedidos_bp.route("/pagamento/webhook/mercadopago", methods=["POST"])
def webhook_mercadopago():
    # Nenhum fluxo do app registra notification_url pra esta rota hoje (o
    # link de cartão é um link estático criado manualmente no Mercado Pago,
    # LINKS_PAGAMENTO_CARTAO; o Pix direto na chave não chama a API pra
    # criar nada, ver pix_copia_cola.py/_tentar_conciliar_pix_direto) -- só
    # dispara se a conta tiver um webhook configurado no painel do Mercado
    # Pago pra TODOS os pagamentos (não confirmado se existe). Deixada
    # funcionando como bônus, sem depender dela: o polling já cobre os dois
    # fluxos sozinho. Sem login, então NUNCA
    # confia em nada que vem no corpo além do id do pagamento; o status
    # real sempre é reconsultado na API deles com nosso próprio access
    # token (_verificar_e_confirmar_pagamento), nunca lido do payload do
    # webhook em si -- um POST forjado nessa rota só desperdiça uma consulta
    # à API, nunca confirma um pedido sem o Mercado Pago ter aprovado de
    # verdade. Responde 200 mesmo quando não há nada a fazer, pra Mercado
    # Pago não ficar reenviando a notificação.
    corpo = request.get_json(silent=True)
    if not isinstance(corpo, dict):
        # corpo pode vir None (JSON ausente/inválido) ou um JSON válido que
        # não é um objeto (lista, número, string, bool...) -- nesses casos
        # `.get("type")` estouraria AttributeError e virava 500 (achado real
        # nesta revisão, reproduzido com corpo `[1,2,3]`/`"x"`/`42`/`true`).
        corpo = {}
    tipo = corpo.get("type") or request.args.get("type") or request.args.get("topic")
    mp_payment_id = None
    if tipo == "payment":
        dados = corpo.get("data")
        if not isinstance(dados, dict):
            # Mesmo risco de `corpo`: um payload tipo
            # `{"type":"payment","data":"x"}` faria `dados.get("id")` estourar.
            dados = {}
        mp_payment_id = str(
            dados.get("id") or request.args.get("data.id") or request.args.get("id") or ""
        ).strip()

    if not mp_payment_id:
        return jsonify({"ok": True}), 200

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.*, u.nome, u.email
                FROM pedido p
                JOIN login_usuario u ON u.id_usuario = p.id_usuario
                WHERE p.mp_payment_id = %s
                """,
                (mp_payment_id,),
            )
            pedido = cur.fetchone()
            if not pedido:
                return jsonify({"ok": True}), 200
            titular = _titular_do_pedido(pedido, pedido["nome"], pedido["email"])
            _verificar_e_confirmar_pagamento(cur, pedido, titular)

    return jsonify({"ok": True}), 200


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
                """
                SELECT p.*, e.numero_lote, e.nome_evento
                FROM pedido p
                JOIN evento e ON e.id_evento = p.id_evento
                WHERE p.id_usuario = %s
                ORDER BY p.data_pedido DESC
                """,
                (usuario["id_usuario"],),
            )
            pedidos = cur.fetchall()

            # Reconcilia AQUI também, não só na tela de pagamento -- achado
            # real do usuário: clicou "Já paguei" antes de pagar de
            # verdade (não confirmou na hora), foi parar em "Meus
            # pedidos", pagou o Pix real uns segundos depois -- mas essa
            # tela só fazia SELECT cru no banco, sem checar nada, então o
            # pedido continuava "pendente" pra sempre, não importa quantas
            # vezes recarregasse ESSA página específica (só /pagamento/
            # <id> reconciliava, e nada te mandava de volta pra lá
            # sozinho). Qualquer pedido pendente do usuário é reconferido
            # antes de renderizar a lista.
            for i, p in enumerate(pedidos):
                if p["status_pedido"] != "pendente":
                    continue
                titular = _titular_do_pedido(p, usuario["nome"], usuario["email"])
                status_atual = _verificar_com_expiracao(cur, p, titular)
                if status_atual != p["status_pedido"]:
                    p = dict(p)
                    p["status_pedido"] = status_atual
                    pedidos[i] = p

            # Mesma regra de "até 2 pedidos por conta" do /comprar: só mostra
            # o link de comprar mais enquanto ainda não bateu esse limite.
            # Com 1 pedido ativo, o próximo é sempre pra OUTRA pessoa (slot
            # 2, 1 ingresso só) -- por isso o teto aqui já reflete isso, não
            # mais quantidade_max_ingressos completo de novo.
            pode_comprar_mais = 0
            evento = _evento_ativo(cur)
            if evento:
                existentes = _pedidos_ativos(cur, usuario["id_usuario"], evento["id_evento"])
                if len(existentes) < 2:
                    vagas_restantes = evento["capacidade_maxima"] - _ingressos_vendidos_total(cur)
                    max_seguinte = evento["quantidade_max_ingressos"] if not existentes else 1
                    pode_comprar_mais = max(0, min(max_seguinte, vagas_restantes))

    return render_template("meus_pedidos.html", pedidos=pedidos, pode_comprar_mais=pode_comprar_mais)
