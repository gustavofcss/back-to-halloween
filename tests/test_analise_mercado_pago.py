import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost:1/z")
os.environ.setdefault("SECRET_KEY", "t")
sys.path.insert(0, sys.argv[1] + "/app")

import config  # noqa: E402
import pedidos  # noqa: E402
import email_utils as e  # noqa: E402


def check(nome, cond):
    print(("OK   " if cond else "FALHOU"), nome)
    if not cond:
        sys.exit(1)


config.MERCADOPAGO_ACCESS_TOKEN = "TEST"
agora = datetime.now(timezone.utc)


class Cur:
    def __init__(self):
        self.sqls = []
        self.rowcount = 1
        self.connection = type("C", (), {"rollback": lambda self: None})()

    def execute(self, sql, params=None):
        self.sqls.append(sql)

    def fetchall(self):
        return []


def pedido(minutos=10, valor="30.00"):
    return {"id_pedido": 9, "valor": Decimal(valor), "data_pedido": agora - timedelta(minutes=minutos),
            "status_pedido": "pendente", "mp_payment_id": None, "nome": "A", "email": "a@x.com"}


titular = {"nome": "A", "email": "a@x.com"}

# ---- Mercado Pago fora do ar NÃO pode expirar o pedido
def falha(*a, **k):
    raise TimeoutError("MP lento")


pedidos._buscar_pagamentos_pix_aprovados = falha
cur = Cur()
res = pedidos._verificar_com_expiracao(cur, pedido(minutos=30), titular)
check("MP fora do ar + pedido vencido -> continua pendente", res == "pendente")
check("MP fora do ar -> nenhum UPDATE de cancelamento", not any("cancelado" in q for q in cur.sqls))

try:
    pedidos._verificar_e_confirmar_pagamento(Cur(), pedido(), titular)
    levantou = False
except pedidos.PagamentoIndisponivel:
    levantou = True
check("falha de consulta levanta PagamentoIndisponivel (não vira 'pendente' mudo)", levantou)

# ---- MP respondeu e ninguém pagou -> expira normalmente
pedidos._buscar_pagamentos_pix_aprovados = lambda desde, usar_cache=True: []
cur = Cur()
res = pedidos._verificar_com_expiracao(cur, pedido(minutos=30), titular)
check("MP respondeu, ninguém pagou, vencido -> cancelado", res == "cancelado")
check("MP respondeu, ainda dentro do prazo -> continua pendente",
      pedidos._verificar_com_expiracao(Cur(), pedido(minutos=1), titular) == "pendente")

# ---- só Pix aprovado DEPOIS do pedido confirma
iso = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
p = pedido(minutos=3)
antigo = {"id": 111, "status": "approved", "payment_method_id": "pix", "transaction_amount": 30.0,
          "date_approved": iso(p["data_pedido"] - timedelta(minutes=1))}
novo = {"id": 222, "status": "approved", "payment_method_id": "pix", "transaction_amount": 30.0,
        "date_approved": iso(p["data_pedido"] + timedelta(seconds=20))}
e._enviar = lambda *a, **k: None
pedidos.enviar_confirmacao_pedido = lambda *a, **k: None

pedidos._buscar_pagamentos_pix_aprovados = lambda desde, usar_cache=True: [antigo]
cur = Cur()
res = pedidos._tentar_conciliar_pix_direto(cur, p, titular)
check("Pix aprovado 1 min ANTES do pedido não confirma", res == "pendente" and not any("confirmado" in q for q in cur.sqls))

pedidos._buscar_pagamentos_pix_aprovados = lambda desde, usar_cache=True: [antigo, novo]
cur = Cur()
res = pedidos._tentar_conciliar_pix_direto(cur, p, titular)
check("Pix aprovado DEPOIS do pedido confirma", res == "confirmado")

sem_data = dict(novo, id=333)
sem_data.pop("date_approved")
pedidos._buscar_pagamentos_pix_aprovados = lambda desde, usar_cache=True: [sem_data]
check("sem date_approved no pagamento segue valendo (não trava a conciliação)",
      pedidos._tentar_conciliar_pix_direto(Cur(), p, titular) == "confirmado")

# ---- valor no aviso do admin
enviados = []
e._enviar = lambda d, a, c, h=None: enviados.append((d, a, c))
e._destinatarios_admin = lambda: [{"email": "a@x.com", "nome": "A"}]


class Inline:
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


e.threading.Thread = Inline
e.avisar_admins_pedido_novo(5, 2, 50.0, "G", "g@x.com")
check("aviso de pedido formata R$ 50.00 (e não 50.0)", enviados[0][1] == "Novo pedido #5 — 2 ingresso(s) · R$ 50.00")

# ---- e-mail em massa em segundo plano
enviados.clear()
e.enviar_email_em_massa_em_segundo_plano(
    [{"email": "x@x.com", "nome": "X"}, {"email": "y@x.com", "nome": "Y"}], "Assunto", "Texto")
check("massa: 1 e-mail por destinatário", [d for d, *_ in enviados] == ["x@x.com", "y@x.com"])


def quebra_em_y(d, a, c, h=None):
    if d == "y@x.com":
        raise RuntimeError("boom")
    enviados.append((d, a, c))


enviados.clear()
e._enviar = quebra_em_y
e.enviar_email_em_massa_em_segundo_plano(
    [{"email": "x@x.com", "nome": "X"}, {"email": "y@x.com", "nome": "Y"}, {"email": "z@x.com", "nome": "Z"}], "A", "T")
check("massa: falha de um destinatário não derruba os outros", [d for d, *_ in enviados] == ["x@x.com", "z@x.com"])

# ---- defaults e sessão
check("remetente padrão (no código) é Back to Halloween", '"BREVO_FROM_NOME", "Back to Halloween"' in open(config.__file__, encoding="utf-8").read())
import app as appmod  # noqa: E402
check("sessão permanente de 14 dias", appmod.app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(days=14))
import pix_copia_cola as pc  # noqa: E402
check("Pix usa o nome Back to Halloween", "BACK TO HALLOWEEN" in pc.gerar_copia_cola("chave", 30.0))
print("\ntudo certo")
