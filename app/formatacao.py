"""Formatação compartilhada entre app.py (filtro de template) e admin.py (JSON)."""
from __future__ import annotations

from zoneinfo import ZoneInfo

_TZ_BRASIL = ZoneInfo("America/Sao_Paulo")


def data_hora_br(valor) -> str:
    """dd/mm/aaaa hh:mm no horário de Brasília. O valor vem em UTC (TIMESTAMPTZ):
    sem converter, mostra 3h a mais do que o relógio de Brasília. Sem valor, "--"."""
    if not valor:
        return "--"
    return valor.astimezone(_TZ_BRASIL).strftime("%d/%m/%Y %H:%M")
