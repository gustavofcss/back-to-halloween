"""Configuração via variáveis de ambiente -- lidas de .env em dev
(python-dotenv) e injetadas direto pelo Render em produção (nunca commitamos
.env, ver .gitignore)."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Variável de ambiente obrigatória não definida: {name} (veja .env.example)")
    return value


DATABASE_URL = _require("DATABASE_URL")
SECRET_KEY = _require("SECRET_KEY")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")

PIX_KEY = os.environ.get("PIX_KEY", "")
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000")

MERCADOPAGO_ACCESS_TOKEN = os.environ.get("MERCADOPAGO_ACCESS_TOKEN", "")
