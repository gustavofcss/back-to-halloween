"""Conexão com o Postgres -- projeto pessoal, banco próprio (Render), sem
nenhuma relação com credenciais/bancos de trabalho."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extras

from config import DATABASE_URL


@contextmanager
def get_conn() -> Iterator["psycopg2.extensions.connection"]:
    """Uma conexão nova por uso, com commit automático se não der exceção
    (e rollback se der) -- padrão simples de context manager, sem pool
    (volume de uso desta ferramenta é baixo o suficiente pra não precisar)."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
