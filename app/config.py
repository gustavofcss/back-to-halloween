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

# E-mail transacional via API do Brevo (HTTPS) -- SMTP direto (porta 587) é
# bloqueado pelo Render (achado real: conexão em smtp.gmail.com travava até
# o worker do gunicorn matar a requisição). Trocado do Resend pro Brevo
# porque o Resend só manda pra fora com um domínio verificado (DNS) -- o
# Brevo verifica só o e-mail remetente (clicando num link), sem precisar de
# domínio próprio, e ainda assim manda pra qualquer destinatário.
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
BREVO_FROM_EMAIL = os.environ.get("BREVO_FROM_EMAIL", "horrornightparty2026@gmail.com")
BREVO_FROM_NOME = os.environ.get("BREVO_FROM_NOME", "Back to Halloween")

# Uma chave Pix DIFERENTE por valor possível de pedido (preço do lote x
# quantidade) -- pedido explícito do usuário: 1 ingresso paga numa chave,
# 2 ingressos pagam noutra, cada uma criada à parte no Mercado Pago. Se o
# preço do lote mudar, precisa cadastrar uma chave nova pro valor novo (e a
# variável de ambiente correspondente).
PIX_KEYS = {
    30.0: os.environ.get("PIX_KEY_30", ""),
    60.0: os.environ.get("PIX_KEY_60", ""),
    50.0: os.environ.get("PIX_KEY_50", ""),  # promoção casadinha (2 por R$ 50)
}

# Promoção "casadinha": 2 ingressos no MESMO pedido por um valor fechado, em
# vez de valor_entrada x 2. Quem liga/desliga é o admin (lote.promo_casadinha,
# tela Evento). O valor é fixo aqui porque cada valor possível precisa de uma
# chave Pix (PIX_KEYS) criada à mão -- se mudar, cadastre a chave pro valor
# novo.
PROMO_CASADINHA_VALOR = 50.0
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000")

MERCADOPAGO_ACCESS_TOKEN = os.environ.get("MERCADOPAGO_ACCESS_TOKEN", "")
