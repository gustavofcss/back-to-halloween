import os, sys, re
from unittest import mock

raiz = sys.argv[1]
os.chdir(raiz + "/app")
sys.path.insert(0, raiz + "/app")
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost:1/x")
os.environ.setdefault("SECRET_KEY", "teste")

falhas = 0


def check(nome, cond):
    global falhas
    print(("OK   " if cond else "FALHOU ") + nome)
    if not cond:
        falhas += 1


import db
import admin
import app as appmod
import email_utils

# _decimal_ou_none: 0.001 arredonda para 0.00 e deve ser recusado
check("0.001 recusado", admin._decimal_ou_none("0.001") is None)
check("0,004 recusado", admin._decimal_ou_none("0,004") is None)
check("30,5 aceito", str(admin._decimal_ou_none("30,5")) == "30.50")
check("negativo recusado", admin._decimal_ou_none("-1") is None)

# db: após 3 mortas devolve conexão avulsa (do_pool False) e libera a vaga
class Morta:
    closed = True

class Viva:
    closed = False

conexoes = []
class PoolFalso:
    def getconn(self):
        return Morta()
    def putconn(self, c, close=False):
        pass

db._pool = PoolFalso()
import threading
db._vagas = threading.BoundedSemaphore(2)
with mock.patch.object(db.psycopg2, "connect", return_value=Viva()):
    conn, do_pool = db._emprestar()
check("conexão avulsa marcada como fora do pool", do_pool is False)
check("vaga do semáforo devolvida", db._vagas.acquire(blocking=False) and db._vagas.acquire(blocking=False))
check("OCIOSA_MAX = 20", db._OCIOSA_MAX == 20.0)

# e-mail de cortesia
enviados = []
with mock.patch.object(email_utils, "_enviar", lambda d, a, c, h: enviados.append((a, c, h))):
    ped = {"id_pedido": 7, "quantidade": 1, "valor": "0.00", "cortesia": True, "acompanhante_nome": None}
    try:
        email_utils.enviar_confirmacao_pedido("a@x.test", "Ana", ped)
    except Exception as e:
        print("erro", e)
check("e-mail de cortesia não diz 'pagamento foi confirmado'", enviados and "pagamento foi confirmado" not in enviados[0][1] and "cortesia" in enviados[0][1])
enviados.clear()
with mock.patch.object(email_utils, "_enviar", lambda d, a, c, h: enviados.append((a, c, h))):
    email_utils.enviar_confirmacao_pedido("a@x.test", "Ana", {**ped, "cortesia": False, "valor": "30.00"})
check("e-mail normal mantém 'pagamento foi confirmado'", enviados and "pagamento foi confirmado" in enviados[0][1])

# cache imutável só em 2xx/304
cl = appmod.app.test_client()
r = cl.get("/static/css/style.css")
r2 = cl.get(re.search(r'/static/css/style\.css\?v=\w+', cl.get("/").get_data(as_text=True)).group(0)) if False else None
r_404 = cl.get("/static/fonts/nao-existe.woff2")
check("404 de fonte não vira imutável", "immutable" not in (r_404.headers.get("Cache-Control") or ""))
r_ok = cl.get("/static/fonts/anton-400.woff2")
check("fonte existente fica imutável", r_ok.status_code == 200 and "immutable" in (r_ok.headers.get("Cache-Control") or ""))

# preload sem ?v=
with appmod.app.test_request_context("/"):
    from flask import render_template_string, url_for
    h = render_template_string("{{ url_for('static', filename='fonts/anton-400.woff2', v=None) }}")
check("preload sem ?v=", "?" not in h)

# xlsx: caracteres ilegais removidos
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
check("regex de caracteres ilegais disponível", ILLEGAL_CHARACTERS_RE.sub("", "a\x00b") == "ab")

print("\n%s" % ("%d FALHA(S)" % falhas if falhas else "tudo certo"))
sys.exit(1 if falhas else 0)
