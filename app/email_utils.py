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

import logging

import requests

import config

logger = logging.getLogger(__name__)

_BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def _enviar(destinatario: str, assunto: str, corpo: str, corpo_html: str | None = None) -> None:
    if not config.BREVO_API_KEY:
        logger.warning(
            "BREVO_API_KEY não configurada, e-mail '%s' não enviado -- "
            "configure BREVO_API_KEY no .env",
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
    resposta.raise_for_status()


def _ticket_html(pedido) -> str:
    # Mesmas cores/estrutura do "ingresso" mostrado na tela de pagamento
    # (ver .ticket* em style.css), só que com estilo inline -- cliente de
    # e-mail não carrega arquivo .css externo nem @font-face.
    return f"""
    <div style="max-width:420px;margin:20px auto;border-radius:16px;overflow:hidden;
                background:linear-gradient(170deg,#fbf3e3 0%,#f3e7cf 100%);
                font-family:Arial,Helvetica,sans-serif;color:#241a14;">
      <div style="background:#241a14;padding:16px 26px;display:flex;
                  justify-content:space-between;align-items:center;">
        <p style="margin:0;font-weight:bold;font-size:15px;letter-spacing:2px;
                  text-transform:uppercase;color:#f2dcaa;">Horror Night</p>
        <p style="margin:0;font-size:11px;letter-spacing:2px;text-transform:uppercase;
                  color:#b9a48a;">Ingresso</p>
      </div>
      <div style="padding:24px 26px 20px;display:flex;justify-content:space-between;
                  align-items:flex-start;">
        <div>
          <p style="margin:0 0 4px;font-weight:bold;font-size:20px;">{pedido['quantidade']} &times; Entrada</p>
          <p style="margin:0;font-size:13px;color:#7a6a56;">Pedido #{pedido['id_pedido']}</p>
        </div>
        <p style="margin:0;font-weight:bold;font-size:28px;color:#e1690f;">R$ {pedido['valor']}</p>
      </div>
      <div style="border-top:2px dashed #d8c7a3;margin:0 26px;"></div>
      <div style="padding:20px 26px 26px;">
        <p style="margin:0;font-size:13.5px;line-height:1.6;color:#4a3d2f;">
          Apresente este pedido (#{pedido['id_pedido']}) na entrada da festa.
        </p>
      </div>
    </div>
    """


def enviar_cadastro_aprovado(destinatario: str, nome: str) -> None:
    corpo = (
        f"Oi, {nome}! \U0001f383\n\n"
        "Seu cadastro na Horror Night Party foi confirmado pelo administrador. "
        f"Já pode entrar por aqui: {config.BASE_URL}/login\n\n"
        "Até a noite mais assustadora do ano!"
    )
    _enviar(destinatario, "Cadastro confirmado — Horror Night Party", corpo)


def enviar_confirmacao_pedido(destinatario: str, nome: str, pedido) -> None:
    corpo = (
        f"Oi, {nome}! \U0001f383\n\n"
        "Seu pagamento foi confirmado! O pedido a seguir já está garantido pra "
        "Horror Night Party:\n\n"
        f"Pedido #{pedido['id_pedido']} — {pedido['quantidade']} ingresso(s) — "
        f"R$ {pedido['valor']}\n\n"
        "Nos vemos na noite mais assustadora do ano!"
    )
    corpo_html = (
        f"<p>Oi, {nome}! \U0001f383</p>"
        "<p>Seu pagamento foi confirmado! Seu ingresso pra Horror Night Party:</p>"
        f"{_ticket_html(pedido)}"
        "<p>Nos vemos na noite mais assustadora do ano!</p>"
    )
    _enviar(
        destinatario,
        "Seu ingresso pra Horror Night Party está confirmado!",
        corpo,
        corpo_html,
    )


def enviar_codigo_recuperacao(destinatario: str, nome: str, codigo: str) -> None:
    corpo = (
        f"Oi, {nome}! \U0001f47b\n\n"
        "Recebemos um pedido pra redefinir sua senha da Horror Night Party. "
        f"Use o código abaixo (válido por 15 minutos):\n\n"
        f"{codigo}\n\n"
        "Se você não pediu isso, pode ignorar este e-mail — sua senha continua a mesma."
    )
    _enviar(destinatario, "Código para redefinir sua senha — Horror Night Party", corpo)
