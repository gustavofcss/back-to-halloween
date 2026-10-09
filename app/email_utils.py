"""Envio de e-mail transacional via API do Brevo (HTTPS).

Antes usava smtplib puro, mas o Render bloqueia conexão SMTP de saída --
a chamada em smtp.gmail.com:587 ficava pendurada até o worker do gunicorn
matar a requisição (WORKER TIMEOUT), derrubando a página no meio de uma
confirmação de cadastro/pedido. Tentamos Resend depois, mas o remetente de
testes deles (onboarding@resend.dev) só manda pra dono da conta -- qualquer
cliente real tomava 422 (achado real: confirmar pedido #5 quebrou assim).
Brevo verifica só o e-mail remetente (link, sem DNS/domínio) e manda pra
qualquer destinatário de verdade.
"""
from __future__ import annotations

import html
import logging
import threading

import requests

import config
import db

logger = logging.getLogger(__name__)

_BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def _enviar(destinatario: str, assunto: str, corpo: str, corpo_html: str | None = None) -> None:
    if not config.BREVO_API_KEY:
        logger.error(
            "BREVO_API_KEY não configurada, e-mail '%s' NÃO foi enviado -- "
            "configure BREVO_API_KEY (Environment do Render / .env)",
            assunto,
        )
        return

    payload = {
        "sender": {"name": config.BREVO_FROM_NOME, "email": config.BREVO_FROM_EMAIL},
        "to": [{"email": destinatario}],
        "subject": assunto,
        "textContent": corpo,
        "htmlContent": corpo_html or f"<pre style=\"font-family:inherit\">{corpo}</pre>",
    }

    resposta = requests.post(
        _BREVO_URL,
        headers={
            "api-key": config.BREVO_API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=payload,
        timeout=10,
    )
    if not resposta.ok:
        # O corpo da resposta do Brevo diz o MOTIVO (remetente não verificado,
        # chave inválida, limite diário...) -- raise_for_status sozinho só dava
        # "400 Client Error" e a causa ficava invisível no log.
        logger.error(
            "Brevo recusou o e-mail '%s' (HTTP %s): %s",
            assunto, resposta.status_code, resposta.text[:300],
        )
    resposta.raise_for_status()


# ---- Visual dos e-mails: tema VHS do site (preto, vermelho, fonte de fita) ----
# Cliente de e-mail não carrega .css externo nem @font-face, então tudo é
# estilo inline com fonte segura (Courier New) e cor em bgcolor + style.
_FUNDO = "#0b0608"
_CARD = "#150d11"
_LINHA = "#3a2a2f"
_TEXTO = "#ece3d0"
_TEXTO_SUAVE = "#a89a86"
_VERMELHO = "#ff2d45"
_MONO = "'Courier New',Courier,monospace"


def _esc(valor) -> str:
    # Nome, RG e a mensagem do admin entram no HTML -- escapa pra nada digitado
    # virar marcação (e pra não quebrar o layout com um "<" no nome).
    return html.escape(str(valor), quote=True)


def _email_shell(corpo_interno: str) -> str:
    # Gmail (e outros webmails) aplicam modo escuro automático no HTML do
    # e-mail e "corrigem" cores explícitas. O layout é escuro, e o meta
    # "light only" ("dark only" não existe pro Gmail) pede pra respeitar as cores exatas, e cada célula leva bgcolor +
    # !important pra não ser invertida (achado real, print do Gmail no celular).
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
</head>
<body bgcolor="{_FUNDO}" style="margin:0;padding:0;background-color:{_FUNDO} !important;background-image:linear-gradient({_FUNDO},{_FUNDO});">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{_FUNDO}" style="background-color:{_FUNDO} !important;background-image:linear-gradient({_FUNDO},{_FUNDO});">
  <tr><td align="center" style="padding:24px 12px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;">
      <tr><td bgcolor="{_FUNDO}" style="background-color:{_FUNDO} !important;background-image:linear-gradient({_FUNDO},{_FUNDO});padding:0 4px 14px;font-family:{_MONO};font-size:15px;letter-spacing:3px;text-transform:uppercase;color:{_TEXTO} !important;">
        <span style="color:{_VERMELHO} !important;">&#9679;</span>&nbsp;REC
        <span style="float:right;color:{_TEXTO_SUAVE} !important;">&#9654; PLAY</span>
      </td></tr>
      <tr><td bgcolor="{_CARD}" style="background-color:{_CARD} !important;background-image:linear-gradient({_CARD},{_CARD});border:1px solid {_LINHA};border-top:3px solid {_VERMELHO};padding:28px 26px;font-family:{_MONO};font-size:15px;line-height:1.65;color:{_TEXTO} !important;">
        <p style="margin:0 0 18px;font-size:22px;letter-spacing:3px;text-transform:uppercase;color:{_TEXTO} !important;">Back to Halloween</p>
        {corpo_interno}
      </td></tr>
      <tr><td bgcolor="{_FUNDO}" style="background-color:{_FUNDO} !important;background-image:linear-gradient({_FUNDO},{_FUNDO});padding:14px 4px 0;font-family:{_MONO};font-size:12px;letter-spacing:2px;text-transform:uppercase;color:{_TEXTO_SUAVE} !important;">
        OUT. 31 &middot; FIM DA FITA
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


def _p(texto: str) -> str:
    # `texto` já vem escapado/montado por quem chama.
    return f'<p style="margin:0 0 14px;color:{_TEXTO} !important;">{texto}</p>'


def _botao(rotulo: str, url: str) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0 18px;"><tr>'
        f'<td bgcolor="{_VERMELHO}" style="background-color:{_VERMELHO} !important;background-image:linear-gradient({_VERMELHO},{_VERMELHO});padding:12px 22px;">'
        f'<a href="{_esc(url)}" style="font-family:{_MONO};font-size:15px;letter-spacing:3px;'
        f'text-transform:uppercase;text-decoration:none;color:#ffffff !important;">&#9654; {_esc(rotulo)}</a>'
        f'</td></tr></table>'
    )


def _codigo_html(codigo: str) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0 18px;"><tr>'
        f'<td bgcolor="#000000" style="background-color:#000000 !important;background-image:linear-gradient(#000000,#000000);border:1px solid {_VERMELHO};'
        f'padding:14px 26px;font-family:{_MONO};font-size:32px;letter-spacing:10px;'
        f'color:{_VERMELHO} !important;">{_esc(codigo)}</td></tr></table>'
    )


def _ticket_html(pedido, nome_titular: str) -> str:
    # Mesma ideia do "ingresso" da tela de pagamento (LADO A · INGRESSO), com
    # estilo inline.
    titulares_html = (
        f'<p style="margin:0;font-size:15px;color:{_TEXTO} !important;">{_esc(nome_titular)} '
        f'<span style="font-size:11px;letter-spacing:1px;text-transform:uppercase;color:{_TEXTO_SUAVE} !important;">(ingresso 1)</span></p>'
        + (
            f'<p style="margin:2px 0 0;font-size:12px;color:{_TEXTO_SUAVE} !important;">RG {_esc(pedido["rg"])}</p>'
            if pedido.get("rg")
            else ""
        )
    )
    if pedido.get("acompanhante_nome"):
        titulares_html += (
            f'<p style="margin:12px 0 0;font-size:15px;color:{_TEXTO} !important;">{_esc(pedido["acompanhante_nome"])} '
            f'<span style="font-size:11px;letter-spacing:1px;text-transform:uppercase;color:{_TEXTO_SUAVE} !important;">(ingresso 2)</span></p>'
            + (
                f'<p style="margin:2px 0 0;font-size:12px;color:{_TEXTO_SUAVE} !important;">RG {_esc(pedido["acompanhante_rg"])}</p>'
                if pedido.get("acompanhante_rg")
                else ""
            )
        )

    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:8px 0 18px;border:1px solid {_VERMELHO};">
      <tr><td bgcolor="{_VERMELHO}" style="background-color:{_VERMELHO} !important;background-image:linear-gradient({_VERMELHO},{_VERMELHO});padding:8px 18px;font-family:{_MONO};font-size:12px;letter-spacing:3px;text-transform:uppercase;color:#ffffff !important;">
        LADO A &middot; INGRESSO
      </td></tr>
      <tr><td bgcolor="#000000" style="background-color:#000000 !important;background-image:linear-gradient(#000000,#000000);padding:18px 18px 14px;font-family:{_MONO};">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
          <td style="font-family:{_MONO};">
            <p style="margin:0 0 4px;font-size:20px;color:{_TEXTO} !important;">{pedido['quantidade']} &times; Entrada</p>
            <p style="margin:0;font-size:12px;color:{_TEXTO_SUAVE} !important;">Pedido #{pedido['id_pedido']}</p>
          </td>
          <td align="right" valign="top" style="font-family:{_MONO};font-size:26px;color:{_VERMELHO} !important;">R$&nbsp;{pedido['valor']}</td>
        </tr></table>
      </td></tr>
      <tr><td bgcolor="#000000" style="background-color:#000000 !important;background-image:linear-gradient(#000000,#000000);padding:0 18px 16px;font-family:{_MONO};">
        {titulares_html}
      </td></tr>
      <tr><td bgcolor="#000000" style="background-color:#000000 !important;background-image:linear-gradient(#000000,#000000);padding:0 18px;"><div style="border-top:2px dashed {_LINHA};"></div></td></tr>
      <tr><td bgcolor="#000000" style="background-color:#000000 !important;background-image:linear-gradient(#000000,#000000);padding:14px 18px 18px;font-family:{_MONO};font-size:13px;line-height:1.6;color:{_TEXTO_SUAVE} !important;">
        Apresente este pedido (#{pedido['id_pedido']}) na entrada da festa.
      </td></tr>
    </table>
    """


def enviar_cadastro_aprovado(destinatario: str, nome: str) -> None:
    url = f"{config.BASE_URL}/login"
    corpo = (
        f"▶ Oi, {nome}!\n\n"
        "Seu cadastro no Back to Halloween foi confirmado pelo administrador. "
        f"Já pode entrar por aqui: {url}\n\n"
        "Até a noite mais assustadora do ano!"
    )
    corpo_html = _email_shell(
        _p(f"Oi, {_esc(nome)}!")
        + _p("Seu cadastro no Back to Halloween foi confirmado pelo administrador. Já pode entrar e garantir seu ingresso.")
        + _botao("Entrar", url)
        + _p("Até a noite mais assustadora do ano!")
    )
    _enviar(destinatario, "Cadastro confirmado — Back to Halloween", corpo, corpo_html)


def enviar_confirmacao_pedido(destinatario: str, nome: str, pedido) -> None:
    abertura = (
        "Seu ingresso de cortesia está garantido!"
        if pedido.get("cortesia")
        else "Seu pagamento foi confirmado!"
    )
    corpo = (
        f"▶ Oi, {nome}!\n\n"
        f"{abertura} O pedido a seguir já está garantido pro "
        "Back to Halloween:\n\n"
        f"Pedido #{pedido['id_pedido']} — {pedido['quantidade']} ingresso(s) — "
        f"R$ {pedido['valor']}\n\n"
        + (
            f"Ingresso 2 pra {pedido['acompanhante_nome']}.\n\n"
            if pedido.get("acompanhante_nome")
            else ""
        )
        + "Nos vemos na noite mais assustadora do ano!"
    )
    corpo_html = _email_shell(
        _p(f"Oi, {_esc(nome)}!")
        + _p(f"{abertura} Seu ingresso pro Back to Halloween:")
        + _ticket_html(pedido, nome)
        + _p("Nos vemos na noite mais assustadora do ano!")
    )
    _enviar(
        destinatario,
        "Seu ingresso pro Back to Halloween está confirmado!",
        corpo,
        corpo_html,
    )


def enviar_email_avulso(destinatario: str, nome: str, assunto: str, mensagem: str) -> None:
    # E-mail em massa do admin (app/admin.py, pedidos_email_massa) -- assunto
    # e mensagem digitados na hora pelo admin, um por cliente com pedido
    # ativo. `mensagem` é texto puro (sem HTML) -- o admin não escreve
    # marcação, só o texto mesmo; quebras de linha viram parágrafos no HTML
    # pra não sair tudo grudado num bloco só.
    corpo = f"▶ Oi, {nome}!\n\n{mensagem}"
    paragrafos_html = "".join(_p(_esc(linha)) for linha in mensagem.split("\n") if linha.strip())
    corpo_html = _email_shell(_p(f"Oi, {_esc(nome)}!") + paragrafos_html)
    _enviar(destinatario, assunto, corpo, corpo_html)


def enviar_codigo_verificacao_email(destinatario: str, nome: str, codigo: str) -> None:
    corpo = (
        f"▶ Oi, {nome}!\n\n"
        "Falta só confirmar que esse e-mail é seu pra continuar seu cadastro no "
        f"Back to Halloween. Use o código abaixo (válido por 15 minutos):\n\n"
        f"{codigo}\n\n"
        "Se você não pediu esse cadastro, pode ignorar este e-mail."
    )
    corpo_html = _email_shell(
        _p(f"Oi, {_esc(nome)}!")
        + _p("Falta só confirmar que esse e-mail é seu pra continuar seu cadastro. Use o código abaixo (válido por 15 minutos):")
        + _codigo_html(codigo)
        + _p("Se você não pediu esse cadastro, pode ignorar este e-mail.")
    )
    _enviar(destinatario, "Confirme seu e-mail — Back to Halloween", corpo, corpo_html)


def enviar_codigo_recuperacao(destinatario: str, nome: str, codigo: str) -> None:
    corpo = (
        f"▶ Oi, {nome}!\n\n"
        "Recebemos um pedido pra redefinir sua senha do Back to Halloween. "
        f"Use o código abaixo (válido por 15 minutos):\n\n"
        f"{codigo}\n\n"
        "Se você não pediu isso, pode ignorar este e-mail — sua senha continua a mesma."
    )
    corpo_html = _email_shell(
        _p(f"Oi, {_esc(nome)}!")
        + _p("Recebemos um pedido pra redefinir sua senha. Use o código abaixo (válido por 15 minutos):")
        + _codigo_html(codigo)
        + _p("Se você não pediu isso, pode ignorar este e-mail — sua senha continua a mesma.")
    )
    _enviar(destinatario, "Código para redefinir sua senha — Back to Halloween", corpo, corpo_html)


# ---- Avisos pros administradores ----
def _destinatarios_admin() -> list[dict]:
    """Admins e superadmins já aprovados, um por endereço de e-mail."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (u.email) u.email, u.nome
                FROM login_usuario u
                JOIN hierarquia h ON h.id_usuario = u.id_usuario
                WHERE h.hierarquia IN ('adm', 'superadm') AND u.email_confirmado = TRUE
                ORDER BY u.email
                """
            )
            return cur.fetchall()


def _avisar_admins(assunto: str, corpo: str, corpo_html: str) -> None:
    try:
        admins = _destinatarios_admin()
    except Exception:
        logger.warning("Falha ao listar os admins que recebem o aviso", exc_info=True)
        return
    for admin in admins:
        try:
            _enviar(admin["email"], assunto, corpo, corpo_html)
        except Exception:
            logger.warning("Falha ao avisar o admin %s", admin["email"], exc_info=True)


def _avisar_admins_em_segundo_plano(assunto: str, corpo: str, corpo_html: str) -> None:
    # Thread à parte: o Brevo leva até 10s por e-mail, e quem está cadastrando
    # ou comprando não pode esperar isso (nem ver um erro se o aviso falhar).
    threading.Thread(target=_avisar_admins, args=(assunto, corpo, corpo_html), daemon=True).start()


def avisar_admins_cadastro_pendente(nome: str, email: str) -> None:
    """Alguém confirmou o e-mail e entrou na fila de aprovação (Cadastros)."""
    url = f"{config.BASE_URL}/admin/cadastros"
    corpo = (
        "\u25b6 Novo cadastro aguardando aprovação\n\n"
        f"{nome} ({email}) confirmou o e-mail e espera a sua aprovação.\n\n"
        f"Ver cadastros: {url}"
    )
    corpo_html = _email_shell(
        _p("Novo cadastro aguardando aprovação.")
        + _p(f"<strong>{_esc(nome)}</strong> ({_esc(email)}) confirmou o e-mail e espera a sua aprovação.")
        + _botao("Ver cadastros", url)
    )
    _avisar_admins_em_segundo_plano("Novo cadastro aguardando aprovação — Back to Halloween", corpo, corpo_html)


def avisar_admins_pedido_novo(
    id_pedido: int,
    quantidade: int,
    valor,
    comprador_nome: str,
    comprador_email: str,
    titular_nome: str | None = None,
    acompanhante_nome: str | None = None,
) -> None:
    """Um pedido acabou de ser criado (ainda aguardando o Pix). `titular_nome`
    só vem preenchido no pedido de outra pessoa; `acompanhante_nome` no pedido
    de 2 ingressos."""
    url = f"{config.BASE_URL}/admin/pedidos"
    valor = f"{float(valor):.2f}"  # o valor da casadinha vem como 50.0, não 50.00
    linhas = [
        f"Pedido #{id_pedido} · {quantidade} ingresso(s) · R$ {valor}",
        f"Comprador: {comprador_nome} ({comprador_email})",
    ]
    if titular_nome:
        linhas.append(f"Ingresso para: {titular_nome}")
    if acompanhante_nome:
        linhas.append(f"Acompanhante: {acompanhante_nome}")

    corpo = "\u25b6 Novo pedido\n\n" + "\n".join(linhas) + f"\n\nVer pedidos: {url}"
    corpo_html = _email_shell(
        _p("Novo pedido feito.")
        + "".join(_p(_esc(linha)) for linha in linhas)
        + _botao("Ver pedidos", url)
    )
    _avisar_admins_em_segundo_plano(
        f"Novo pedido #{id_pedido} — {quantidade} ingresso(s) · R$ {valor}", corpo, corpo_html
    )


def enviar_email_em_massa_em_segundo_plano(destinatarios: list[dict], assunto: str, mensagem: str) -> None:
    """E-mail em massa do admin. Sai numa thread à parte: mandar um a um dentro da
    requisição passava fácil de 30s com a lista cheia, o gunicorn matava o
    worker no meio e sobrava um envio pela metade, sem aviso nenhum."""

    def _enviar_todos() -> None:
        enviados = falhas = 0
        for d in destinatarios:
            try:
                enviar_email_avulso(d["email"], d["nome"], assunto, mensagem)
                enviados += 1
            except Exception:
                logger.warning("Falha ao enviar e-mail em massa pra %s", d["email"], exc_info=True)
                falhas += 1
        logger.info("E-mail em massa '%s': %s enviado(s), %s falha(s)", assunto, enviados, falhas)

    threading.Thread(target=_enviar_todos, daemon=True).start()


def _em_segundo_plano(funcao, *args) -> None:
    """Roda um envio de e-mail numa thread: o Brevo leva até 10s, e dentro de uma
    requisição isso segurava a conexão do banco (e o lock do pedido) ou passava dos
    30s do gunicorn quando eram muitos e-mails. Falha só vai pro log."""

    def _rodar() -> None:
        try:
            funcao(*args)
        except Exception:
            logger.warning("Falha num e-mail em segundo plano (%s)", funcao.__name__, exc_info=True)

    threading.Thread(target=_rodar, daemon=True).start()


def enviar_confirmacao_pedido_em_segundo_plano(destinatario: str, nome: str, pedido) -> None:
    _em_segundo_plano(enviar_confirmacao_pedido, destinatario, nome, dict(pedido))


def enviar_cadastro_aprovado_em_segundo_plano(destinatario: str, nome: str) -> None:
    _em_segundo_plano(enviar_cadastro_aprovado, destinatario, nome)
