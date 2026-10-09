"""Conexão com o Postgres -- projeto pessoal, banco próprio, sem nenhuma relação
com credenciais/bancos de trabalho.

Pool de conexões por processo. Antes cada `get_conn()` abria uma conexão NOVA
(TCP + TLS + login no Supabase) e a fechava no fim: uma página do admin abria 6
delas, e com o app na Oregon e o banco na Virgínia isso custava cerca de 0,7 s
por conexão (achado real, medindo o site no ar). Agora as conexões são
emprestadas de um pool e devolvidas, e só a primeira de cada uma paga o custo."""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extras
from psycopg2 import pool

from config import DATABASE_URL

logger = logging.getLogger(__name__)

# Por processo do gunicorn (2 workers = 10 no total, dentro dos 15 do pooler gratuito
# do Supabase). Cada requisição pede até 2 ao mesmo
# tempo (a expiração de pedidos usa uma transação à parte), com 4 threads.
_MAX_CONEXOES = int(os.environ.get("DB_POOL_MAX", "5"))
# O Supabase (e o Render) derrubam conexão parada. Se uma conexão ficou ociosa
# mais que isso, testa antes de usar em vez de estourar no meio de uma página.
_OCIOSA_MAX = 20.0
# Com o pool cheio, espera uma conexão voltar (poucos ms) em vez de abrir uma
# nova na hora, que é justamente o que custa ~0,7 s. Só estourando esse tempo
# abre uma conexão avulsa.
_ESPERA_VAGA = 2.0

_pool: pool.ThreadedConnectionPool | None = None
_vagas: threading.BoundedSemaphore | None = None
_trava = threading.Lock()
_ultimo_uso: dict[int, float] = {}


def _opcoes() -> dict:
    return {
        "cursor_factory": psycopg2.extras.RealDictCursor,
        "connect_timeout": 10,
        # detecta conexão morta pela rede sem esperar o timeout do SO
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    }


def _pool_do_processo() -> pool.ThreadedConnectionPool:
    global _pool, _vagas
    if _pool is None:
        with _trava:
            if _pool is None:
                _vagas = threading.BoundedSemaphore(_MAX_CONEXOES)
                novo = pool.ThreadedConnectionPool(1, _MAX_CONEXOES, DATABASE_URL, **_opcoes())
                # O psycopg2 só guarda `minconn` conexões ociosas e FECHA as demais
                # ao devolvê-las: com minconn=1, qualquer pico de requisições em
                # paralelo abria e fechava conexões o tempo todo (o ganho do pool
                # sumia). Subir o atributo depois da criação guarda até o máximo
                # sem abrir tudo de uma vez na primeira requisição.
                novo.minconn = _MAX_CONEXOES
                _pool = novo
    return _pool


def _utilizavel(conn) -> bool:
    if conn.closed:
        return False
    parada_ha = time.monotonic() - _ultimo_uso.get(id(conn), time.monotonic())
    if parada_ha < _OCIOSA_MAX:
        return True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.rollback()
        return True
    except Exception:
        return False


def _emprestar():
    """(conexão, veio_do_pool). Se não aparecer vaga no pool a tempo, devolve uma
    conexão avulsa, que é fechada no fim em vez de voltar pro pool."""
    pool_ = _pool_do_processo()
    if not _vagas.acquire(timeout=_ESPERA_VAGA):
        return psycopg2.connect(DATABASE_URL, **_opcoes()), False
    try:
        for _ in range(3):
            conn = pool_.getconn()
            if _utilizavel(conn):
                return conn, True
            _ultimo_uso.pop(id(conn), None)
            pool_.putconn(conn, close=True)
        # as 3 do pool estavam mortas: abre uma avulsa (não entra no pool), então
        # a vaga do semáforo é devolvida já
        conn = psycopg2.connect(DATABASE_URL, **_opcoes())
    except BaseException:
        _vagas.release()
        raise
    _vagas.release()
    return conn, False


def _devolver(conn, do_pool: bool) -> None:
    try:
        if not do_pool:
            conn.close()
        elif conn.closed:
            _ultimo_uso.pop(id(conn), None)
            _pool.putconn(conn, close=True)
        else:
            _ultimo_uso[id(conn)] = time.monotonic()
            _pool.putconn(conn)
    except Exception:
        logger.warning("Falha ao devolver a conexão ao pool", exc_info=True)
    finally:
        if do_pool:
            _vagas.release()


@contextmanager
def get_conn() -> Iterator["psycopg2.extensions.connection"]:
    """Uma conexão emprestada do pool, com commit automático se não der exceção
    (e rollback se der) -- mesmo contrato de antes. Usos aninhados pegam
    conexões DIFERENTES (transações independentes), como sempre foi."""
    conn, do_pool = _emprestar()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass  # a própria conexão morreu; _devolver a descarta
        raise
    finally:
        _devolver(conn, do_pool)
