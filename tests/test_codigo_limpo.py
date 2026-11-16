import os
import re
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost:1/z")
os.environ.setdefault("SECRET_KEY", "t")
raiz = Path(sys.argv[1])
app = raiz / "app"
sys.path.insert(0, str(app))

import app as appmod  # noqa: E402

ok = True


def check(nome, cond, detalhe=""):
    global ok
    print(("OK   " if cond else "FALHOU"), nome, detalhe)
    ok = ok and cond


# 1) todo template compila
env = appmod.app.jinja_env
falhas = []
for f in sorted((app / "templates").rglob("*.html")):
    nome = f.relative_to(app / "templates").as_posix()
    try:
        env.get_template(nome)
    except Exception as e:  # noqa: BLE001
        falhas.append(f"{nome}: {e}")
check("todos os templates compilam", not falhas, "; ".join(falhas))

# 2) todo arquivo estático citado existe
refs = set()
for f in list((app / "templates").rglob("*.html")):
    t = f.read_text(encoding="utf-8")
    refs |= set(re.findall(r"filename=['\"]([^'\"]+)['\"]", t))
    refs |= set(re.findall(r"/static/([\w./-]+)", t))
for f in (app / "static" / "css").glob("*.css"):
    t = f.read_text(encoding="utf-8")
    for u in re.findall(r"url\(([^)]+)\)", t):
        u = u.strip("'\" ")
        if u.startswith("data:") or u.startswith("http"):
            continue
        refs.add(("css", f.name, u))
faltando = []
for r in refs:
    if isinstance(r, tuple):
        _, css, u = r
        alvo = (app / "static" / "css" / u).resolve()
        if not alvo.exists():
            faltando.append(f"{css}: {u}")
    elif "{{" not in r and not (app / "static" / r).exists():
        faltando.append(r)
check("todo arquivo estático citado existe", not faltando, "faltam: " + ", ".join(faltando) if faltando else f"({len(refs)} referências)")

# 3) as páginas públicas respondem sem 500 e sem 404 de asset
c = appmod.app.test_client()
import db  # noqa: E402


class Cur:
    rowcount = 0

    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return None

    def fetchall(self):
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


db.get_conn = lambda: Conn()
for rota in ["/", "/login", "/signup", "/esqueci-senha", "/redefinir-senha", "/confirmar-email"]:
    r = c.get(rota)
    check(f"GET {rota} -> {r.status_code}", r.status_code == 200)
    if rota == "/":
        html = r.get_data(as_text=True)
        scripts = re.findall(r'src="(/static/js/[^"]+)"', html)
        check("home carrega só scripts que existem", all((app / p.split("?")[0].lstrip("/")).exists() for p in scripts), str(scripts))
        check("home carrega vhs.js uma vez só", html.count("js/vhs.js") == 1)
        check("home não referencia mais o convite.js", "convite.js" not in html)

# 4) filtro de data continua registrado e correto
from datetime import datetime, timezone  # noqa: E402

f = appmod.app.jinja_env.filters["data_hora_br"]
check("filtro data_hora_br: UTC 03:00 -> Brasília 00:00", f(datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc)) == "05/10/2026 00:00")
check("filtro data_hora_br: vazio -> '--'", f(None) == "--")

print("\n" + ("TUDO CERTO" if ok else "HÁ FALHAS"))
sys.exit(0 if ok else 1)
