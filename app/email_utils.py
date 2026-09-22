"""Envio de e-mail de confirmação de conta -- smtplib puro (stdlib), sem
dependência nova."""
from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

import config

logger = logging.getLogger(__name__)


def enviar_email_confirmacao(destinatario: str, nome: str, token: str) -> None:
    if not config.SMTP_USER:
        logger.warning(
            "SMTP não configurado, e-mail de confirmação não enviado -- "
            "configure SMTP_* no .env"
        )
        return

    link = f"{config.BASE_URL}/confirmar-email/{token}"
    corpo = (
        f"Oi, {nome}! \U0001f383\n\n"
        "Falta pouco pra garantir seu lugar na Horror Night Party. "
        "Confirme seu e-mail clicando no link abaixo (válido por 24 horas):\n\n"
        f"{link}\n\n"
        "Se você não fez esse cadastro, pode ignorar este e-mail.\n\n"
        "Até a noite mais assustadora do ano!"
    )

    msg = MIMEText(corpo, "plain", "utf-8")
    msg["Subject"] = "Confirme seu e-mail -- Horror Night Party"
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
