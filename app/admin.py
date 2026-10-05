"""Área administrativa -- gestão de pedidos, hierarquia, material e eventos
(admin_required em toda rota, decorator já existe desde a Fase 2).

Convenção AJAX (fase "admin sem recarregar página", ver .dev-squad/memory.md):
toda rota de MUTAÇÃO (confirmar/cancelar/apagar/promover/rebaixar/remover e
as variantes em lote) é JSON-only e exige o header `X-Requested-With:
XMLHttpRequest` -- isso serve tanto de discriminador (admin_required precisa
devolver 401 JSON em vez de redirect pra essas rotas) quanto de mitigação de
CSRF (um <form> cross-site não consegue mandar esse header). As rotas GET
que renderizam página continuam HTML normal, sem exigir o header."""
from __future__ import annotations

import csv
import io
import logging
from functools import wraps
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import psycopg2
from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for

import config
import db
from auth import EMAIL_RE, admin_required, current_user
from email_utils import enviar_cadastro_aprovado, enviar_confirmacao_pedido, enviar_email_avulso
from pedidos import _evento_ativo, _validar_rg, _validar_whatsapp

logger = logging.getLogger(__name__)

_TZ_BRASIL = ZoneInfo("America/Sao_Paulo")


def _data_hora_br(valor) -> str:
    # Mesma lógica do filtro Jinja `data_hora_br` (app.py) -- duplicada aqui
    # de propósito, pequena o bastante pra não valer o risco de import
    # circular (app.py já importa admin_bp DESTE módulo).
    if not valor:
        return "--"
    return valor.astimezone(_TZ_BRASIL).strftime("%d/%m/%Y %H:%M")

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# -------------------- envelope JSON + convenção AJAX --------------------

def _ajax() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _json_envelope(payload: dict, status: int = 200):
    resp = jsonify(payload)
    resp.status_code = status
    return resp


def _json_ok(aplicados=None, ignorados=None, totais=None, aviso=None, status: int = 200):
    return _json_envelope(
        {
            "ok": True,
            "aplicados": aplicados or [],
            "ignorados": ignorados or [],
            "totais": totais or {},
            "aviso": aviso,
        },
        status,
    )


def _json_err(erro: str, codigo: str, status: int):
    payload = {"ok": False, "erro": erro, "codigo": codigo}
    if codigo == "auth":
        payload["login_url"] = url_for("auth.login")
    return _json_envelope(payload, status)


def _exigir_ajax(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not _ajax():
            return _json_err(
                "Esta ação só pode ser feita pela interface do admin (com JavaScript habilitado).",
                "regra",
                400,
            )
        return view_func(*args, **kwargs)

    return wrapped


def _ids_do_body() -> list[int] | None:
    corpo = request.get_json(silent=True)
    if not isinstance(corpo, dict):
        # corpo pode vir None (JSON ausente/inválido) ou um JSON válido que
        # não é um objeto (lista, número, string...) -- nesses dois casos
        # `.get("ids")` estouraria AttributeError antes desta checagem.
        return None
    ids = corpo.get("ids")
    if not isinstance(ids, list):
        return None
    try:
        return [int(i) for i in ids]
    except (TypeError, ValueError):
        return None


@admin_bp.after_request
def _reescrever_redirect_login_ajax(response):
    # admin_required faz redirect(302) pro login quando a sessão expirou --
    # um fetch() segue o redirect sozinho e recebe o HTML de login com
    # status 200, que o JS leria como sucesso. Pra uma request AJAX,
    # reescreve pra 401 JSON com login_url; o JS faz a navegação de verdade.
    if _ajax() and response.status_code in (301, 302, 303):
        destino = urlsplit(response.headers.get("Location", "")).path
        if destino and destino.rstrip("/") == url_for("auth.login").rstrip("/"):
            return _json_err("Sessão expirada. Faça login novamente.", "auth", 401)
    return response


@admin_bp.errorhandler(403)
def _erro_403(e):
    if _ajax():
        return _json_err("Você não tem permissão para fazer isso.", "proibido", 403)
    return e


@admin_bp.errorhandler(404)
def _erro_404(e):
    if _ajax():
        return _json_err("Não encontrado.", "nao_encontrado", 404)
    return e


@admin_bp.errorhandler(400)
def _erro_400(e):
    if not _ajax():
        return e
    msg = getattr(e, "description", None) or "Requisição inválida."
    return _json_err(msg, "regra", 400)


@admin_bp.errorhandler(500)
def _erro_500(e):
    if _ajax():
        logger.exception("Erro não tratado numa rota AJAX do admin")
        return _json_err("Erro interno. Tente novamente.", "servidor", 500)
    return e


@admin_bp.context_processor
def _injetar_contadores_nav():
    # Contador de cada item da sidebar sempre visível, não só na página ativa
    # -- antes cada item só mostrava número quando a PRÓPRIA página já tinha
    # carregado aquela lista (pedidos.html tem `pedidos`, cadastros.html tem
    # `pendentes` etc.), então os outros 4 itens ficavam sem pílula (achado
    # real, usuário reportou querendo ver o número mesmo fora da aba). 5
    # COUNT(*) baratos (tabelas pequenas, um evento de festa só) resolvem sem
    # duplicar as queries mais pesadas que cada página já faz pra montar sua
    # própria lista.
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM pedido")
            total_pedidos = cur.fetchone()["n"]
            cur.execute(
                "SELECT COUNT(*) AS n FROM login_usuario "
                "WHERE email_confirmado = FALSE AND email_verificado = TRUE"
            )
            total_cadastros = cur.fetchone()["n"]
            cur.execute(
                "SELECT COUNT(*) AS n FROM login_usuario u "
                "JOIN hierarquia h ON h.id_usuario = u.id_usuario "
                "WHERE u.email_confirmado = TRUE"
            )
            total_hierarquia = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM despesa_material")
            total_material = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM evento")
            total_eventos = cur.fetchone()["n"]
            # Card do evento na sidebar (ver HANDOFF-ADMIN-HEADER-VAGAS.md)
            # aparece em toda página do admin, não só em pedidos -- reaproveita
            # a mesma _totais_pedidos que a rota pedidos já usa, sem query nova.
            totais_pedidos = _totais_pedidos(cur)
    return {
        "nav_counts": {
            "pedidos": total_pedidos,
            "cadastros": total_cadastros,
            "hierarquia": total_hierarquia,
            "material": total_material,
            "eventos": total_eventos,
        },
        "sidebar_vendidos": totais_pedidos["ingressos_vendidos_total"],
        "sidebar_capacidade": totais_pedidos["capacidade_maxima"],
    }


# -------------------- totais recalculados após cada mutação --------------------

def _totais_pedidos(cur) -> dict:
    cur.execute("SELECT capacidade_maxima FROM evento ORDER BY data_cadastro DESC LIMIT 1")
    evento_atual = cur.fetchone()
    capacidade_maxima = evento_atual["capacidade_maxima"] if evento_atual else 60

    cur.execute(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(quantidade) FILTER (WHERE status_pedido != 'cancelado'), 0) AS ingressos_vendidos,
               COALESCE(SUM(valor) FILTER (WHERE status_pedido != 'cancelado'), 0) AS valor_total,
               COUNT(*) FILTER (WHERE status_pedido = 'confirmado') AS confirmados,
               COUNT(*) FILTER (WHERE status_pedido = 'pendente') AS pendentes
        FROM pedido
        """
    )
    agregados = cur.fetchone()
    return {
        "capacidade_maxima": capacidade_maxima,
        "ingressos_vendidos_total": agregados["ingressos_vendidos"],
        "total_pedidos": agregados["total"],
        "valor_total": f"{agregados['valor_total']:.2f}",
        "confirmados": agregados["confirmados"],
        "pendentes": agregados["pendentes"],
    }


def _totais_hierarquia(cur) -> dict:
    cur.execute(
        """
        SELECT h.hierarquia, COUNT(*) AS n
        FROM login_usuario u
        JOIN hierarquia h ON h.id_usuario = u.id_usuario
        WHERE u.email_confirmado = TRUE
        GROUP BY h.hierarquia
        """
    )
    contagens = {r["hierarquia"]: r["n"] for r in cur.fetchall()}
    admins = contagens.get("adm", 0)
    clientes = contagens.get("cliente", 0)
    superadmins = contagens.get("superadm", 0)
    # "hierarquia" (admins + clientes + superadmins) é o mesmo número que a
    # pílula da sidebar mostra (ver admin/_layout.html, nav_counts) --
    # devolvido aqui pra ela também poder ser atualizada ao vivo depois de
    # promover/rebaixar/remover, igual as outras já são via data-admin-total.
    return {
        "admins": admins,
        "clientes": clientes,
        "superadmins": superadmins,
        "hierarquia": admins + clientes + superadmins,
    }


def _totais_cadastros(cur) -> dict:
    cur.execute(
        "SELECT COUNT(*) AS total FROM login_usuario "
        "WHERE email_confirmado = FALSE AND email_verificado = TRUE"
    )
    return {"pendentes": cur.fetchone()["total"]}


def _totais_material(cur) -> dict:
    cur.execute("SELECT COALESCE(SUM(valor), 0) AS total FROM pedido WHERE status_pedido = 'confirmado'")
    total_arrecadado = cur.fetchone()["total"]
    # COUNT(*) junto com o SUM (uma query só) -- "material" é o mesmo
    # número que a pílula da sidebar mostra (nav_counts), devolvido aqui
    # pra ela atualizar ao vivo depois de cadastrar/apagar material.
    cur.execute("SELECT COUNT(*) AS n, COALESCE(SUM(valor), 0) AS total FROM despesa_material")
    linha = cur.fetchone()
    total_gasto = linha["total"]
    saldo = total_arrecadado - total_gasto
    return {
        "total_arrecadado": f"{total_arrecadado:.2f}",
        "total_gasto": f"{total_gasto:.2f}",
        "saldo": f"{saldo:.2f}",
        "material": linha["n"],
    }


# -------------------- páginas --------------------

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
                       p.acompanhante_nome, p.slot, p.cortesia,
                       COALESCE(p.titular_nome, u.nome) AS nome,
                       COALESCE(p.titular_email, u.email) AS email,
                       u.nome AS comprador_nome, u.email AS comprador_email
                FROM pedido p
                JOIN login_usuario u ON u.id_usuario = p.id_usuario
                ORDER BY p.data_pedido DESC
                """
            )
            lista = cur.fetchall()
            totais = _totais_pedidos(cur)

    return render_template(
        "admin/pedidos.html",
        pedidos=lista,
        capacidade_maxima=totais["capacidade_maxima"],
        ingressos_vendidos_total=totais["ingressos_vendidos_total"],
    )


@admin_bp.route("/pedidos/cortesia", methods=["POST"])
@admin_required
def pedidos_cortesia():
    # Ingresso de cortesia -- pedido explícito do usuário. valor=0,
    # status_pedido já nasce 'confirmado' (não passa pelo fluxo de
    # pagamento nenhum). id_usuario fica sendo o ADMIN que criou (o
    # convidado não precisa ter conta própria no sistema) --
    # titular_nome/titular_email/rg/whatsapp são os dados de quem vai usar
    # o ingresso de verdade, mesmo padrão já usado no pedido de slot 2
    # (comprado pra outra pessoa). O índice único de slot ignora cortesia
    # (schema.sql), então o admin pode criar quantos cortesia quiser sem
    # esbarrar no limite de "até 2 pedidos por conta".
    admin = current_user()
    nome = (request.form.get("nome") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    rg = _validar_rg(request.form.get("rg"))
    whatsapp = _validar_whatsapp(request.form.get("whatsapp"))

    if not nome or not EMAIL_RE.match(email) or not rg or not whatsapp:
        flash("Preencha nome, e-mail válido, RG e WhatsApp (só números) do convidado.")
        return redirect(url_for("admin.pedidos"))

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            evento = _evento_ativo(cur)
            if not evento:
                flash("Nenhum evento ativo -- cadastre um lote primeiro.")
                return redirect(url_for("admin.pedidos"))

            cur.execute(
                """
                INSERT INTO pedido (
                    id_usuario, id_evento, slot, titular_nome, titular_email,
                    rg, whatsapp, valor, quantidade, status_pedido, cortesia
                )
                VALUES (%s, %s, 1, %s, %s, %s, %s, 0, 1, 'confirmado', TRUE)
                RETURNING id_pedido
                """,
                (admin["id_usuario"], evento["id_evento"], nome, email, rg, whatsapp),
            )
            id_pedido = cur.fetchone()["id_pedido"]

    try:
        enviar_confirmacao_pedido(email, nome, {"id_pedido": id_pedido, "valor": "0.00", "quantidade": 1})
    except Exception:
        logger.warning("Falha ao enviar e-mail de ingresso cortesia", exc_info=True)

    flash(f"Ingresso cortesia #{id_pedido} criado pra {nome}.")
    return redirect(url_for("admin.pedidos"))


@admin_bp.route("/pedidos/email-massa", methods=["GET", "POST"])
@admin_required
def pedidos_email_massa():
    # E-mail em massa pra todo cliente com pedido ATIVO (não cancelado) --
    # pedido explícito do usuário. Manda pro titular de cada pedido (quem
    # vai usar o ingresso -- titular_email se for pra outra pessoa, senão
    # o e-mail da própria conta), um por endereço ÚNICO (uma pessoa com 2
    # pedidos ativos não recebe 2 e-mails iguais).
    if request.method == "GET":
        return render_template("admin/email_massa.html")

    assunto = (request.form.get("assunto") or "").strip()
    mensagem = (request.form.get("mensagem") or "").strip()
    if not assunto or not mensagem:
        flash("Preencha assunto e mensagem.")
        return render_template("admin/email_massa.html", assunto=assunto, mensagem=mensagem), 400

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (COALESCE(p.titular_email, u.email))
                       COALESCE(p.titular_email, u.email) AS email,
                       COALESCE(p.titular_nome, u.nome) AS nome
                FROM pedido p
                JOIN login_usuario u ON u.id_usuario = p.id_usuario
                WHERE p.status_pedido != 'cancelado'
                """
            )
            destinatarios = cur.fetchall()

    enviados = 0
    falhas = 0
    for d in destinatarios:
        try:
            enviar_email_avulso(d["email"], d["nome"], assunto, mensagem)
            enviados += 1
        except Exception:
            logger.warning("Falha ao enviar e-mail em massa pra %s", d["email"], exc_info=True)
            falhas += 1

    flash(f"E-mail mandado pra {enviados} cliente(s)." + (f" {falhas} falharam." if falhas else ""))
    return redirect(url_for("admin.pedidos"))


@admin_bp.route("/pedidos/exportar")
@admin_required
def pedidos_exportar():
    # Planilha pra portaria -- pedido explícito do usuário. Só pedidos
    # CONFIRMADOS (quem não pagou não deveria entrar). Uma linha por
    # PESSOA, não por pedido -- um pedido de 2 ingressos (titular +
    # acompanhante) vira 2 linhas, senão a portaria só teria o nome de um
    # dos dois pra conferir.
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id_pedido, p.rg, p.acompanhante_nome, p.acompanhante_rg,
                       COALESCE(p.titular_nome, u.nome) AS nome
                FROM pedido p
                JOIN login_usuario u ON u.id_usuario = p.id_usuario
                WHERE p.status_pedido = 'confirmado'
                ORDER BY p.id_pedido
                """
            )
            lista = cur.fetchall()

    # Numeração sequencial (1, 2, 3...) pra portaria conferir/marcar --
    # não é o id_pedido (achado real, usuário corrigiu: "a numeração que
    # digo era um sequencial do lado", não o número interno do pedido).
    buf = io.StringIO()
    escritor = csv.writer(buf, delimiter=";")
    escritor.writerow(["Numeração", "Nome", "RG"])
    numero = 0
    for p in lista:
        numero += 1
        escritor.writerow([numero, p["nome"], p["rg"] or ""])
        if p["acompanhante_nome"]:
            numero += 1
            escritor.writerow([numero, p["acompanhante_nome"], p["acompanhante_rg"] or ""])

    return Response(
        "﻿" + buf.getvalue(),  # BOM -- Excel abre acentuado certo com UTF-8 sem confundir com outra codificação
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=pedidos_portaria.csv"},
    )


@admin_bp.route("/pedidos/lista")
@admin_required
def pedidos_lista():
    # Polling leve (10s, ver admin/_layout.html) pra detectar pedido novo
    # sem recarregar a página inteira -- devolve só os campos que a linha
    # da tabela precisa pra se desenhar, no mesmo formato que data_hora_br
    # já usa no HTML renderizado no servidor.
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id_pedido, p.quantidade, p.valor, p.status_pedido, p.data_pedido,
                       p.acompanhante_nome, p.slot, p.cortesia,
                       COALESCE(p.titular_nome, u.nome) AS nome,
                       COALESCE(p.titular_email, u.email) AS email,
                       u.nome AS comprador_nome, u.email AS comprador_email
                FROM pedido p
                JOIN login_usuario u ON u.id_usuario = p.id_usuario
                ORDER BY p.data_pedido DESC
                """
            )
            lista = cur.fetchall()
            totais = _totais_pedidos(cur)
    return jsonify(
        {
            "itens": [
                {
                    "id": p["id_pedido"],
                    "nome": p["nome"],
                    "email": p["email"],
                    "quantidade": p["quantidade"],
                    "valor": f"{p['valor']:.2f}",
                    "status": p["status_pedido"],
                    "data": _data_hora_br(p["data_pedido"]),
                    "acompanhante_nome": p["acompanhante_nome"],
                    "slot": p["slot"],
                    "comprador_nome": p["comprador_nome"],
                    "cortesia": p["cortesia"],
                }
                for p in lista
            ],
            "totais": totais,
        }
    )


@admin_bp.route("/pedidos/<int:id_pedido>/confirmar", methods=["POST"])
@admin_required
@_exigir_ajax
def pedido_confirmar(id_pedido: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.*,
                       COALESCE(p.titular_nome, u.nome) AS nome,
                       COALESCE(p.titular_email, u.email) AS email
                FROM pedido p
                JOIN login_usuario u ON u.id_usuario = p.id_usuario
                WHERE p.id_pedido = %s
                """,
                (id_pedido,),
            )
            pedido = cur.fetchone()
            if not pedido:
                abort(404)

            # UPDATE com guarda no WHERE + rowcount (não o booleano lido no
            # SELECT acima) -- mesma race que as rotas em lote corrigiam via
            # RETURNING: sem a guarda aqui, dois admins confirmando o mesmo
            # pedido ao mesmo tempo fariam os dois UPDATEs "sem condição"
            # rodarem e os dois mandarem e-mail de confirmação duplicado.
            cur.execute(
                "UPDATE pedido SET status_pedido = 'confirmado' "
                "WHERE id_pedido = %s AND status_pedido != 'confirmado'",
                (id_pedido,),
            )
            mudou = cur.rowcount > 0
            totais = _totais_pedidos(cur)

    if not mudou:
        return _json_ok(
            ignorados=[{"id": id_pedido, "motivo": "Pedido já estava confirmado."}],
            totais=totais,
        )

    aviso = None
    try:
        enviar_confirmacao_pedido(pedido["email"], pedido["nome"], pedido)
    except Exception:
        logger.warning("Falha ao enviar e-mail de confirmação de pedido", exc_info=True)
        aviso = "Pedido confirmado, mas não consegui enviar o e-mail pro cliente. Avise ele manualmente."

    return _json_ok(aplicados=[{"id": id_pedido, "status": "confirmado"}], totais=totais, aviso=aviso)


@admin_bp.route("/pedidos/<int:id_pedido>/cancelar", methods=["POST"])
@admin_required
@_exigir_ajax
def pedido_cancelar(id_pedido: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pedido SET status_pedido = 'cancelado' "
                "WHERE id_pedido = %s AND status_pedido != 'cancelado'",
                (id_pedido,),
            )
            mudou = cur.rowcount > 0
            totais = _totais_pedidos(cur)

    if mudou:
        return _json_ok(aplicados=[{"id": id_pedido, "status": "cancelado"}], totais=totais)
    return _json_ok(
        ignorados=[{"id": id_pedido, "motivo": "Pedido já estava cancelado ou não existe."}],
        totais=totais,
    )


@admin_bp.route("/pedidos/confirmar-lote", methods=["POST"])
@admin_required
@_exigir_ajax
def pedidos_confirmar_lote():
    ids = _ids_do_body()
    if ids is None:
        return _json_err("Lista de ids inválida.", "regra", 400)

    encontrados: dict[int, dict] = {}
    elegiveis: list[dict] = []
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            if ids:
                cur.execute(
                    """
                    SELECT p.*,
                           COALESCE(p.titular_nome, u.nome) AS nome,
                           COALESCE(p.titular_email, u.email) AS email
                    FROM pedido p
                    JOIN login_usuario u ON u.id_usuario = p.id_usuario
                    WHERE p.id_pedido = ANY(%s)
                    """,
                    (ids,),
                )
                encontrados = {p["id_pedido"]: p for p in cur.fetchall()}
                elegiveis = [p for p in encontrados.values() if p["status_pedido"] != "confirmado"]
                if elegiveis:
                    cur.execute(
                        "UPDATE pedido SET status_pedido = 'confirmado' "
                        "WHERE id_pedido = ANY(%s) AND status_pedido != 'confirmado' "
                        "RETURNING id_pedido",
                        ([p["id_pedido"] for p in elegiveis],),
                    )
                    # Confere quem foi REALMENTE atualizado -- não quem tinha
                    # sido considerado elegível no SELECT de alguns instantes
                    # atrás. Sem isso, um pedido cancelado/confirmado por
                    # outro admin bem no meio dessa janela entraria em
                    # "aplicados" e receberia e-mail mesmo sem ter mudado.
                    confirmados_ids = {r["id_pedido"] for r in cur.fetchall()}
                    elegiveis = [p for p in elegiveis if p["id_pedido"] in confirmados_ids]
            totais = _totais_pedidos(cur)

    elegiveis_ids = {p["id_pedido"] for p in elegiveis}
    aplicados, ignorados = [], []
    for id_pedido in ids:
        if id_pedido in elegiveis_ids:
            aplicados.append({"id": id_pedido, "status": "confirmado"})
        elif id_pedido in encontrados:
            ignorados.append({"id": id_pedido, "motivo": "Já estava confirmado."})
        else:
            ignorados.append({"id": id_pedido, "motivo": "Pedido não encontrado."})

    aviso = None
    falhou_email = False
    for pedido in elegiveis:
        try:
            enviar_confirmacao_pedido(pedido["email"], pedido["nome"], pedido)
        except Exception:
            logger.warning("Falha ao enviar e-mail de confirmação de pedido (lote)", exc_info=True)
            falhou_email = True
    if falhou_email:
        aviso = "Pedidos confirmados, mas não consegui enviar e-mail pra algum cliente. Avise manualmente."

    return _json_ok(aplicados=aplicados, ignorados=ignorados, totais=totais, aviso=aviso)


@admin_bp.route("/pedidos/cancelar-lote", methods=["POST"])
@admin_required
@_exigir_ajax
def pedidos_cancelar_lote():
    ids = _ids_do_body()
    if ids is None:
        return _json_err("Lista de ids inválida.", "regra", 400)

    elegiveis_ids: list[int] = []
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            if ids:
                # UPDATE...RETURNING numa tacada só (em vez de SELECT elegíveis
                # + UPDATE em dois passos): elimina a janela em que outro admin
                # poderia mudar o status do pedido entre a checagem e a
                # mutação, o que faria "aplicados" mentir sobre o que de fato
                # aconteceu no banco.
                cur.execute(
                    "UPDATE pedido SET status_pedido = 'cancelado' "
                    "WHERE id_pedido = ANY(%s) AND status_pedido != 'cancelado' "
                    "RETURNING id_pedido",
                    (ids,),
                )
                elegiveis_ids = [r["id_pedido"] for r in cur.fetchall()]
            totais = _totais_pedidos(cur)

    elegiveis_set = set(elegiveis_ids)
    aplicados = [{"id": i, "status": "cancelado"} for i in ids if i in elegiveis_set]
    ignorados = [
        {"id": i, "motivo": "Já estava cancelado ou não existe."} for i in ids if i not in elegiveis_set
    ]
    return _json_ok(aplicados=aplicados, ignorados=ignorados, totais=totais)


@admin_bp.route("/pedidos/<int:id_pedido>/apagar", methods=["POST"])
@admin_required
@_exigir_ajax
def pedido_apagar(id_pedido: int):
    # Só apaga se já estiver cancelado -- evita apagar por engano um pedido
    # pendente/confirmado que ainda representa uma venda de verdade.
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM pedido WHERE id_pedido = %s AND status_pedido = 'cancelado'",
                (id_pedido,),
            )
            apagou = cur.rowcount > 0
            totais = _totais_pedidos(cur)

    if apagou:
        return _json_ok(aplicados=[{"id": id_pedido}], totais=totais)
    return _json_ok(
        ignorados=[{"id": id_pedido, "motivo": "Só é possível apagar pedidos cancelados."}],
        totais=totais,
    )


@admin_bp.route("/pedidos/apagar-lote", methods=["POST"])
@admin_required
@_exigir_ajax
def pedidos_apagar_lote():
    # Existia apagar em lote pra hierarquia/cadastros mas não pra pedidos --
    # o botão "Apagar selecionados" nunca chegou a existir na barra de lote
    # de Pedidos (achado real, usuário reportou: marcava "selecionar todos"
    # e só tinha Confirmar/Cancelar, sem jeito de apagar os cancelados de
    # uma vez). Mesma guarda do single: só apaga quem já está cancelado.
    ids = _ids_do_body()
    if ids is None:
        return _json_err("Lista de ids inválida.", "regra", 400)

    elegiveis_ids: list[int] = []
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            if ids:
                cur.execute(
                    "DELETE FROM pedido WHERE id_pedido = ANY(%s) AND status_pedido = 'cancelado' "
                    "RETURNING id_pedido",
                    (ids,),
                )
                elegiveis_ids = [r["id_pedido"] for r in cur.fetchall()]
            totais = _totais_pedidos(cur)

    elegiveis_set = set(elegiveis_ids)
    aplicados = [{"id": i} for i in ids if i in elegiveis_set]
    ignorados = [
        {"id": i, "motivo": "Só é possível apagar pedidos cancelados."}
        for i in ids
        if i not in elegiveis_set
    ]
    return _json_ok(aplicados=aplicados, ignorados=ignorados, totais=totais)


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
                WHERE u.email_confirmado = TRUE
                ORDER BY u.nome
                """
            )
            usuarios = cur.fetchall()
    return render_template("admin/hierarquia.html", usuarios=usuarios, eu=current_user())


@admin_bp.route("/hierarquia/lista")
@admin_required
def hierarquia_lista():
    # Polling leve (10s) só da lista de CLIENTES -- é onde item "novo"
    # aparece de verdade (um cadastro que acabou de ser confirmado vira
    # cliente aqui). Admins mudam raramente e só por ação manual de outro
    # admin (que já vê o resultado na hora, sem precisar de polling).
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id_usuario, u.nome, u.email
                FROM login_usuario u
                JOIN hierarquia h ON h.id_usuario = u.id_usuario
                WHERE u.email_confirmado = TRUE AND h.hierarquia = 'cliente'
                ORDER BY u.nome
                """
            )
            clientes = cur.fetchall()
            totais = _totais_hierarquia(cur)
    return jsonify(
        {
            "itens": [{"id": c["id_usuario"], "nome": c["nome"], "email": c["email"]} for c in clientes],
            "totais": totais,
        }
    )


@admin_bp.route("/hierarquia/<int:id_usuario>/promover", methods=["POST"])
@admin_required
@_exigir_ajax
def hierarquia_promover(id_usuario: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE hierarquia SET hierarquia = 'adm' WHERE id_usuario = %s AND hierarquia != 'adm'",
                (id_usuario,),
            )
            mudou = cur.rowcount > 0
            totais = _totais_hierarquia(cur)

    if mudou:
        return _json_ok(aplicados=[{"id": id_usuario, "hierarquia": "adm"}], totais=totais)
    return _json_ok(ignorados=[{"id": id_usuario, "motivo": "Já era admin ou não existe."}], totais=totais)


@admin_bp.route("/hierarquia/<int:id_usuario>/rebaixar", methods=["POST"])
@admin_required
@_exigir_ajax
def hierarquia_rebaixar(id_usuario: int):
    if id_usuario == session.get("user_id"):
        return _json_err("Não é possível rebaixar a si mesmo.", "regra", 400)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            # SUPERADM nunca pode ser rebaixado, nem por outro admin -- essa
            # checagem tem que ficar aqui (não só escondendo o botão no
            # template), senão dá pra chamar a rota direto via fetch.
            cur.execute("SELECT hierarquia FROM hierarquia WHERE id_usuario = %s", (id_usuario,))
            atual = cur.fetchone()
            if atual and atual["hierarquia"] == "superadm":
                return _json_err("O SUPERADM não pode ser rebaixado.", "regra", 403)
            cur.execute(
                "UPDATE hierarquia SET hierarquia = 'cliente' WHERE id_usuario = %s AND hierarquia != 'cliente'",
                (id_usuario,),
            )
            mudou = cur.rowcount > 0
            totais = _totais_hierarquia(cur)

    if mudou:
        return _json_ok(aplicados=[{"id": id_usuario, "hierarquia": "cliente"}], totais=totais)
    return _json_ok(ignorados=[{"id": id_usuario, "motivo": "Já era cliente ou não existe."}], totais=totais)


@admin_bp.route("/hierarquia/<int:id_usuario>/remover", methods=["POST"])
@admin_required
@_exigir_ajax
def hierarquia_remover(id_usuario: int):
    if id_usuario == session.get("user_id"):
        return _json_err("Não é possível apagar a si mesmo.", "regra", 400)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM login_usuario WHERE id_usuario = %s AND id_usuario IN "
                "(SELECT id_usuario FROM hierarquia WHERE hierarquia = 'cliente')",
                (id_usuario,),
            )
            apagou = cur.rowcount > 0
            totais = _totais_hierarquia(cur)

    if apagou:
        return _json_ok(aplicados=[{"id": id_usuario}], totais=totais)
    return _json_ok(ignorados=[{"id": id_usuario, "motivo": "Só é possível apagar clientes."}], totais=totais)


@admin_bp.route("/hierarquia/promover-lote", methods=["POST"])
@admin_required
@_exigir_ajax
def hierarquia_promover_lote():
    ids = _ids_do_body()
    if ids is None:
        return _json_err("Lista de ids inválida.", "regra", 400)

    elegiveis_ids: list[int] = []
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            if ids:
                # UPDATE...RETURNING direto (sem SELECT elegíveis + UPDATE em
                # dois passos): fecha a janela em que outro admin poderia
                # promover a mesma pessoa entre a checagem e a mutação.
                cur.execute(
                    "UPDATE hierarquia SET hierarquia = 'adm' "
                    "WHERE id_usuario = ANY(%s) AND hierarquia != 'adm' "
                    "RETURNING id_usuario",
                    (ids,),
                )
                elegiveis_ids = [r["id_usuario"] for r in cur.fetchall()]
            totais = _totais_hierarquia(cur)

    elegiveis_set = set(elegiveis_ids)
    aplicados = [{"id": i, "hierarquia": "adm"} for i in ids if i in elegiveis_set]
    ignorados = [{"id": i, "motivo": "Já era admin ou não existe."} for i in ids if i not in elegiveis_set]
    return _json_ok(aplicados=aplicados, ignorados=ignorados, totais=totais)


@admin_bp.route("/hierarquia/remover-lote", methods=["POST"])
@admin_required
@_exigir_ajax
def hierarquia_remover_lote():
    ids = _ids_do_body()
    if ids is None:
        return _json_err("Lista de ids inválida.", "regra", 400)

    meu_id = session.get("user_id")
    ignorados = [{"id": i, "motivo": "Não é possível apagar a si mesmo."} for i in ids if i == meu_id]
    candidatos = [i for i in ids if i != meu_id]

    elegiveis_ids: list[int] = []
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            if candidatos:
                # DELETE...RETURNING direto: a checagem "hierarquia = cliente"
                # já vinha embutida no próprio DELETE, mas "aplicados" antes
                # era montado a partir de um SELECT feito ANTES do DELETE --
                # se alguém promovesse um desses ids a admin bem nessa janela,
                # o DELETE (corretamente) não apagava, mas a resposta ainda
                # dizia "apagado". Usar as linhas que o próprio DELETE
                # devolve garante que "aplicados" reflete o que aconteceu de
                # verdade no banco.
                cur.execute(
                    "DELETE FROM login_usuario WHERE id_usuario = ANY(%s) AND id_usuario IN "
                    "(SELECT id_usuario FROM hierarquia WHERE hierarquia = 'cliente') "
                    "RETURNING id_usuario",
                    (candidatos,),
                )
                elegiveis_ids = [r["id_usuario"] for r in cur.fetchall()]
            totais = _totais_hierarquia(cur)

    elegiveis_set = set(elegiveis_ids)
    aplicados = [{"id": i} for i in candidatos if i in elegiveis_set]
    ignorados += [{"id": i, "motivo": "Só é possível apagar clientes."} for i in candidatos if i not in elegiveis_set]
    return _json_ok(aplicados=aplicados, ignorados=ignorados, totais=totais)


@admin_bp.route("/cadastros")
@admin_required
def cadastros():
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_usuario, nome, email, data_cadastro FROM login_usuario "
                "WHERE email_confirmado = FALSE AND email_verificado = TRUE ORDER BY data_cadastro"
            )
            pendentes = cur.fetchall()
    return render_template("admin/cadastros.html", pendentes=pendentes)


@admin_bp.route("/cadastros/lista")
@admin_required
def cadastros_lista():
    # Polling leve (10s) pra detectar cadastro novo sem recarregar a página.
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_usuario, nome, email, data_cadastro FROM login_usuario "
                "WHERE email_confirmado = FALSE AND email_verificado = TRUE ORDER BY data_cadastro"
            )
            pendentes = cur.fetchall()
            totais = _totais_cadastros(cur)
    return jsonify(
        {
            "itens": [
                {
                    "id": p["id_usuario"],
                    "nome": p["nome"],
                    "email": p["email"],
                    "data": _data_hora_br(p["data_cadastro"]),
                }
                for p in pendentes
            ],
            "totais": totais,
        }
    )


@admin_bp.route("/cadastros/<int:id_usuario>/confirmar", methods=["POST"])
@admin_required
@_exigir_ajax
def cadastro_confirmar(id_usuario: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            # UPDATE...RETURNING direto (em vez de SELECT + UPDATE em dois
            # passos): fecha a mesma race que as rotas em lote corrigiam --
            # sem a guarda "email_confirmado = FALSE" no WHERE, dois admins
            # confirmando o mesmo cadastro ao mesmo tempo mandariam os dois
            # e-mail de "cadastro aprovado" duplicado.
            cur.execute(
                "UPDATE login_usuario SET email_confirmado = TRUE, data_atualizacao = now() "
                "WHERE id_usuario = %s AND email_confirmado = FALSE "
                "RETURNING nome, email",
                (id_usuario,),
            )
            usuario = cur.fetchone()
            totais = _totais_cadastros(cur)

    if not usuario:
        return _json_ok(
            ignorados=[{"id": id_usuario, "motivo": "Cadastro não encontrado ou já confirmado."}],
            totais=totais,
        )

    aviso = None
    try:
        enviar_cadastro_aprovado(usuario["email"], usuario["nome"])
    except Exception:
        logger.warning("Falha ao enviar e-mail de cadastro aprovado", exc_info=True)
        aviso = "Cadastro confirmado, mas não consegui enviar o e-mail pro cliente. Avise ele manualmente."

    return _json_ok(aplicados=[{"id": id_usuario}], totais=totais, aviso=aviso)


@admin_bp.route("/cadastros/<int:id_usuario>/recusar", methods=["POST"])
@admin_required
@_exigir_ajax
def cadastro_recusar(id_usuario: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM login_usuario WHERE id_usuario = %s AND email_confirmado = FALSE",
                (id_usuario,),
            )
            apagou = cur.rowcount > 0
            totais = _totais_cadastros(cur)

    if apagou:
        return _json_ok(aplicados=[{"id": id_usuario}], totais=totais)
    return _json_ok(
        ignorados=[{"id": id_usuario, "motivo": "Cadastro não encontrado ou já confirmado."}],
        totais=totais,
    )


@admin_bp.route("/cadastros/confirmar-lote", methods=["POST"])
@admin_required
@_exigir_ajax
def cadastros_confirmar_lote():
    ids = _ids_do_body()
    if ids is None:
        return _json_err("Lista de ids inválida.", "regra", 400)

    elegiveis: list[dict] = []
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            if ids:
                # UPDATE...RETURNING direto (dá pra buscar nome/email da
                # própria linha atualizada, sem SELECT em separado): fecha a
                # janela em que outro admin poderia confirmar o mesmo
                # cadastro entre a checagem e a mutação.
                cur.execute(
                    "UPDATE login_usuario SET email_confirmado = TRUE, data_atualizacao = now() "
                    "WHERE id_usuario = ANY(%s) AND email_confirmado = FALSE "
                    "RETURNING id_usuario, nome, email",
                    (ids,),
                )
                elegiveis = cur.fetchall()
            totais = _totais_cadastros(cur)

    elegiveis_set = {u["id_usuario"] for u in elegiveis}
    aplicados = [{"id": i} for i in ids if i in elegiveis_set]
    ignorados = [{"id": i, "motivo": "Não encontrado ou já confirmado."} for i in ids if i not in elegiveis_set]

    aviso = None
    falhou_email = False
    for usuario in elegiveis:
        try:
            enviar_cadastro_aprovado(usuario["email"], usuario["nome"])
        except Exception:
            logger.warning("Falha ao enviar e-mail de cadastro aprovado (lote)", exc_info=True)
            falhou_email = True
    if falhou_email:
        aviso = "Cadastros confirmados, mas não consegui enviar e-mail pra algum cliente. Avise manualmente."

    return _json_ok(aplicados=aplicados, ignorados=ignorados, totais=totais, aviso=aviso)


@admin_bp.route("/cadastros/recusar-lote", methods=["POST"])
@admin_required
@_exigir_ajax
def cadastros_recusar_lote():
    ids = _ids_do_body()
    if ids is None:
        return _json_err("Lista de ids inválida.", "regra", 400)

    elegiveis_ids: list[int] = []
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            if ids:
                # DELETE...RETURNING direto: fecha a janela em que outro
                # admin poderia confirmar o cadastro entre a checagem e a
                # mutação (o que faria "aplicados" mentir sobre o apagado).
                cur.execute(
                    "DELETE FROM login_usuario WHERE id_usuario = ANY(%s) AND email_confirmado = FALSE "
                    "RETURNING id_usuario",
                    (ids,),
                )
                elegiveis_ids = [r["id_usuario"] for r in cur.fetchall()]
            totais = _totais_cadastros(cur)

    elegiveis_set = set(elegiveis_ids)
    aplicados = [{"id": i} for i in ids if i in elegiveis_set]
    ignorados = [{"id": i, "motivo": "Não encontrado ou já confirmado."} for i in ids if i not in elegiveis_set]
    return _json_ok(aplicados=aplicados, ignorados=ignorados, totais=totais)


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
            totais = _totais_material(cur)

    return render_template(
        "admin/material.html",
        materiais=materiais,
        total_arrecadado=totais["total_arrecadado"],
        total_gasto=totais["total_gasto"],
        saldo=totais["saldo"],
    )


@admin_bp.route("/material/lista")
@admin_required
def material_lista():
    # Polling leve (30s -- material muda bem menos que pedido/cadastro,
    # pedido explícito do usuário) pra detectar material novo sem
    # recarregar a página.
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
            totais = _totais_material(cur)
    return jsonify(
        {
            "itens": [
                {
                    "id": m["id"],
                    "nome": m["nome_material"],
                    "descricao": m["descricao"] or "—",
                    "valor": f"{m['valor']:.2f}",
                    "data": m["data_compra"].strftime("%d/%m/%Y"),
                    "cadastrado_por": m["cadastrado_por"] or "--",
                }
                for m in materiais
            ],
            "totais": totais,
        }
    )


@admin_bp.route("/material/<int:id_material>/apagar", methods=["POST"])
@admin_required
@_exigir_ajax
def material_apagar(id_material: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM despesa_material WHERE id = %s", (id_material,))
            apagou = cur.rowcount > 0
            totais = _totais_material(cur)

    if apagou:
        return _json_ok(aplicados=[{"id": id_material}], totais=totais)
    return _json_ok(ignorados=[{"id": id_material, "motivo": "Material não encontrado."}], totais=totais)


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
        endereco = ultimo["endereco"] if ultimo else None
        bairro = ultimo["bairro"] if ultimo else None
        capacidade_maxima = ultimo["capacidade_maxima"] if ultimo else 60
        numero_lote = (ultimo["numero_lote"] + 1) if ultimo else 1
        promo_casadinha = bool(ultimo["promo_casadinha"]) if ultimo else False

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO evento
                        (nome_evento, data_evento, hora_evento, endereco, bairro, numero_lote,
                         valor_entrada, quantidade_max_ingressos, capacidade_maxima, usuario_cadastro,
                         promo_casadinha)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        nome_evento,
                        data_evento,
                        hora_evento,
                        endereco,
                        bairro,
                        numero_lote,
                        valor_entrada,
                        quantidade_max_ingressos,
                        capacidade_maxima,
                        current_user()["id_usuario"],
                        promo_casadinha,
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
    endereco = ultimo["endereco"] if ultimo else ""
    bairro = ultimo["bairro"] if ultimo else ""
    return render_template(
        "admin/eventos.html",
        eventos=lista,
        ultimo=ultimo,
        proximo_lote=proximo_lote,
        capacidade_maxima=capacidade_maxima,
        endereco=endereco,
        bairro=bairro,
        ingressos_vendidos_total=ingressos_vendidos_total,
        promo_casadinha_valor=int(config.PROMO_CASADINHA_VALOR),
        promo_casadinha_pix_ok=bool(config.PIX_KEYS.get(config.PROMO_CASADINHA_VALOR)),
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


@admin_bp.route("/eventos/nome-data", methods=["POST"])
@admin_required
def eventos_nome_data():
    # Mesmo padrão de eventos_capacidade/eventos_endereco: só atualiza o
    # lote mais recente (o "ativo") -- nome/data/hora são propriedade da
    # FESTA, não do lote em si, e a tela pública (convite.html) só lê o
    # evento mais recente mesmo.
    nome_evento = (request.form.get("nome_evento") or "").strip()
    data_evento = request.form.get("data_evento") or None
    hora_evento = request.form.get("hora_evento") or None

    if not nome_evento:
        flash("O nome do evento não pode ficar em branco.")
        return redirect(url_for("admin.eventos"))

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_evento FROM evento ORDER BY data_cadastro DESC LIMIT 1")
            ultimo = cur.fetchone()
            if ultimo:
                cur.execute(
                    "UPDATE evento SET nome_evento = %s, data_evento = %s, hora_evento = %s WHERE id_evento = %s",
                    (nome_evento, data_evento, hora_evento, ultimo["id_evento"]),
                )
    return redirect(url_for("admin.eventos"))


@admin_bp.route("/eventos/casadinha", methods=["POST"])
@admin_required
def eventos_casadinha():
    # Liga/desliga a promoção casadinha no lote ativo (o mais recente, mesmo
    # padrão de eventos_capacidade). Pedidos já criados guardam o próprio
    # valor, então desligar não mexe em quem já está pagando.
    ligar = request.form.get("ativo") == "1"
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_evento FROM evento ORDER BY data_cadastro DESC LIMIT 1")
            ultimo = cur.fetchone()
            if ultimo:
                cur.execute(
                    "UPDATE evento SET promo_casadinha = %s WHERE id_evento = %s",
                    (ligar, ultimo["id_evento"]),
                )
    flash("Promoção casadinha ligada." if ligar else "Promoção casadinha desligada.")
    return redirect(url_for("admin.eventos"))


@admin_bp.route("/eventos/endereco", methods=["POST"])
@admin_required
def eventos_endereco():
    # Mesmo padrão de eventos_capacidade: só atualiza o lote mais recente
    # (o "ativo" -- ver comentário em eventos_apagar), porque endereco/
    # bairro são propriedade da FESTA, não do lote em si -- lotes antigos
    # (encerrados) não precisam refletir a mudança, e a tela pública
    # (convite.html) só lê o evento mais recente mesmo.
    endereco = (request.form.get("endereco") or "").strip()
    bairro = (request.form.get("bairro") or "").strip()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_evento FROM evento ORDER BY data_cadastro DESC LIMIT 1")
            ultimo = cur.fetchone()
            if ultimo:
                cur.execute(
                    "UPDATE evento SET endereco = %s, bairro = %s WHERE id_evento = %s",
                    (endereco, bairro, ultimo["id_evento"]),
                )
    return redirect(url_for("admin.eventos"))


@admin_bp.route("/eventos/<int:id_evento>/apagar", methods=["POST"])
@admin_required
@_exigir_ajax
def eventos_apagar(id_evento: int):
    # "Ativo" (admin/eventos.html) é sempre o lote mais recente
    # (ORDER BY data_cadastro DESC, loop.first) -- por isso apagar o lote
    # ativo já faz o anterior virar o novo ativo sozinho, sem nenhuma lógica
    # extra aqui: é só o próximo item da mesma lista ordenada (achado real,
    # pedido do usuário: "apagar o lote, aí volta pro anterior"). Bloqueado
    # se já existir pedido pra esse lote -- apagar destruiria uma venda de
    # verdade (violação de FK vira aviso, não erro cru).
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("DELETE FROM evento WHERE id_evento = %s", (id_evento,))
                apagou = cur.rowcount > 0
            except psycopg2.errors.ForeignKeyViolation:
                # Precisa dar rollback ANTES de sair do `with db.get_conn()`
                # -- sem isso, o commit automático dele na saída limpa
                # estoura "current transaction is aborted" (a transação já
                # tinha sido abortada pelo próprio erro de FK, e Postgres
                # rejeita qualquer comando, inclusive commit, até um
                # rollback explícito), substituindo esse _json_err por um
                # 500 genérico em vez da mensagem amigável.
                conn.rollback()
                return _json_err(
                    "Esse lote já tem pedido vinculado -- não dá pra apagar.",
                    "regra",
                    400,
                )

    if apagou:
        return _json_ok(aplicados=[{"id": id_evento}])
    return _json_ok(ignorados=[{"id": id_evento, "motivo": "Lote não encontrado."}])
