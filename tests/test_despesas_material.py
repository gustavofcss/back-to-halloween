import os
import re
import sys
from datetime import date
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost:1/z")
os.environ.setdefault("SECRET_KEY", "t")
sys.path.insert(0, sys.argv[1] + "/app")

import admin  # noqa: E402
import app as appmod  # noqa: E402
from flask import get_flashed_messages  # noqa: E402


def check(nome, cond):
    print(("OK   " if cond else "FALHOU"), nome)
    if not cond:
        sys.exit(1)


# ---------- rota /admin/material/<id>/editar com banco simulado ----------
executados = []
rowcount = {"v": 1}


class Cur:
    @property
    def rowcount(self):
        return rowcount["v"]

    def execute(self, sql, params=None):
        executados.append((sql, params))

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


admin.db.get_conn = lambda: Conn()
view = admin.material_editar
while hasattr(view, "__wrapped__"):
    view = view.__wrapped__


def chamar(form):
    executados.clear()
    with appmod.app.test_request_context("/admin/material/5/editar", method="POST", data=form):
        resp = view(5)
        return resp, get_flashed_messages()


r, msgs = chamar({"nome_material": " Teias ", "valor": "12,5", "descricao": "  roxas "})
check("edição válida faz UPDATE com valores limpos",
      len(executados) == 1 and executados[0][1] == ("Teias", Decimal("12.50"), "roxas", 5))
check("edição válida redireciona pra /admin/material", r.status_code == 302 and r.location.endswith("/admin/material"))
check("edição válida avisa 'Material atualizado.'", msgs == ["Material atualizado."])

r, msgs = chamar({"nome_material": "", "valor": "10"})
check("nome vazio não toca no banco", executados == [] and "Informe o nome" in msgs[0])
r, msgs = chamar({"nome_material": "x", "valor": "-3"})
check("valor negativo não toca no banco", executados == [])
r, msgs = chamar({"nome_material": "x", "valor": "abc"})
check("valor inválido não toca no banco", executados == [])
r, msgs = chamar({"nome_material": "x" * 161, "valor": "5"})
check("nome com 161 caracteres recusado", executados == [])

rowcount["v"] = 0
r, msgs = chamar({"nome_material": "x", "valor": "5"})
check("id inexistente avisa 'não encontrado'", msgs == ["Material não encontrado."])

# ---------- tela de material renderizada ----------
t = appmod.app.jinja_env.get_template("admin/material.html")
mats = [{"id": 7, "nome_material": 'Teia "<b>"', "valor": Decimal("12.50"), "descricao": None,
         "data_compra": date(2026, 10, 5), "cadastrado_por": "Gustavo"}]
with appmod.app.test_request_context("/admin/material"):
    appmod.app.update_template_context  # noqa: B018
    html = t.render(materiais=mats, total_arrecadado="0.00", total_gasto="12.50", saldo="-12.50",
                    usuario=None, nav_counts=None, url_for=__import__("flask").url_for)
check("card tem botão Editar", "data-material-editar" in html)
check("card tem formulário de edição apontando pra /editar", 'action="/admin/material/7/editar"' in html)
check("nome com aspas/HTML é escapado no value", 'value="Teia &#34;&lt;b&gt;&#34;"' in html)
check("descrição vazia vira campo vazio (não '—')", re.search(r'name="descricao" class="input" value=""', html) is not None)
check("botão Apagar continua", "/admin/material/7/apagar" in html)
check("botão Atualizar no cabeçalho do admin", "data-atualizar-pagina" in html)

# ---------- Meus pedidos ----------
t2 = appmod.app.jinja_env.get_template("meus_pedidos.html")
with appmod.app.test_request_context("/meus-pedidos"):
    html2 = t2.render(pedidos=[], pode_comprar_mais=0, usuario=None, tem_pedido=False)
check("Meus pedidos tem botão Atualizar", "data-atualizar-pagina" in html2)
print("\ntudo certo")
