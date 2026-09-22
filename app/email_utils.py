"""Envio de e-mail de confirmação de conta -- smtplib puro (stdlib), sem
dependência nova."""
from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

import config

logger = logging.getLogger(__name__)


def enviar_cadastro_aprovado(destinatario: str, nome: str) -> None:
    if not config.SMTP_USER:
        logger.warning(
            "SMTP não configurado, e-mail de cadastro aprovado não enviado -- "
            "configure SMTP_* no .env"
        )
        return

    corpo = (
        f"Oi, {nome}! \U0001f383\n\n"
        "Seu cadastro na Horror Night Party foi confirmado pelo administrador. "
        f"Já pode entrar por aqui: {config.BASE_URL}/login\n\n"
        "Até a noite mais assustadora do ano!"
    )

    msg = MIMEText(corpo, "plain", "utf-8")
    msg["Subject"] = "Cadastro confirmado -- Horror Night Party"
    msg["From"] = config.SMTP_FROM
    msg["To"] = destinatario

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.sendmail(config.SMTP_FROM, [destinatario], msg.as_string())


def enviar_confirmacao_pedido(destinatario: str, nome: str, pedido) -> None:
    if not config.SMTP_USER:
        logger.warning(
            "SMTP não configurado, e-mail de confirmação de pedido não enviado -- "
            "configure SMTP_* no .env"
        )
        return

    corpo = (
        f"Oi, {nome}! \U0001f383\n\n"
        "Seu pagamento foi confirmado! O pedido a seguir já está garantido pra "
        "Horror Night Party:\n\n"
        f"Pedido #{pedido['id_pedido']} -- {pedido['quantidade']} ingresso(s) -- "
        f"R$ {pedido['valor']}\n\n"
        "Nos vemos na noite mais assustadora do ano!"
    )

    msg = MIMEText(corpo, "plain", "utf-8")
    msg["Subject"] = "Seu ingresso pra Horror Night Party está confirmado!"
    msg["From"] = config.SMTP_FROM
    msg["To"] = destinatario

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.sendmail(config.SMTP_FROM, [destinatario], msg.as_string())


def enviar_codigo_recuperacao(destinatario: str, nome: str, codigo: str) -> None:
    if not config.SMTP_USER:
        logger.warning(
            "SMTP não configurado, código de recuperação não enviado -- "
            "configure SMTP_* no .env"
        )
        return

    corpo = (
        f"Oi, {nome}! \U0001f47b\n\n"
        "Recebemos um pedido pra redefinir sua senha da Horror Night Party. "
        f"Use o código abaixo (válido por 15 minutos):\n\n"
        f"{codigo}\n\n"
        "Se você não pediu isso, pode ignorar este e-mail -- sua senha continua a mesma."
    )

    msg = MIMEText(corpo, "plain", "utf-8")
    msg["Subject"] = "Código para redefinir sua senha -- Horror Night Party"
    msg["From"] = config.SMTP_FROM
    msg["To"] = destinatario

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.sendmail(config.SMTP_FROM, [destinatario], msg.as_string())
