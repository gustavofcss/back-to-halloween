import os
import sys
from datetime import datetime, timedelta, timezone

for k, v in {"DATABASE_URL": "postgresql://x:y@localhost:1/z", "SECRET_KEY": "t", "BASE_URL": "https://exemplo.test",
             "BREVO_API_KEY": "", "MERCADOPAGO_ACCESS_TOKEN": "", "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""}.items():
    os.environ[k] = v
raiz = sys.argv[1]
sys.path.insert(0, raiz + "/app")

from werkzeug.security import check_password_hash, generate_password_hash  # noqa: E402

ok = True


def check(nome, cond, detalhe=""):
    global ok
    print(("OK   " if cond else "FALHOU"), nome, detalhe)
    ok = ok and cond


import db  # noqa: E402
import auth  # noqa: E402
import limitador  # noqa: E402
import app as appmod  # noqa: E402
from authlib.integrations.base_client.errors import OAuthError  # noqa: E402


class L(dict):
    __getattr__ = dict.get


agora = datetime.now(timezone.utc)

# ------------------------------------------------------------------ banco simulado com regras
regras = []     # (trecho do SQL, resultado do fetchone | função)
sqls = []


def regra(trecho, valor):
    regras.append((trecho, valor))


def zerar():
    regras.clear()
    sqls.clear()
    limitador.limpar()


class Cur:
    def __init__(self):
        self.sql = ""
        self.params = None
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())
        self.params = params
        sqls.append(self.sql)

    def fetchone(self):
        for trecho, valor in regras:
            if trecho in self.sql:
                return valor(self) if callable(valor) else valor
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

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


db.get_conn = lambda: Conn()
auth.avisar_admins_cadastro_pendente = lambda *a, **k: None

# ========================================================== _next_seguro (open redirect)
bons = ["/comprar", "/comprar?x=1&y=2", "/admin/pedidos#a", "/pagamento/12"]
ruins = ["//evil.example", "/\\evil.example", "/\t/evil.example", "/\n/evil.example", "/\r\n/evil.example", "/ /evil", "http://evil.example",
         "https://evil.example/x", "javascript:alert(1)", "", None, "x", "/" + "a" * 400, "/a\x00b", "/a\tb", "/a\\b", "/%09/ok-mas-texto"]
for n in bons:
    check(f"next aceita {n!r}", auth._next_seguro(n) == n)
for n in ruins[:-1]:
    check(f"next recusa {n!r}"[:60], auth._next_seguro(n) is None)
check("next com %09 (texto, não TAB) é só um caminho comum", auth._next_seguro("/%09/ok-mas-texto") == "/%09/ok-mas-texto")

# ========================================================== limitador (unidade)
import time as _t  # noqa: E402

relogio = {"t": 1000.0}
limitador.time.monotonic = lambda: relogio["t"]
limitador.limpar()
res = [limitador.estourou("a", 3, 60) for _ in range(4)]
check("limitador: 3 passam, a 4ª estoura", res == [False, False, False, True], str(res))
relogio["t"] += 61
check("limitador: depois da janela volta a liberar", limitador.estourou("a", 3, 60) is False)
check("limitador: chaves diferentes não se misturam", limitador.estourou("b", 3, 60) is False)
limitador.time.monotonic = _t.monotonic

with appmod.app.test_request_context("/login", headers={"CF-Connecting-IP": "1.1.1.1", "X-Forwarded-For": "2.2.2.2, 3.3.3.3"}):
    check("IP: usa CF-Connecting-IP primeiro", limitador.ip_do_cliente() == "1.1.1.1")
with appmod.app.test_request_context("/login", headers={"X-Forwarded-For": "2.2.2.2, 3.3.3.3"}):
    check("IP: depois X-Forwarded-For (o primeiro)", limitador.ip_do_cliente() == "2.2.2.2")
with appmod.app.test_request_context("/login", environ_base={"REMOTE_ADDR": "9.9.9.9"}):
    check("IP: por último o remote_addr", limitador.ip_do_cliente() == "9.9.9.9")

# ========================================================== limitador nas rotas
H = {"Origin": "http://localhost"}
zerar()
c = appmod.app.test_client()
codigos = [c.post("/login", data={"email": "x@x.test", "senha": "errada"}, headers={**H, "CF-Connecting-IP": "5.5.5.5"}).status_code for _ in range(12)]
check("login: 10 tentativas passam (401) e a 11ª e a 12ª tomam 429", codigos == [401] * 10 + [429, 429], str(codigos))
check("login: GET continua livre mesmo bloqueado", c.get("/login", headers={"CF-Connecting-IP": "5.5.5.5"}).status_code == 200)
check("login: outro IP não é afetado", c.post("/login", data={"email": "x@x.test", "senha": "e"}, headers={**H, "CF-Connecting-IP": "6.6.6.6"}).status_code == 401)
r = c.post("/login", data={"email": "x@x.test", "senha": "e"}, headers={**H, "CF-Connecting-IP": "5.5.5.5"})
check("a página 429 explica e tem o cabeçalho de segurança", r.status_code == 429 and "Muitas tentativas" in r.get_data(as_text=True))

zerar()
form_signup = {"nome": "Ana Souza", "email": "ana@x.test", "senha": "123456"}
cods = [c.post("/signup", data=form_signup, headers={**H, "CF-Connecting-IP": "7.7.7.7"}).status_code for _ in range(7)]
check("signup: só 5 por minuto por IP (o resto 429)", cods.count(429) == 2 and 429 not in cods[:5], str(cods))
for rota, maximo in [("/esqueci-senha", 5), ("/confirmar-email/reenviar", 5), ("/confirmar-email/mudar-email", 5), ("/confirmar-email", 10), ("/redefinir-senha", 10)]:
    zerar()
    cods = [c.post(rota, data={"email": "a@x.test", "codigo": "123456", "senha": "novasenha", "novo_email": "n@x.test", "email_atual": "a@x.test"},
                   headers={**H, "CF-Connecting-IP": "8.8.8.8"}).status_code for _ in range(maximo + 1)]
    check(f"{rota}: a {maximo + 1}ª tentativa no minuto toma 429", cods[-1] == 429 and 429 not in cods[:-1], str(cods[-2:]))

# ========================================================== cabeçalhos / tamanho / sessão
zerar()
r = c.get("/login")
check("X-Content-Type-Options: nosniff", r.headers.get("X-Content-Type-Options") == "nosniff")
check("X-Frame-Options: DENY e frame-ancestors 'none'", r.headers.get("X-Frame-Options") == "DENY" and "frame-ancestors 'none'" in r.headers.get("Content-Security-Policy", ""))
check("Referrer-Policy definido", bool(r.headers.get("Referrer-Policy")))
check("HSTS ligado (BASE_URL é https)", "max-age=" in r.headers.get("Strict-Transport-Security", ""))
check("cabeçalhos também nos estáticos", c.get("/static/css/vhs.css").headers.get("X-Frame-Options") == "DENY")

zerar()
r = c.post("/pagamento/webhook/mercadopago", data=b"{" + b"a" * 2_000_000, content_type="application/json")
check("corpo de 2 MB no webhook público -> 413 (sem ler tudo na memória)", r.status_code == 413, str(r.status_code))
check("corpo normal no webhook continua 200", c.post("/pagamento/webhook/mercadopago", json={"type": "x"}).status_code == 200)

# conta apagada com cookie válido
zerar()
c2 = appmod.app.test_client()
with c2.session_transaction() as sess:
    sess["user_id"] = 77   # a consulta não acha ninguém (regras vazias)
r = c2.get("/meus-pedidos")
check("conta apagada com cookie válido: vai pro login (302), não dá 500", r.status_code == 302 and "/login" in r.location, str(r.status_code))
with c2.session_transaction() as sess:
    check("...e a sessão é limpa", "user_id" not in sess)
r = c2.get("/admin/pedidos")
check("admin com conta apagada também vai pro login", r.status_code == 302 and "/login" in r.location)

# revogação de sessão por troca de senha
zerar()
ts_conta = agora.replace(microsecond=0)
usuario = L(id_usuario=1, nome="Ana", email="a@x.test", data_atualizacao=ts_conta, hierarquia="cliente")
regra("FROM login_usuario u JOIN hierarquia h", usuario)
with appmod.app.test_request_context("/"):
    from flask import session
    session["user_id"] = 1
    session["sv"] = int(ts_conta.timestamp())
    check("sessão com a mesma versão da conta continua valendo", auth.current_user() is not None)
with appmod.app.test_request_context("/"):
    from flask import session
    session["user_id"] = 1
    session["sv"] = int(ts_conta.timestamp()) - 500   # logou ANTES de a senha ser redefinida
    check("sessão aberta antes da troca de senha é derrubada", auth.current_user() is None and "user_id" not in session)
with appmod.app.test_request_context("/"):
    from flask import session
    session["user_id"] = 1   # sessão antiga, anterior a este deploy (sem versão)
    check("sessão sem versão (anterior ao deploy) segue válida", auth.current_user() is not None)

# login abre a sessão com versão e o next malicioso não vira redirect externo
zerar()
senha_hash = generate_password_hash("segredo123")
regra("SELECT id_usuario, senha_hash, email_confirmado", L(id_usuario=1, senha_hash=senha_hash, email_confirmado=True, email_verificado=True))
regra("SELECT data_atualizacao FROM login_usuario", L(data_atualizacao=ts_conta))
regra("SELECT hierarquia FROM hierarquia", L(hierarquia="cliente"))
c3 = appmod.app.test_client()
r = c3.post("/login", data={"email": "a@x.test", "senha": "segredo123", "next": "/\t/evil.example"}, headers=H)
check("login com next '/<TAB>/evil.example' NÃO redireciona pra fora", r.status_code == 302 and "evil" not in r.location, r.location)
with c3.session_transaction() as sess:
    check("login grava user_id e a versão da sessão", sess.get("user_id") == 1 and sess.get("sv") == int(ts_conta.timestamp()), str(dict(sess)))
r = c3.post("/login", data={"email": "a@x.test", "senha": "segredo123", "next": "/\r\n/evil.example"}, headers=H)
check("next com CRLF não derruba o login (antes: 500)", r.status_code == 302, str(r.status_code))
r = c3.post("/login", data={"email": "a@x.test", "senha": "segredo123", "next": "/pagamento/5"}, headers=H)
check("next legítimo continua funcionando", r.status_code == 302 and r.location.endswith("/pagamento/5"), r.location)

# ========================================================== códigos de 6 dígitos
check("hash do código usa scrypt N=2048", auth._hash_do_codigo("123456").startswith("scrypt:2048:8:1$"))
h_novo = auth._hash_do_codigo("123456")
h_velho = generate_password_hash("123456")   # custo padrão (código emitido antes do deploy)
check("código novo é conferido", check_password_hash(h_novo, "123456") and not check_password_hash(h_novo, "654321"))
check("código ANTIGO (custo padrão) continua sendo conferido", check_password_hash(h_velho, "123456"))
t0 = _t.perf_counter()
for _ in range(10):
    auth._hash_do_codigo("123456")
custo = (_t.perf_counter() - t0) / 10 * 1000
check("hash do código é rápido (<30 ms, o padrão leva ~190 ms)", custo < 30, f"({custo:.1f} ms)")

claim_sql = "UPDATE login_usuario SET email_verificacao_tentativas = email_verificacao_tentativas + 1"
hash_ok = auth._hash_do_codigo("123456")
expira = agora + timedelta(minutes=10)


def linha_claim(tentativas, hash_=hash_ok, exp=expira):
    return L(id_usuario=1, nome="Ana", email_verificacao_codigo_hash=hash_, email_verificacao_codigo_expira=exp, email_verificacao_tentativas=tentativas)


def post_codigo(codigo):
    return appmod.app.test_client().post("/confirmar-email", data={"email": "a@x.test", "codigo": codigo}, headers=H)


for ruim in ["", "12", "abcdef", "1234567", "12 456"]:
    zerar()
    regra(claim_sql, linha_claim(1))
    post_codigo(ruim)
    check(f"confirmar: código {ruim!r} nem chega a reservar tentativa", not any(q.startswith(claim_sql) for q in sqls))

zerar()
regra(claim_sql, linha_claim(1))
r = post_codigo("123456")
ordem = [i for i, q in enumerate(sqls) if q.startswith(claim_sql)]
check("confirmar: a tentativa é reservada num UPDATE ... RETURNING (atômico)", ordem and "RETURNING" in sqls[ordem[0]])
check("confirmar: código certo aprova (200 cadastro em análise)", r.status_code == 200 and "Cadastro em análise" in r.get_data(as_text=True))
check("confirmar: sucesso zera o contador e limpa o código", any("email_verificado = TRUE, email_verificacao_tentativas = 0" in q for q in sqls))

zerar()
regra(claim_sql, linha_claim(3))
r = post_codigo("000000")
check("confirmar: código errado na 3ª tentativa -> 400 e o código continua valendo", r.status_code == 400 and not any("codigo_hash = NULL WHERE" in q for q in sqls))
zerar()
regra(claim_sql, linha_claim(5))
r = post_codigo("000000")
check("confirmar: 5ª tentativa errada invalida o código (hash = NULL)", r.status_code == 400 and any("email_verificacao_codigo_hash = NULL WHERE id_usuario" in q for q in sqls))
zerar()   # nenhuma linha devolvida pela reserva = tentativas esgotadas
r = post_codigo("123456")
check("confirmar: tentativas esgotadas -> nem o código certo passa até pedir outro", r.status_code == 400 and not any("email_verificado = TRUE" in q for q in sqls))
zerar()
regra(claim_sql, linha_claim(1, exp=agora - timedelta(minutes=1)))
check("confirmar: código expirado -> 400", post_codigo("123456").status_code == 400)

claim_reset = "UPDATE login_usuario SET reset_tentativas = reset_tentativas + 1"


def post_reset(codigo, senha="novasenha1"):
    return appmod.app.test_client().post("/redefinir-senha", data={"email": "a@x.test", "codigo": codigo, "senha": senha}, headers=H)


zerar()
regra(claim_reset, L(id_usuario=1, reset_codigo_hash=hash_ok, reset_codigo_expira=expira, reset_tentativas=1))
r = post_reset("123456", senha="123")
check("redefinir: senha curta é avisada à parte e NÃO gasta tentativa", r.status_code == 400 and "entre 6 e 128" in r.get_data(as_text=True) and not any(q.startswith(claim_reset) for q in sqls))
zerar()
regra(claim_reset, L(id_usuario=1, reset_codigo_hash=hash_ok, reset_codigo_expira=expira, reset_tentativas=1))
r = post_reset("123456")
check("redefinir: código certo troca a senha e atualiza data_atualizacao (derruba sessões)", r.status_code == 302 and any("senha_hash = %s, reset_tentativas = 0" in q and "data_atualizacao = now()" in q for q in sqls))
zerar()
regra(claim_reset, L(id_usuario=1, reset_codigo_hash=hash_ok, reset_codigo_expira=expira, reset_tentativas=5))
r = post_reset("000000")
check("redefinir: 5ª tentativa errada invalida o código", r.status_code == 400 and any("reset_codigo_hash = NULL WHERE id_usuario" in q for q in sqls))
zerar()
r = post_reset("123456")
check("redefinir: tentativas esgotadas -> recusa", r.status_code == 400 and not any("senha_hash = %s" in q for q in sqls))
zerar()
r = post_reset("abcdef")
check("redefinir: código malformado não reserva tentativa", not any(q.startswith(claim_reset) for q in sqls))

# mudar e-mail não zera o contador
zerar()
regra("SELECT id_usuario, nome FROM login_usuario WHERE email = %s AND email_verificado = FALSE", L(id_usuario=1, nome="Ana"))
auth.enviar_codigo_verificacao_email = lambda *a, **k: None
appmod.app.test_client().post("/confirmar-email/mudar-email", data={"email_atual": "a@x.test", "novo_email": "novo@x.test"}, headers=H)
upd = next((q for q in sqls if q.startswith("UPDATE login_usuario SET email = %s")), "")
check("mudar e-mail: o UPDATE existe e NÃO zera email_verificacao_tentativas", bool(upd) and "tentativas" not in upd, upd[:90])

# ========================================================== Google
zerar()
class FakeGoogle:
    def authorize_access_token(self):
        raise OAuthError(error="mismatching_state")

    def authorize_redirect(self, uri):
        from flask import redirect
        return redirect("https://accounts.google.com/x")


auth.oauth.google = FakeGoogle()
cg = appmod.app.test_client()
r = cg.get("/auth/google/callback?state=ruim&code=x")
check("callback do Google com state inválido volta pro login (302), não dá 500", r.status_code == 302 and "/login" in r.location, str(r.status_code))
cg.get("/auth/google?next=%2F%09%2Fevil.example")
with cg.session_transaction() as sess:
    check("/auth/google guarda next já saneado (o malicioso vira None)", sess.get("login_next") is None, str(sess.get("login_next")))
cg.get("/auth/google?next=/comprar")
with cg.session_transaction() as sess:
    check("/auth/google guarda next legítimo", sess.get("login_next") == "/comprar")

print("\n" + ("TUDO CERTO" if ok else "HÁ FALHAS"))
sys.exit(0 if ok else 1)
