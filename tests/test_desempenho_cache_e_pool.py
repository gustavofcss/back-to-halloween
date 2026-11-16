import hashlib
import os
import re
import sys
import threading
import time

os.environ["DATABASE_URL"] = "postgresql://x:y@localhost:1/z"
os.environ["SECRET_KEY"] = "t"
os.environ["BASE_URL"] = "https://exemplo.test"
raiz = sys.argv[1]
sys.path.insert(0, raiz + "/app")

import psycopg2  # noqa: E402

ok = True


def check(nome, cond, detalhe=""):
    global ok
    print(("OK   " if cond else "FALHOU"), nome, detalhe)
    ok = ok and cond


# ------------------------------------------------------------ banco simulado
class _Info:
    transaction_status = 0


class Cur:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.consultas.append(sql)
        if self.conn.quebrada and sql == "SELECT 1":
            raise psycopg2.OperationalError("server closed the connection")

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class Conn:
    def __init__(self):
        self.closed = 0
        self.info = _Info()
        self.consultas, self.commits, self.rollbacks, self.fechada = [], 0, 0, False
        self.quebrada = False

    def cursor(self, *a, **k):
        return Cur(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = 1
        self.fechada = True


abertas = []


def conectar(*a, **k):
    c = Conn()
    abertas.append(c)
    return c


psycopg2.connect = conectar

import db  # noqa: E402


def zerar(maximo=6):
    db._pool = None
    db._ultimo_uso.clear()
    db._MAX_CONEXOES = maximo
    abertas.clear()


# 1) reaproveita a conexão
zerar()
with db.get_conn() as c1:
    pass
with db.get_conn() as c2:
    pass
check("2 usos seguidos reaproveitam a MESMA conexão", c1 is c2 and len(abertas) == 1, f"(abertas: {len(abertas)})")
check("sucesso faz commit", c1.commits == 2 and c1.rollbacks == 0)

# 2) exceção: rollback, propaga, e a conexão volta pro pool
zerar()
try:
    with db.get_conn() as c:
        raise ValueError("boom")
except ValueError:
    pass
check("exceção faz rollback e propaga", c.rollbacks == 1 and c.commits == 0)
with db.get_conn() as c3:
    pass
check("conexão que deu exceção é reaproveitada depois", c3 is c and len(abertas) == 1)

# 3) uso aninhado = conexões diferentes (transações independentes)
zerar()
with db.get_conn() as externa:
    with db.get_conn() as interna:
        pass
check("uso aninhado usa conexões diferentes", externa is not interna and len(abertas) == 2)

# 4) pool cheio: conexão avulsa, fechada no fim e fora do pool
zerar(maximo=2)
db._ESPERA_VAGA = 0.05  # no teste não espera 2s pela vaga que nunca vai aparecer
with db.get_conn() as a:
    with db.get_conn() as b:
        with db.get_conn() as extra:
            check("pool cheio (2) abre uma conexão avulsa pro 3º pedido", extra not in (a, b) and len(abertas) == 3)
check("conexão avulsa é FECHADA no fim", extra.fechada is True)
check("as do pool continuam abertas", not a.fechada and not b.fechada)
with db.get_conn() as depois:
    pass
check("depois, o pool segue funcionando", depois in (a, b))

# 5) conexão ociosa e quebrada é descartada e substituída
zerar()
with db.get_conn() as velha:
    pass
velha.quebrada = True
db._ultimo_uso[id(velha)] -= 200  # parada há mais de 45s
with db.get_conn() as nova:
    pass
check("conexão ociosa que morreu é descartada", nova is not velha and velha.fechada)
check("a substituta é uma conexão nova", len(abertas) == 2)

# 6) conexão ociosa mas viva só é testada (SELECT 1) e mantida
zerar()
with db.get_conn() as viva:
    pass
db._ultimo_uso[id(viva)] -= 200
with db.get_conn() as mesma:
    pass
check("conexão ociosa mas viva é mantida", mesma is viva and "SELECT 1" in viva.consultas and len(abertas) == 1)

# 7) conexão já fechada é descartada na devolução
zerar()
with db.get_conn() as c:
    c.closed = 2
with db.get_conn() as d:
    pass
check("conexão fechada na devolução não volta pro pool", d is not c and len(abertas) == 2)

# 8) várias threads ao mesmo tempo
zerar(maximo=3)
db._ESPERA_VAGA = 5.0
erros = []
usos = {"max_simultaneas": 0, "agora": 0}
trava = threading.Lock()


def trabalho():
    try:
        for _ in range(20):
            with db.get_conn():
                with trava:
                    usos["agora"] += 1
                    usos["max_simultaneas"] = max(usos["max_simultaneas"], usos["agora"])
                time.sleep(0.001)
                with trava:
                    usos["agora"] -= 1
    except Exception as e:  # noqa: BLE001
        erros.append(repr(e))


ts = [threading.Thread(target=trabalho) for _ in range(10)]
[t.start() for t in ts]
[t.join() for t in ts]
check("10 threads x 20 usos com pool de 3: sem erro", not erros, str(erros[:1]))
avulsas_fechadas = sum(1 for c in abertas if c.fechada)
check("10 threads disputando 3 vagas ESPERAM em vez de abrir conexão nova", len(abertas) <= 4, f"(abertas {len(abertas)}, fechadas {avulsas_fechadas})")

# ------------------------------------------------------- estáticos versionados
zerar()
import app as appmod  # noqa: E402

c = appmod.app.test_client()
html = c.get("/").get_data(as_text=True)
css = re.search(r'href="(/static/css/style\.css\?v=([0-9a-f]{10}))"', html)
check("o HTML aponta pro CSS com ?v=<hash>", css is not None)
esperado = hashlib.md5(open(raiz + "/app/static/css/style.css", "rb").read()).hexdigest()[:10]
check("o hash é o do CONTEÚDO do arquivo", css and css.group(2) == esperado)
r = c.get(css.group(1))
check("estático com ?v= fica 1 ano no cache (immutable)", "max-age=31536000" in r.headers.get("Cache-Control", "") and "immutable" in r.headers["Cache-Control"], r.headers.get("Cache-Control"))
r = c.get("/static/css/style.css")
check("estático SEM ?v= continua revalidando", "immutable" not in (r.headers.get("Cache-Control") or ""), r.headers.get("Cache-Control"))
check("HTML da página não é cacheado", "immutable" not in (c.get("/").headers.get("Cache-Control") or ""))
for arq in ("js/vhs.js", "js/comum.js", "css/vhs.css", "css/convite.css", "assets/og.png", "assets/eyes.mp4"):
    check(f"/static/{arq}?v= responde 200", c.get(f"/static/{arq}?v=x").status_code == 200)
with appmod.app.test_request_context("/"):
    from flask import url_for
    u = url_for("static", filename="nao-existe.css")
    check("arquivo inexistente não quebra o url_for", u == "/static/nao-existe.css", u)

print("\n" + ("TUDO CERTO" if ok else "HÁ FALHAS"))
sys.exit(0 if ok else 1)
