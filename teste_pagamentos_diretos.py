"""Script ISOLADO pra consultar a API de Pagamentos do Mercado Pago e ver
os pagamentos recebidos num periodo -- inclui Pix pago DIRETO na chave
(sem Checkout), diferente do relatorio de liquidacao (ver
teste_mercadopago.py), que so cobre reserva de VENDA.

NAO faz parte do sistema principal, so leitura (GET), nao cria nem altera
nada.

Achado real (2026-09-24): um Pix pago direto na chave aleatoria da conta
apareceu aqui como um `payment` normal (status=approved,
payment_method_id=pix, payment_type_id=bank_transfer), em tempo real, SEM
taxa (fee_details vazio, net_received_amount = valor cheio) -- mas SEM
nome do pagador (payer.first_name/last_name vem null; o CNPJ que aparece
em payer.identification e do BANCO de origem, nao da pessoa).

Uso:
    python teste_pagamentos_diretos.py [horas_pra_tras]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent / "app"))
import config  # noqa: E402

TIMEOUT = 30


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.MERCADOPAGO_ACCESS_TOKEN}"}


def buscar_pagamentos(inicio: datetime, fim: datetime) -> list[dict]:
    params = {
        "begin_date": inicio.strftime("%Y-%m-%dT%H:%M:%S.000-00:00"),
        "end_date": fim.strftime("%Y-%m-%dT%H:%M:%S.000-00:00"),
        "range": "date_created",
        "sort": "date_created",
        "criteria": "desc",
    }
    resp = requests.get(
        "https://api.mercadopago.com/v1/payments/search",
        headers=_headers(),
        params=params,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def resumo(pagamento: dict) -> dict:
    banco = (
        pagamento.get("point_of_interaction", {})
        .get("transaction_data", {})
        .get("bank_info", {})
        .get("payer", {})
        .get("long_name")
    )
    return {
        "id": pagamento.get("id"),
        "status": pagamento.get("status"),
        "payment_type_id": pagamento.get("payment_type_id"),
        "valor": pagamento.get("transaction_amount"),
        "criado_em": pagamento.get("date_created"),
        "aprovado_em": pagamento.get("date_approved"),
        "banco_pagador": banco,
        "sub_type": pagamento.get("point_of_interaction", {}).get("sub_type"),
        "transaction_id": pagamento.get("transaction_details", {}).get("transaction_id"),
        "taxa": pagamento.get("fee_details"),
    }


def main() -> None:
    if not config.MERCADOPAGO_ACCESS_TOKEN:
        print("MERCADOPAGO_ACCESS_TOKEN nao configurado no .env -- abortando.")
        return

    horas = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    fim = datetime.now(timezone.utc)
    inicio = fim - timedelta(hours=horas)

    print(f"Buscando pagamentos de {inicio.isoformat()} ate {fim.isoformat()}...")
    pagamentos = buscar_pagamentos(inicio, fim)
    print(f"\n{len(pagamentos)} pagamento(s) encontrado(s).\n")
    for p in pagamentos:
        print(json.dumps(resumo(p), indent=2, ensure_ascii=False))
        print()


if __name__ == "__main__":
    main()
