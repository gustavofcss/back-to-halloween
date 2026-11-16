import os
import sys
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost:1/z")
os.environ.setdefault("SECRET_KEY", "t")
sys.path.insert(0, sys.argv[1] + "/app")

import config  # noqa: E402
import email_utils as e  # noqa: E402


def check(nome, cond):
    print(("OK   " if cond else "FALHOU"), nome)
    if not cond:
        sys.exit(1)


enviados = []
e._enviar = lambda dest, assunto, corpo, html=None: enviados.append((dest, assunto, corpo, html))
e._destinatarios_admin = lambda: [{"email": "a@x.com", "nome": "A"}, {"email": "b@x.com", "nome": "B"}]


class Inline:
    """Roda a thread na hora, pra o teste poder conferir o resultado."""

    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


e.threading.Thread = Inline
config.BASE_URL = "https://site.exemplo"

# ---- cadastro pendente
e.avisar_admins_cadastro_pendente('Ana <b>X</b>', "ana@x.com")
check("cadastro: 1 e-mail por admin (2)", [d for d, *_ in enviados] == ["a@x.com", "b@x.com"])
dest, assunto, corpo, html = enviados[0]
check("cadastro: assunto", assunto == "Novo cadastro aguardando aprovação — Back to Halloween")
check("cadastro: texto puro traz nome, e-mail e link", "Ana <b>X</b> (ana@x.com)" in corpo and "https://site.exemplo/admin/cadastros" in corpo)
check("cadastro: HTML escapa o nome", "Ana &lt;b&gt;X&lt;/b&gt;" in html and "<b>X</b>" not in html)
check("cadastro: botão leva pra /admin/cadastros", 'href="https://site.exemplo/admin/cadastros"' in html)

# ---- pedido novo
enviados.clear()
e.avisar_admins_pedido_novo(66, 2, Decimal("50.00"), "Gustavo", "g@x.com", acompanhante_nome="Maria")
dest, assunto, corpo, html = enviados[0]
check("pedido: assunto com número, quantidade e valor", assunto == "Novo pedido #66 — 2 ingresso(s) · R$ 50.00")
check("pedido: comprador e acompanhante no corpo", "Comprador: Gustavo (g@x.com)" in corpo and "Acompanhante: Maria" in corpo)
check("pedido: sem 'Ingresso para' quando é o próprio comprador", "Ingresso para" not in corpo)
check("pedido: botão leva pra /admin/pedidos", 'href="https://site.exemplo/admin/pedidos"' in html)

enviados.clear()
e.avisar_admins_pedido_novo(70, 1, Decimal("30.00"), "Gustavo", "g@x.com", titular_nome="Joana")
check("pedido de outra pessoa mostra 'Ingresso para'", "Ingresso para: Joana" in enviados[0][2])

# ---- falhas nunca propagam
def quebra(*a, **k):
    raise RuntimeError("Brevo fora do ar")


e._enviar = quebra
enviados.clear()
try:
    e.avisar_admins_pedido_novo(1, 1, Decimal("30"), "x", "x@x.com")
    e.avisar_admins_cadastro_pendente("x", "x@x.com")
    ok = True
except Exception:
    ok = False
check("falha no envio não propaga (cadastro/compra seguem)", ok)

e._destinatarios_admin = quebra
try:
    e.avisar_admins_cadastro_pendente("x", "x@x.com")
    ok = True
except Exception:
    ok = False
check("falha ao listar admins não propaga", ok)

# ---- a chamada nos fluxos reais
import auth  # noqa: E402
import pedidos  # noqa: E402

check("auth.py usa avisar_admins_cadastro_pendente", hasattr(auth, "avisar_admins_cadastro_pendente"))
check("pedidos.py usa avisar_admins_pedido_novo", hasattr(pedidos, "avisar_admins_pedido_novo"))
print("\ntudo certo")
