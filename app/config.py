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

# E-mail transacional via API do Resend (HTTPS) -- SMTP direto (porta 587)
# é bloqueado pelo Render (achado real: conexão em smtp.gmail.com travava até
# o worker do gunicorn matar a requisição). RESEND_FROM usa o remetente de
# testes do Resend por padrão -- funciona sem precisar verificar um domínio
# próprio; troque só se/quando verificar um domínio de verdade lá.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "Horror Night Party <onboarding@resend.dev>")

PIX_KEY = os.environ.get("PIX_KEY", "")
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000")

MERCADOPAGO_ACCESS_TOKEN = os.environ.get("MERCADOPAGO_ACCESS_TOKEN", "")

# Links de pagamento por cartão (Checkout Pro) criados manualmente pelo
# usuário no site do Mercado Pago -- um por valor possível de pedido (preço
# do lote x quantidade). Se o preço do lote mudar, esses links precisam ser
# recriados manualmente e atualizados aqui.
LINKS_PAGAMENTO_CARTAO = {
    30.0: os.environ.get("LINK_PAGAMENTO_30", ""),
    60.0: os.environ.get("LINK_PAGAMENTO_60", ""),
}
