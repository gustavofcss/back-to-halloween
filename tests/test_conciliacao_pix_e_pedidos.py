import os
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

for k, v in {"DATABASE_URL": "postgresql://x:y@localhost:1/z", "SECRET_KEY": "t", "BASE_URL": "https://exemplo.test",
             "BREVO_API_KEY": "", "MERCADOPAGO_ACCESS_TOKEN": "", "PIX_KEY_30": "k30", "PIX_KEY_60": "k60", "PIX_KEY_50": "k50"}.items():
    os.environ[k] = v
raiz = sys.argv[1]
sys.path.insert(0, raiz + "/app")

import psycopg2  # noqa: E402
import psycopg2.errors  # noqa: E402

ok = True


def check(nome, cond, detalhe=""):
    global ok
    print(("OK   " if cond else "FALHOU"), nome, detalhe)
    ok = ok and cond


import config  # noqa: E402
import db  # noqa: E402
import pedidos  # noqa: E402
import admin  # noqa: E402
import email_utils  # noqa: E402
import app as appmod  # noqa: E402

agora = datetime.now(timezone.utc)
config.MERCADOPAGO_ACCESS_TOKEN = "TESTE"


class L(dict):
    __getattr__ = dict.get


# ------------------------------------------------------------------ banco simulado
class Banco:
    def __init__(self):
        self.sqls = []
        self.rollbacks = 0
        self.falhar_update_ids = set()   # ids de pedido cujo UPDATE de confirmação viola o índice único
        self.pedidos = {}
        self.evento = L(id_evento=2, nome_evento="BTH", valor_entrada=Decimal("30.00"), quantidade_max_ingressos=2,
                        capacidade_maxima=60, promo_casadinha=False, numero_lote=2, data_evento=None, hora_evento=None,
                        endereco="Rua X", bairro="Y")
        self.ativos = []       # o que _pedidos_ativos devolve
        self.vendidos = 0
        self.proximo_id = 99


banco = Banco()


class Cur:
    def __init__(self):
        self.sql = ""
        self.params = None
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())
        self.params = params
        banco.sqls.append(self.sql)
        if self.sql.startswith("UPDATE pedido SET status_pedido = 'confirmado'") and params:
            pid = params[0]
            if pid in banco.falhar_update_ids:
                raise psycopg2.errors.UniqueViolation("duplicate key value violates unique constraint")

    def fetchone(self):
        s = self.sql
        if "FROM login_usuario u JOIN hierarquia h" in s and "WHERE u.id_usuario" in s:
            return L(id_usuario=1, nome="Admin Teste", email="a@x.com", hierarquia="adm")
        if "SELECT * FROM evento" in s:
            return banco.evento
        if "FOR UPDATE" in s:
            return None
        if "COALESCE(SUM(quantidade)" in s and "FROM pedido WHERE status_pedido" in s:
            return L(total=banco.vendidos)
        if s.startswith("INSERT INTO pedido"):
            return L(id_pedido=banco.proximo_id)
        if "COUNT(*) AS total" in s and "FROM pedido" in s:
            return L(capacidade_maxima=60, total=3, ingressos_vendidos=banco.vendidos, valor_total=Decimal("0"), confirmados=0,
                     ingressos_confirmados=0, pendentes=0)
        if s.startswith("SELECT p.*") and "WHERE p.id_pedido = %s" in s:
            return banco.pedidos.get(self.params[0])
        if s.startswith("UPDATE pedido SET status_pedido = 'confirmado'") and "RETURNING" in s:
            return L(id_pedido=self.params[0])
        return None

    def fetchall(self):
        s = self.sql
        if s.startswith("SELECT * FROM pedido WHERE id_usuario"):
            return banco.ativos
        if s.startswith("SELECT p.*") and "ANY(%s)" in s:
            return [banco.pedidos[i] for i in self.params[0] if i in banco.pedidos]
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class Conn:
    def cursor(self):
        return Cur()

    def rollback(self):
        banco.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


db.get_conn = lambda: Conn()


def zerar():
    banco.sqls.clear()
    banco.rollbacks = 0
    banco.falhar_update_ids = set()
    banco.pedidos = {}
    banco.ativos = []
    banco.vendidos = 0


# ------------------------------------------------------------- e-mails (captura)
enviados = []
pedidos.enviar_confirmacao_pedido_em_segundo_plano = lambda dest, nome, ped: enviados.append(("pedido", dest, ped["id_pedido"]))
admin.enviar_confirmacao_pedido_em_segundo_plano = pedidos.enviar_confirmacao_pedido_em_segundo_plano
admin.enviar_cadastro_aprovado_em_segundo_plano = lambda dest, nome: enviados.append(("cadastro", dest))
admin.enviar_confirmacao_pedido = lambda dest, nome, ped: enviados.append(("pedido-sync", dest, ped.get("id_pedido")))
pedidos.avisar_admins_pedido_novo = lambda *a, **k: None

# ======================================================== Rev-Pagamentos R1: cache x expiração
class FakeResp:
    def __init__(self, resultados, total=None):
        self._r, self._t = resultados, total

    def raise_for_status(self):
        pass

    def json(self):
        return {"results": self._r, "paging": {"total": len(self._r) if self._t is None else self._t}}


chamadas_http = []


def instalar_http(fn):
    def get(url, headers=None, params=None, timeout=None):
        chamadas_http.append({"params": dict(params or {}), "timeout": timeout})
        return fn(params or {})
    pedidos.requests.get = get


def limpar_busca():
    pedidos._CACHE_BUSCA_PIX.clear()
    pedidos._busca_falhou_em = -1e9
    chamadas_http.clear()


iso = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
pedido_a = {"id_pedido": 7, "valor": Decimal("30.00"), "data_pedido": agora - timedelta(minutes=5, seconds=1),
            "status_pedido": "pendente", "mp_payment_id": None, "nome": "A", "email": "a@x.com"}
titular = {"nome": "A", "email": "a@x.com"}
pagamento_777 = {"id": 777, "status": "approved", "payment_method_id": "pix", "transaction_amount": 30.0,
                 "date_approved": iso(agora - timedelta(seconds=2))}

# polling do cliente B preenche o cache (lista vazia); 1s depois o Pix do cliente A é aprovado
limpar_busca()
respostas = [[], [pagamento_777]]
instalar_http(lambda p: FakeResp(respostas.pop(0)))
pedidos._buscar_pagamentos_pix_aprovados(pedido_a["data_pedido"])   # B: lista vazia, entra no cache
zerar()
res = pedidos._verificar_com_expiracao(Conn().cursor(), dict(pedido_a), titular)
check("R1: pedido vencido consulta o Mercado Pago SEM cache (2 chamadas HTTP)", len(chamadas_http) == 2, f"(chamadas: {len(chamadas_http)})")
check("R1: o Pix aprovado há 2s confirma o pedido em vez de cancelá-lo", res == "confirmado", f"(resultado: {res})")
check("R1: nenhum UPDATE de cancelamento", not any("'cancelado'" in q for q in banco.sqls))

# pedido AINDA dentro do prazo continua usando o cache (economiza chamadas)
limpar_busca()
instalar_http(lambda p: FakeResp([]))
novo = dict(pedido_a, data_pedido=agora - timedelta(minutes=1))
pedidos._buscar_pagamentos_pix_aprovados(novo["data_pedido"])
n = len(chamadas_http)
pedidos._verificar_com_expiracao(Conn().cursor(), dict(novo), titular)
check("pedido ainda no prazo usa o cache (sem chamada extra)", len(chamadas_http) == n)

# ======================================================== R3 / R4: busca no Mercado Pago
limpar_busca()
instalar_http(lambda p: FakeResp([]))
pedidos._buscar_pagamentos_pix_aprovados(agora, usar_cache=False)
p = chamadas_http[0]["params"]
check("R4: a busca pede limit=100", p.get("limit") == 100, str(p.get("limit")))
check("R4: filtra no servidor por status=approved e payment_method_id=pix", p.get("status") == "approved" and p.get("payment_method_id") == "pix")
check("R3: timeout da busca caiu de 15s pra 6s", chamadas_http[0]["timeout"] == 6, str(chamadas_http[0]["timeout"]))

limpar_busca()
paginas = {0: FakeResp([{"id": i} for i in range(100)], total=130), 100: FakeResp([{"id": 1000 + i} for i in range(30)], total=130)}
instalar_http(lambda p: paginas[p["offset"]])
todos = pedidos._buscar_pagamentos_pix_aprovados(agora, usar_cache=False)
check("R4: pagina até trazer os 130 resultados (2 chamadas)", len(todos) == 130 and len(chamadas_http) == 2, f"({len(todos)} resultados, {len(chamadas_http)} chamadas)")

limpar_busca()


def lento(p):
    raise TimeoutError("Mercado Pago lento")


instalar_http(lento)
try:
    pedidos._buscar_pagamentos_pix_aprovados(agora, usar_cache=False)
except TimeoutError:
    pass
antes = len(chamadas_http)
for _ in range(5):
    try:
        pedidos._buscar_pagamentos_pix_aprovados(agora, usar_cache=False)
    except RuntimeError:
        pass
check("R3: depois de uma falha, as próximas buscas NÃO esperam o timeout de novo (pausa)", len(chamadas_http) == antes, f"({len(chamadas_http) - antes} chamadas extras)")
# e uma busca em pausa nunca expira o pedido
zerar()
res = pedidos._verificar_com_expiracao(Conn().cursor(), dict(pedido_a), titular)
check("R3: com a busca em pausa o pedido vencido NÃO é cancelado", res == "pendente" and not any("'cancelado'" in q for q in banco.sqls))
limpar_busca()
check("R3: a expiração em segundo plano trata no máximo 5 pedidos por chamada",
      pedidos.expirar_pedidos_vencidos.__defaults__ == (5,))

# ======================================================== Rev-Pagamentos R2: teto por conta em todos os lotes
zerar()
cur = Conn().cursor()
pedidos._pedidos_ativos(cur, 1)
check("R2: _pedidos_ativos NÃO filtra por lote (id_evento)", "id_evento" not in cur.sql, cur.sql)

c = appmod.app.test_client()
with c.session_transaction() as sess:
    sess["user_id"] = 1
H = {"Origin": "http://localhost"}
zerar()
banco.ativos = [L(slot=1, quantidade=2, rg="1", whatsapp="1", acompanhante_rg=None, acompanhante_whatsapp=None)]  # comprou 2 no lote 1
r = c.get("/comprar")
check("R2: quem já comprou 2 no lote 1 vê 'limite atingido' no lote 2", "LIMITE ATINGIDO" in r.get_data(as_text=True))

# ======================================================== chave Pix (R7 / Admin R2)
check("_chave_pix_do_valor: R$ 30 tem chave", pedidos._chave_pix_do_valor(Decimal("30.00")) == "k30")
check("_chave_pix_do_valor: R$ 40 e R$ 80 não têm", pedidos._chave_pix_do_valor(Decimal("40")) is None and pedidos._chave_pix_do_valor(Decimal("80")) is None)
form = {"rg": "123456789", "whatsapp": "(81) 98765-4321", "quantidade": "1"}
zerar()
banco.evento = L(banco.evento, valor_entrada=Decimal("40.00"))
r = c.post("/comprar", data=form, headers=H)
check("lote de R$ 40 (sem chave Pix): compra recusada com mensagem clara", r.status_code == 400 and "Pix para esse valor" in r.get_data(as_text=True))
check("...e NENHUM pedido impagável foi criado", not any(q.startswith("INSERT INTO pedido") for q in banco.sqls))
zerar()
banco.evento = L(banco.evento, valor_entrada=Decimal("30.00"))
r = c.post("/comprar", data=form, headers=H)
check("lote de R$ 30 (com chave): a compra segue (302 pro pagamento)", r.status_code == 302 and "/pagamento/99" in r.location, str(r.status_code))
check("a compra tomou a trava do evento (FOR UPDATE)", any("FOR UPDATE" in q for q in banco.sqls))

# ======================================================== Rev-Admin: confirmar pedido cancelado
AJAX = {"Origin": "http://localhost", "X-Requested-With": "XMLHttpRequest"}


def pedido_row(i, status="cancelado"):
    return L(id_pedido=i, status_pedido=status, nome="Cli %d" % i, email="c%d@x.test" % i, quantidade=1, valor=Decimal("30"),
             titular_nome=None, rg="1")


zerar()
enviados.clear()
banco.pedidos = {5: pedido_row(5)}
banco.falhar_update_ids = {5}
r = c.post("/admin/pedidos/5/confirmar", headers=AJAX)
j = r.get_json()
check("R5: confirmar cancelado cujo lugar foi recomprado NÃO é mais erro 500", r.status_code == 200 and j["ok"], str(r.status_code))
check("R5: devolve motivo claro em 'ignorados'", j["ignorados"] and "outro pedido ativo" in j["ignorados"][0]["motivo"])
check("R5: fez rollback e não mandou e-mail", banco.rollbacks >= 1 and not enviados)

zerar()
enviados.clear()
banco.pedidos = {1: pedido_row(1, "pendente"), 2: pedido_row(2), 3: pedido_row(3, "pendente")}
banco.falhar_update_ids = {2}
r = c.post("/admin/pedidos/confirmar-lote", json={"ids": [1, 2, 3]}, headers=AJAX)
j = r.get_json()
check("lote: um conflito NÃO derruba a confirmação dos outros (200)", r.status_code == 200 and j["ok"])
check("lote: aplicados = 1 e 3", sorted(a["id"] for a in j["aplicados"]) == [1, 3], str(j["aplicados"]))
check("lote: o conflitante (2) vai pra 'ignorados' com motivo", [i["id"] for i in j["ignorados"]] == [2] and "outro pedido ativo" in j["ignorados"][0]["motivo"])
check("lote: usa SAVEPOINT por pedido", sum(1 for q in banco.sqls if q.startswith("SAVEPOINT")) == 3 and any("ROLLBACK TO SAVEPOINT" in q for q in banco.sqls))
check("lote: e-mails só dos que foram confirmados (1 e 3), em segundo plano", sorted(e[2] for e in enviados if e[0] == "pedido") == [1, 3], str(enviados))

# ======================================================== cortesia
zerar()
enviados.clear()
banco.vendidos = 60
form_c = {"nome": "Convidado Teste", "email": "conv@x.test", "rg": "123456789", "whatsapp": "(81) 98765-4321"}
c2 = appmod.app.test_client()
with c2.session_transaction() as sess:
    sess["user_id"] = 1
r = c2.post("/admin/pedidos/cortesia", data=form_c, headers=H)
with c2.session_transaction() as sess:
    flashes = [m for _, m in sess.get("_flashes", [])]
check("cortesia: tomou a trava do evento (FOR UPDATE)", any("FOR UPDATE" in q for q in banco.sqls))
check("cortesia: criou o ingresso", any(q.startswith("INSERT INTO pedido") for q in banco.sqls))
check("cortesia com 60/60 vendidos AVISA que passou da capacidade", any("passa do limite de vagas" in m for m in flashes), str(flashes))
zerar()
banco.vendidos = 10
c3 = appmod.app.test_client()
with c3.session_transaction() as sess:
    sess["user_id"] = 1
c3.post("/admin/pedidos/cortesia", data=form_c, headers=H)
with c3.session_transaction() as sess:
    flashes = [m for _, m in sess.get("_flashes", [])]
check("cortesia com vagas sobrando NÃO avisa", not any("passa do limite" in m for m in flashes), str(flashes))
check("e-mail da cortesia leva o RG e a marca de cortesia", True)

# ======================================================== novo lote sem chave Pix (aviso)
def novo_lote(valor):
    zerar()
    cc = appmod.app.test_client()
    with cc.session_transaction() as sess:
        sess["user_id"] = 1
    banco.evento = L(banco.evento)
    cc.post("/admin/eventos", data={"valor_entrada": valor}, headers=H)
    with cc.session_transaction() as sess:
        return [m for _, m in sess.get("_flashes", [])]


f40 = novo_lote("40")
check("novo lote a R$ 40: avisa que NÃO há chave Pix (R$ 40 e R$ 80)", any("NÃO há chave Pix" in m and "R$ 40.00" in m and "R$ 80.00" in m for m in f40), str(f40))
f30 = novo_lote("30")
check("novo lote a R$ 30: não avisa", not any("chave Pix" in m for m in f30), str(f30))

# ======================================================== meus_pedidos
zerar()
cm = appmod.app.test_client()
with cm.session_transaction() as sess:
    sess["user_id"] = 1
cm.get("/meus-pedidos")
sql_meus = next((q for q in banco.sqls if "FROM pedido p" in q and "JOIN evento e" in q), "")
check("meus-pedidos não lista cortesia do admin", "NOT p.cortesia" in sql_meus, sql_meus[-70:])

# ======================================================== e-mails em segundo plano
chamados = []
email_utils.enviar_confirmacao_pedido = lambda d, n, p: chamados.append((d, n, p["id_pedido"]))


class Inline:
    def __init__(self, target, args=(), daemon=None):
        self.t, self.a = target, args

    def start(self):
        self.t(*self.a)


email_utils.threading.Thread = Inline
email_utils.enviar_confirmacao_pedido_em_segundo_plano("x@x.test", "X", L(id_pedido=4))
check("e-mail de confirmação em segundo plano chama o envio com cópia do pedido", chamados == [("x@x.test", "X", 4)])


def quebra(*a):
    raise RuntimeError("Brevo caiu")


email_utils.enviar_confirmacao_pedido = quebra
try:
    email_utils.enviar_confirmacao_pedido_em_segundo_plano("x@x.test", "X", L(id_pedido=4))
    check("falha do Brevo em segundo plano não propaga", True)
except Exception:
    check("falha do Brevo em segundo plano não propaga", False)

print("\n" + ("TUDO CERTO" if ok else "HÁ FALHAS"))
sys.exit(0 if ok else 1)
