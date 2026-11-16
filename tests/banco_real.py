"""Ajuda dos testes que usam um PostgreSQL de verdade (embarcado, via `pgserver`).

`pip install pgserver` -- sem isso os testes de banco são pulados. O servidor sobe
numa pasta temporária, usa uma porta local e é apagado no fim. Nada toca no banco
de produção.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

try:
    import pgserver
except ImportError:  # pragma: no cover
    pgserver = None

try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # pragma: no cover
    psycopg2 = None


def disponivel() -> bool:
    return pgserver is not None and psycopg2 is not None


class BancoReal:
    """Um servidor Postgres temporário com a capacidade de criar bancos isolados."""

    def __init__(self) -> None:
        self._pasta = tempfile.mkdtemp(prefix="pg3fn_")
        self._srv = pgserver.get_server(self._pasta)
        self._base = self._srv.get_uri().rsplit("/", 1)[0]

    def criar(self, nome: str) -> str:
        """Cria um banco vazio e devolve a URL de conexão."""
        self._srv.psql(f"CREATE DATABASE {nome};")
        return f"{self._base}/{nome}"

    def fechar(self) -> None:
        try:
            self._srv.cleanup()
        finally:
            shutil.rmtree(self._pasta, ignore_errors=True)


def conectar(url: str):
    conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = False
    return conn


def rodar_sql(url: str, sql: str) -> None:
    conn = conectar(url)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()


def ler_arquivo(caminho: Path) -> str:
    return caminho.read_text(encoding="utf-8")


def pular(motivo: str) -> None:
    print(f"PULADO {motivo}")
    sys.exit(0)
