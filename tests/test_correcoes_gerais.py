import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost:1/z")
os.environ.setdefault("SECRET_KEY", "t")
sys.path.insert(0, sys.argv[1] + "/app")

import config  # noqa: E402
import pedidos  # noqa: E402
import admin  # noqa: E402

ok = 0


def check(nome, cond):
    global ok
    print(("OK   " if cond else "FALHOU"), nome)
    if not cond:
        sys.exit(1)
    ok += 1


# ---------------- _proximo_slot / limite de ingressos ----------------
ev = {"quantidade_max_ingressos": 2}
P = lambda slot, q: {"slot": slot, "quantidade": q}
check("conta nova -> slot 1 com até 2", pedidos._proximo_slot([], ev) == (1, 2))
check("1 pedido de 1 ingresso (slot 1) -> slot 2 com 1", pedidos._proximo_slot([P(1, 1)], ev) == (2, 1))
check("1 pedido de 2 ingressos (casadinha) -> bloqueia", pedidos._proximo_slot([P(1, 2)], ev) is None)
check("2 pedidos -> bloqueia", pedidos._proximo_slot([P(1, 1), P(2, 1)], ev) is None)
check("só o slot 2 ativo (slot 1 cancelado) -> volta pro slot 1 com 1", pedidos._proximo_slot([P(2, 1)], ev) == (1, 1))
check("limite 1 ingresso por conta", pedidos._proximo_slot([P(1, 1)], {"quantidade_max_ingressos": 1}) is None)

# ---------------- validadores do admin ----------------
d = admin._decimal_ou_none
check("valor '30' ok", d("30") == Decimal("30.00"))
check("valor '30,50' (vírgula) ok", d("30,50") == Decimal("30.50"))
check("valor 'abc' recusado", d("abc") is None)
check("valor negativo recusado", d("-5") is None)
check("valor zero recusado", d("0") is None)
check("valor vazio recusado", d("") is None and d(None) is None)
check("valor NaN/Infinity recusado", d("NaN") is None and d("Infinity") is None)
check("valor absurdo recusado", d("999999999") is None)
i = admin._inteiro_ou_none
check("inteiro 60 em 1..5000", i("60", 1, 5000) == 60)
check("inteiro 'x' recusado", i("x", 1, 5000) is None)
check("inteiro fora da faixa recusado", i("0", 1, 5000) is None and i("9999", 1, 5000) is None)

# ---------------- CSRF por Origin (Flask de verdade) ----------------
import app as appmod  # noqa: E402

c = appmod.app.test_client()
r = c.post("/login", data={"email": "a@b.c", "senha": "x"}, headers={"Origin": "https://evil.example"})
check("POST com Origin de outro site -> 403", r.status_code == 403)
r = c.post("/login", data={"email": "a@b.c", "senha": "x"}, headers={"Origin": "null"})
check("POST com Origin 'null' -> 403", r.status_code == 403)
try:
    r = c.post("/login", data={"email": "a@b.c", "senha": "x"}, headers={"Origin": "http://localhost"})
    passou = r.status_code != 403
except Exception:
    passou = True  # chegou na view (e quebrou sem banco), ou seja, o CSRF liberou
check("POST com Origin do próprio site passa pelo filtro", passou)
r = c.post("/pagamento/webhook/mercadopago", json={"x": 1})
check("webhook sem Origin (servidor-pra-servidor) passa", r.status_code == 200)
check("cookie de sessão com SameSite=Lax", appmod.app.config["SESSION_COOKIE_SAMESITE"] == "Lax")

# ---------------- expiração de pedidos abandonados (banco simulado) ----------------
agora = datetime.now(timezone.utc)
config.MERCADOPAGO_ACCESS_TOKEN = "TEST"
chamadas = []


class Cur:
    def __init__(self):
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        if "FROM v_pedido p" in self.sql and "p.data_pedido <" in self.sql:
            return [
                {"id_pedido": 7, "valor": Decimal("30"), "data_pedido": agora - timedelta(minutes=30),
                 "status_pedido": "pendente", "mp_payment_id": None, "titular_nome": None,
                 "titular_email": None, "nome_conta": "Ana", "email_conta": "a@b.c"},
            ]
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class Conn:
    def cursor(self):
        return Cur()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


pedidos.db.get_conn = lambda: Conn()
pedidos._verificar_com_expiracao = lambda cur, p, t: chamadas.append(p["id_pedido"]) or "cancelado"
pedidos.expirar_pedidos_vencidos()
check("sweep reconcilia/expira o pedido vencido #7", chamadas == [7])
config.MERCADOPAGO_ACCESS_TOKEN = ""
chamadas.clear()
pedidos.expirar_pedidos_vencidos()
check("sweep não faz nada sem token do Mercado Pago", chamadas == [])

# ---------------- cache da busca no Mercado Pago ----------------
n = []


class Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"results": [{"id": 1}]}


pedidos.requests.get = lambda *a, **k: n.append(1) or Resp()
config.MERCADOPAGO_ACCESS_TOKEN = "TEST"
t = agora.replace(second=10)
pedidos._buscar_pagamentos_pix_aprovados(t)
pedidos._buscar_pagamentos_pix_aprovados(t.replace(second=40))
check("2 consultas no mesmo minuto -> 1 chamada à API", len(n) == 1)
pedidos._buscar_pagamentos_pix_aprovados(t + timedelta(minutes=2))
check("minuto diferente -> chamada nova", len(n) == 2)

# ---------------- cartão removido ----------------
check("config sem LINKS_PAGAMENTO_CARTAO", not hasattr(config, "LINKS_PAGAMENTO_CARTAO"))
check("rota de cartão removida", "pedidos.pagamento_cartao" not in appmod.app.view_functions)

print(f"\n{ok} verificações passaram")
