"""Gera o payload Pix "Copia e Cola" (BR Code / EMV QR Code) localmente --
sem nenhuma chamada de API pra criar o pagamento (isso é o que evita a taxa
do Checkout: o QR é só uma representação do "pague pra esta chave, este
valor", igual um boleto -- quem processa o pagamento de verdade é o Pix da
própria chave, não o Mercado Pago).

Formato EMV QRCPS-MPM (o mesmo usado por todo Pix do Brasil, especificado
pelo Bacen): série de campos "TTLLVVVV..." (tag de 2 dígitos, tamanho de 2
dígitos, valor), terminando com o CRC16 do resto da string."""
from __future__ import annotations

import base64
import io
from decimal import Decimal

import qrcode

_POLINOMIO_CRC16 = 0x1021
_INICIAL_CRC16 = 0xFFFF


def _campo(tag: str, valor: str) -> str:
    return f"{tag}{len(valor):02d}{valor}"


def _crc16_ccitt(dados: str) -> str:
    # CRC16-CCITT (polinômio 0x1021, inicial 0xFFFF) -- o mesmo algoritmo
    # que todo validador de Pix Copia-e-Cola usa pra conferir os últimos 4
    # caracteres da string. Implementado na mão (sem lib externa) porque é
    # só isso que falta pro payload ficar 100% correto.
    crc = _INICIAL_CRC16
    for byte in dados.encode("ascii"):
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ _POLINOMIO_CRC16) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def gerar_copia_cola(
    chave: str,
    valor: Decimal | float,
    *,
    nome_recebedor: str = "HORROR NIGHT PARTY",
    cidade: str = "RECIFE",
    txid: str = "***",
) -> str:
    """Monta o Pix Copia e Cola pra um valor fixo, pronto pra virar QR Code
    (ver static/... não, isso é gerado on-the-fly em pedidos.py) ou ser
    colado direto num app de banco.

    nome_recebedor: até 25 caracteres. cidade: até 15. txid: até 25
    (alfanumérico -- "***" é o valor reservado pelo padrão Bacen pra "sem
    identificador específico", usado quando não precisamos que o pagador
    veja nada além do valor)."""
    nome_recebedor = nome_recebedor.strip().upper()[:25]
    cidade = cidade.strip().upper()[:15]
    txid = (txid or "***").strip()[:25]

    merchant_account_info = _campo("00", "br.gov.bcb.pix") + _campo("01", chave)
    additional_data = _campo("05", txid)

    payload_sem_crc = (
        _campo("00", "01")  # Payload Format Indicator
        + _campo("26", merchant_account_info)  # Merchant Account Information -- Pix
        + _campo("52", "0000")  # Merchant Category Code
        + _campo("53", "986")  # Transaction Currency -- BRL
        + _campo("54", f"{Decimal(valor):.2f}")  # Transaction Amount
        + _campo("58", "BR")  # Country Code
        + _campo("59", nome_recebedor)  # Merchant Name
        + _campo("60", cidade)  # Merchant City
        + _campo("62", additional_data)  # Additional Data Field Template
        + "6304"  # CRC16 tag + tamanho fixo (o valor vem depois, calculado sobre isto)
    )
    return payload_sem_crc + _crc16_ccitt(payload_sem_crc)


def gerar_qr_base64(copia_cola: str) -> str:
    """PNG do QR Code, pronto pra `<img src="data:image/png;base64,...">` --
    gerado localmente (lib qrcode), sem chamada de API."""
    buf = io.BytesIO()
    qrcode.make(copia_cola).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")
