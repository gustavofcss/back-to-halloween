"""Fluxo completo do site contra um PostgreSQL de verdade (schema 3FN).

Sobe o app Flask real apontando para um banco embarcado e percorre: cadastro e
códigos de verificação, compra (1 e 2 ingressos, 2º pedido para outra pessoa),
conciliação do Pix (com a API do Mercado Pago simulada), expiração, painel admin
(pedidos, cortesia, planilha, lotes, festa, encerrar vendas) e redefinição de senha.
Confere sempre o que ficou nas tabelas (pedido, ingresso, participante...).

Requer `pip install pgserver`; sem isso o teste é pulado.
"""
import io
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tests"))
import banco_real as br  # noqa: E402

if not br.disponivel():
    br.pular("(instale `pgserver` para rodar os testes com banco real)")

falhas = 0


def check(nome, cond, detalhe=""):
    global falhas
    print(("OK   " if cond else "FALHOU ") + nome + ((" -> " + str(detalhe)) if (detalhe and not cond) else ""))
    if not cond:
        falhas += 1


banco = br.BancoReal()
try:
    URL = banco.criar("e2e")
    os.environ.update(
        DATABASE_URL=URL, SECRET_KEY="teste", BASE_URL="http://localhost", PIX_KEY_30="k30", PIX_KEY_50="k50",
        PIX_KEY_60="k60", MERCADOPAGO_ACCESS_TOKEN="token-de-teste", BREVO_API_KEY="", GOOGLE_CLIENT_ID="",
        GOOGLE_CLIENT_SECRET="",
    )
    os.chdir(RAIZ / "app")
    sys.path.insert(0, str(RAIZ / "app"))
    br.rodar_sql(URL, br.ler_arquivo(RAIZ / "app" / "schema.sql"))

    from werkzeug.security import generate_password_hash  # noqa: E402

    import app as appmod  # noqa: E402
    import email_utils  # noqa: E402
    import limitador  # noqa: E402
    import pedidos  # noqa: E402

    # ---- simulações das APIs externas
    ENVIADOS: list[tuple[str, str, str]] = []
    email_utils._enviar = lambda d, a, c, h=None: ENVIADOS.append((d, a, c))
    PAGAMENTOS: list[dict] = []
    pedidos._buscar_pagamentos_pix_aprovados = lambda desde, usar_cache=True: list(PAGAMENTOS)

    def pagamento(id_, valor):
        PAGAMENTOS.append({"id": id_, "status": "approved", "payment_method_id": "pix", "transaction_amount": valor,
                           "date_approved": datetime.now(timezone.utc).isoformat()})

    def emails_para(destino):
        time.sleep(0.4)  # envios rodam em thread
        return [e for e in ENVIADOS if e[0] == destino]

    # ---- dados iniciais
    senha = generate_password_hash("senha123")
    br.rodar_sql(URL, f"""
        INSERT INTO login_usuario (id_usuario, nome, email, senha_hash, email_confirmado, email_verificado) VALUES
          (1, 'Admin Um', 'admin@x.test', '{senha}', TRUE, TRUE),
          (2, 'Ana Lima', 'ana@x.test', '{senha}', TRUE, TRUE),
          (3, 'Bruno Costa', 'bruno@x.test', '{senha}', TRUE, TRUE),
          (4, 'Carla Dias', 'carla@x.test', '{senha}', TRUE, TRUE);
        SELECT setval('login_usuario_id_usuario_seq', 4);
        INSERT INTO hierarquia (id_usuario, hierarquia) VALUES (1,'superadm'),(2,'cliente'),(3,'cliente'),(4,'cliente');
        INSERT INTO festa (nome_festa, data_festa, hora_festa, endereco, bairro, capacidade_maxima, usuario_cadastro)
          VALUES ('Back to Halloween', '2026-10-31', '20:00', 'Rua Exemplo, 123', 'Centro', 4, 1);
        INSERT INTO lote (id_festa, numero_lote, valor_entrada, quantidade_max_ingressos, promo_casadinha)
          VALUES (1, 1, 30, 2, TRUE);
    """)

    conn = br.conectar(URL)

    def q(sql, params=None):
        with conn.cursor() as cur:
            cur.execute(sql, params)
            r = cur.fetchall() if cur.description else []
        conn.commit()
        return r

    ORIGEM = {"Origin": "http://localhost"}

    def cliente(email, senha_="senha123"):
        limitador.limpar()
        c = appmod.app.test_client()
        r = c.post("/login", data={"email": email, "senha": senha_}, headers=ORIGEM)
        assert r.status_code in (302, 303), (email, r.status_code)
        return c

    def post(c, caminho, ajax=False, **kw):
        h = dict(ORIGEM)
        if ajax:
            h["X-Requested-With"] = "XMLHttpRequest"
        return c.post(caminho, headers=h, **kw)

    # =========================================================== páginas públicas
    anon = appmod.app.test_client()
    r = anon.get("/")
    check("a página inicial abre e mostra o lote (v_evento)", r.status_code == 200 and b"R$&nbsp;30" in r.data, r.status_code)

    # =========================================================== Ana compra 2 ingressos (casadinha)
    ana = cliente("ana@x.test")
    r = ana.get("/comprar")
    check("GET /comprar abre para quem está logado", r.status_code == 200 and b"Back to Halloween" in r.data)
    r = post(ana, "/comprar", data={
        "quantidade": "2", "rg": "123.456.78", "whatsapp": "(81) 99999-0001",
        "acompanhante_nome": "Bia Lima", "acompanhante_email": "bia@x.test", "acompanhante_rg": "3456789",
        "acompanhante_whatsapp": "81999990003"})
    check("POST /comprar (2 ingressos) redireciona para o pagamento", r.status_code == 302 and "/pagamento/1" in r.headers["Location"], r.status_code)
    p = q("SELECT * FROM pedido WHERE id_pedido = 1")[0]
    check("o pedido guarda só o que é do pedido (lote, valor da casadinha, pendente)",
          p["id_lote"] == 1 and float(p["valor"]) == 50.0 and p["status_pedido"] == "pendente" and p["slot"] == 1 and not p["cortesia"])
    ing = q("""SELECT i.papel, pa.nome, pa.email, pa.rg, pa.whatsapp FROM ingresso i
               JOIN participante pa ON pa.id_participante = i.id_participante WHERE i.id_pedido = 1 ORDER BY i.papel""")
    check("2 ingressos: o acompanhante e o titular (a própria conta) viram participantes",
          [(i["papel"], i["nome"], i["rg"]) for i in ing] == [("acompanhante", "Bia Lima", "3456789"), ("titular", "Ana Lima", "12345678")], ing)
    vp = q("SELECT * FROM v_pedido WHERE id_pedido = 1")[0]
    check("v_pedido mostra quantidade 2, titular e acompanhante", vp["quantidade"] == 2 and vp["titular_nome"] == "Ana Lima" and vp["acompanhante_nome"] == "Bia Lima")
    check("a contagem de vagas usa os ingressos", pedidos._ingressos_vendidos_total(conn.cursor()) == 2)

    r = ana.get("/pagamento/1")
    check("a tela de pagamento abre (QR Pix)", r.status_code == 200, r.status_code)
    r = post(ana, "/pagamento/1/verificar")
    check("sem Pix aprovado o pedido segue pendente", r.get_json()["status"] == "pendente", r.get_json())
    pagamento("P-100", 50.0)
    r = post(ana, "/pagamento/1/verificar")
    check("com o Pix de R$ 50 aprovado o pedido é confirmado", r.get_json()["status"] == "confirmado", r.get_json())
    p = q("SELECT status_pedido, mp_payment_id FROM pedido WHERE id_pedido = 1")[0]
    check("o id do pagamento fica no pedido", p["status_pedido"] == "confirmado" and p["mp_payment_id"] == "P-100")
    check("e-mail de ingresso confirmado enviado", any("confirmado" in e[1].lower() for e in emails_para("ana@x.test")))
    check("Meus pedidos abre", ana.get("/meus-pedidos").status_code == 200)
    pedidos._consultar_pagamento = lambda mp_id: {"status": "approved", "payment_method_id": "pix"}
    r = anon.post("/pagamento/webhook/mercadopago", json={"type": "payment", "data": {"id": "P-100"}})
    check("webhook do Mercado Pago acha o pedido pelo id do pagamento (sem erro)", r.status_code == 200, r.status_code)
    r = anon.post("/pagamento/webhook/mercadopago", json={"type": "payment", "data": {"id": "P-NAO-EXISTE"}})
    check("webhook com pagamento desconhecido responde 200", r.status_code == 200, r.status_code)
    r = ana.get("/comprar")
    check("Ana já usou os 2 ingressos da conta: tela de limite", b"LIMITE" in r.data, r.status_code)

    # =========================================================== Bruno: 1 ingresso + 2º pedido para outra pessoa
    bruno = cliente("bruno@x.test")
    post(bruno, "/comprar", data={"quantidade": "1", "rg": "2345678", "whatsapp": "81999990002"})
    p2 = q("SELECT * FROM pedido WHERE id_usuario = 3 ORDER BY id_pedido")[0]
    check("Bruno: pedido de 1 ingresso a R$ 30", float(p2["valor"]) == 30.0 and p2["status_pedido"] == "pendente")
    r = post(bruno, f"/pagamento/{p2['id_pedido']}/verificar")
    check("o Pix já usado (P-100, R$ 50) não confirma o pedido de R$ 30", r.get_json()["status"] == "pendente")
    pagamento("P-101", 30.0)
    r = post(bruno, f"/pagamento/{p2['id_pedido']}/verificar")
    check("o Pix de R$ 30 confirma o pedido do Bruno", r.get_json()["status"] == "confirmado")

    post(bruno, "/comprar", data={"quantidade": "1", "rg": "9999999", "whatsapp": "81999990004",
                                  "titular_nome": "Fulano Silva", "titular_email": "fulano@x.test"})
    p3 = q("SELECT * FROM pedido WHERE id_usuario = 3 AND slot = 2")[0]
    t3 = q("""SELECT pa.nome, pa.email FROM ingresso i JOIN participante pa ON pa.id_participante = i.id_participante
              WHERE i.id_pedido = %s AND i.papel = 'titular'""", (p3["id_pedido"],))[0]
    check("2º pedido (slot 2): o titular é a OUTRA pessoa", t3["nome"] == "Fulano Silva" and t3["email"] == "fulano@x.test")

    # expiração: passou de 5 minutos sem Pix
    q("UPDATE pedido SET data_pedido = now() - interval '10 minutes' WHERE id_pedido = %s", (p3["id_pedido"],))
    r = post(bruno, f"/pagamento/{p3['id_pedido']}/verificar")
    st = q("SELECT status_pedido FROM pedido WHERE id_pedido = %s", (p3["id_pedido"],))[0]["status_pedido"]
    vp3 = q("SELECT status_pedido, expirado FROM v_pedido WHERE id_pedido = %s", (p3["id_pedido"],))[0]
    check("sem pagamento e fora da janela o pedido expira (status 'expirado' na tabela)", st == "expirado" and r.get_json()["status"] == "cancelado", (st, r.get_json()))
    check("a view mostra cancelado + expirado (como as telas sempre leram)", vp3["status_pedido"] == "cancelado" and vp3["expirado"] is True)
    check("a vaga volta (só os 3 ingressos pagos contam)", pedidos._ingressos_vendidos_total(conn.cursor()) == 3)
    r = post(bruno, "/comprar", data={"quantidade": "1", "rg": "8888888", "whatsapp": "81999990009",
                                      "titular_nome": "Gabi Souza", "titular_email": "gabi@x.test"})
    check("depois de expirar, o slot 2 pode ser comprado de novo", r.status_code == 302, r.status_code)
    # Bruno: slot1 + slot2 novo = capacidade 4 cheia
    carla = cliente("carla@x.test")
    check("capacidade cheia: ESGOTADO", b"ESGOTADO" in carla.get("/comprar").data)

    # =========================================================== painel admin
    admin_c = cliente("admin@x.test")
    r = admin_c.get("/admin/pedidos")
    check("admin: lista de pedidos abre com titulares", r.status_code == 200 and b"Ana Lima" in r.data and b"Bia Lima" in r.data, r.status_code)
    r = admin_c.get("/admin/pedidos/lista")
    check("admin: JSON da lista de pedidos (polling)", r.status_code == 200 and len(r.get_json()["itens"]) >= 3 and "totais" in r.get_json(), r.status_code)
    r = admin_c.get("/admin/pedidos/exportar")
    from openpyxl import load_workbook
    ws = load_workbook(io.BytesIO(r.data)).active
    nomes = {ws.cell(i, 2).value for i in range(2, ws.max_row + 1)}
    check("planilha da portaria lista titulares e acompanhantes", r.status_code == 200 and {"Ana Lima", "Bia Lima", "Bruno Costa"} <= nomes, nomes)

    r = post(admin_c, "/admin/pedidos/cortesia", data={"nome": "Convidado Zé", "email": "ze@x.test", "rg": "4444444", "whatsapp": "81999990005"})
    c = q("SELECT * FROM pedido WHERE cortesia")[0]
    zi = q("SELECT pa.nome, pa.rg FROM ingresso i JOIN participante pa ON pa.id_participante = i.id_participante WHERE i.id_pedido = %s", (c["id_pedido"],))
    check("cortesia: pedido confirmado de valor 0 com o convidado como participante",
          r.status_code == 302 and c["status_pedido"] == "confirmado" and float(c["valor"]) == 0 and c["id_usuario"] == 1
          and zi[0]["nome"] == "Convidado Zé" and zi[0]["rg"] == "4444444", (c, zi))
    check("e-mail de cortesia enviado ao convidado", any("cortesia" in e[2].lower() for e in emails_para("ze@x.test")))

    # confirmar à mão o pedido pendente do Bruno (slot 2 novo)
    pend = q("SELECT id_pedido FROM pedido WHERE status_pedido = 'pendente'")[0]["id_pedido"]
    r = post(admin_c, f"/admin/pedidos/{pend}/confirmar", ajax=True)
    check("admin confirma um Pix pendente", r.status_code == 200 and r.get_json()["aplicados"], r.get_json())

    r = post(admin_c, f"/admin/pedidos/{p3['id_pedido']}/cancelar", ajax=True)
    check("cancelar um pedido que já expirou é recusado como 'já encerrado'", r.get_json()["aplicados"] == [] and r.get_json()["ignorados"], r.get_json())
    antes_part = q("SELECT COUNT(*) AS n FROM participante")[0]["n"]
    r = post(admin_c, f"/admin/pedidos/{p3['id_pedido']}/apagar", ajax=True)
    depois_part = q("SELECT COUNT(*) AS n FROM participante")[0]["n"]
    check("apagar um pedido expirado remove o pedido, o ingresso e o participante", r.get_json()["aplicados"]
          and q("SELECT COUNT(*) AS n FROM pedido WHERE id_pedido = %s", (p3["id_pedido"],))[0]["n"] == 0 and antes_part - depois_part == 1, (antes_part, depois_part))
    r = post(admin_c, f"/admin/pedidos/{p2['id_pedido']}/cancelar", ajax=True)
    check("admin cancela um pedido confirmado", r.get_json()["aplicados"] and q("SELECT status_pedido FROM pedido WHERE id_pedido = %s", (p2["id_pedido"],))[0]["status_pedido"] == "cancelado")
    check("e-mail em massa e outras páginas do admin abrem",
          all(admin_c.get(u).status_code == 200 for u in ("/admin/pedidos/email-massa", "/admin/cadastros", "/admin/hierarquia", "/admin/material", "/admin/eventos")))

    # ---- evento: festa (compartilhada) e lotes
    r = post(admin_c, "/admin/eventos", data={"valor_entrada": "50", "quantidade_max_ingressos": "2"})
    lotes = q("SELECT id_lote, id_festa, numero_lote, valor_entrada, promo_casadinha FROM lote ORDER BY id_lote")
    check("novo lote aponta para a MESMA festa e herda a promoção", len(lotes) == 2 and lotes[1]["id_festa"] == 1 and lotes[1]["numero_lote"] == 2
          and lotes[1]["promo_casadinha"] is True and q("SELECT COUNT(*) AS n FROM festa")[0]["n"] == 1, lotes)
    post(admin_c, "/admin/eventos/capacidade", data={"capacidade_maxima": "3"})
    ve = q("SELECT id_evento, capacidade_maxima FROM v_evento ORDER BY id_evento")
    check("capacidade é da festa: todos os lotes enxergam o valor novo", [v["capacidade_maxima"] for v in ve] == [3, 3], ve)
    post(admin_c, "/admin/eventos/nome-data", data={"nome_evento": "Nome Novo", "data_evento": "2026-11-01", "hora_evento": "21:00"})
    post(admin_c, "/admin/eventos/endereco", data={"endereco": "Av. Nova, 9", "bairro": "Boa Vista"})
    f = q("SELECT * FROM festa")[0]
    check("nome, data, hora e endereço ficam na festa", f["nome_festa"] == "Nome Novo" and str(f["data_festa"]) == "2026-11-01"
          and str(f["hora_festa"]) == "21:00:00" and f["endereco"] == "Av. Nova, 9" and f["bairro"] == "Boa Vista")
    convite = anon.get("/").data
    check("o convite mostra o endereço e o horário novos da festa", b"Av. Nova, 9" in convite and b"21:00" in convite)
    post(admin_c, "/admin/eventos/casadinha", data={"ativo": "0"})
    lp = q("SELECT id_lote, promo_casadinha FROM lote ORDER BY id_lote")
    check("liga/desliga da casadinha age só no lote ativo", [(l["id_lote"], l["promo_casadinha"]) for l in lp] == [(1, True), (2, False)], lp)
    post(admin_c, "/admin/eventos/encerrar-vendas", data={"encerrar": "1"})
    check("encerrar vendas marca o lote ativo", q("SELECT vendas_encerradas FROM lote WHERE id_lote = 2")[0]["vendas_encerradas"] is True)
    check("com vendas encerradas, /comprar avisa", b"VENDAS ENCERRADAS" in carla.get("/comprar").data)
    check("e o convite troca o botão", b"VENDAS ENCERRADAS" in anon.get("/").data and b"COMPRAR INGRESSO" not in anon.get("/").data)
    post(admin_c, "/admin/eventos/encerrar-vendas", data={"encerrar": "0"})
    r = post(admin_c, "/admin/eventos/1/apagar", ajax=True)
    check("não dá para apagar lote que tem pedido (FK vira aviso, sem 500)", r.status_code == 400 and q("SELECT COUNT(*) AS n FROM lote WHERE id_lote = 1")[0]["n"] == 1, (r.status_code, r.get_data()[:120]))
    r = post(admin_c, "/admin/eventos/2/apagar", ajax=True)
    check("apagar o lote ativo vazio volta para o anterior, com os dados da festa", r.get_json()["aplicados"]
          and q("SELECT id_evento, nome_evento FROM v_evento")[0] == {"id_evento": 1, "nome_evento": "Nome Novo"}, r.get_json())

    # =========================================================== cadastro e códigos
    limitador.limpar()
    novo = appmod.app.test_client()
    r = post(novo, "/signup", data={"nome": "Dora Alves", "email": "dora@x.test", "senha": "segredo1"})
    cod = q("SELECT c.* FROM codigo_verificacao c JOIN login_usuario u ON u.id_usuario = c.id_usuario WHERE u.email = 'dora@x.test'")
    check("cadastro cria a conta e o código na tabela codigo_verificacao", r.status_code == 200 and len(cod) == 1 and cod[0]["tipo"] == "email" and cod[0]["tentativas"] == 0, r.status_code)
    codigo = re.search(r"\b(\d{6})\b", emails_para("dora@x.test")[0][2]).group(1)
    for _ in range(2):
        post(novo, "/confirmar-email", data={"email": "dora@x.test", "codigo": "000000" if codigo != "000000" else "111111"})
    check("cada palpite errado gasta uma tentativa", q("SELECT tentativas FROM codigo_verificacao c JOIN login_usuario u USING (id_usuario) WHERE u.email = 'dora@x.test'")[0]["tentativas"] == 2)
    post(novo, "/confirmar-email/reenviar", data={"email": "dora@x.test"})
    check("reenviar logo em seguida não troca o código (intervalo de 1 min)",
          q("SELECT codigo_hash FROM codigo_verificacao c JOIN login_usuario u USING (id_usuario) WHERE u.email = 'dora@x.test'")[0]["codigo_hash"] == cod[0]["codigo_hash"])
    r = post(novo, "/confirmar-email", data={"email": "dora@x.test", "codigo": codigo})
    u = q("SELECT email_verificado FROM login_usuario WHERE email = 'dora@x.test'")[0]
    check("código certo verifica o e-mail e apaga o código", r.status_code == 200 and u["email_verificado"] is True
          and q("SELECT COUNT(*) AS n FROM codigo_verificacao c JOIN login_usuario u USING (id_usuario) WHERE u.email = 'dora@x.test'")[0]["n"] == 0)
    check("o admin foi avisado do cadastro pendente", any("pendente" in e[1].lower() or "cadastro" in e[1].lower() for e in emails_para("admin@x.test")))

    limitador.limpar()
    post(novo, "/signup", data={"nome": "Edu Souza", "email": "edu@x.test", "senha": "segredo1"})
    codigo_edu = re.search(r"\b(\d{6})\b", emails_para("edu@x.test")[0][2]).group(1)
    errado = "000000" if codigo_edu != "000000" else "111111"
    for _ in range(5):
        post(novo, "/confirmar-email", data={"email": "edu@x.test", "codigo": errado})
    check("5 erros apagam o código", q("SELECT COUNT(*) AS n FROM codigo_verificacao c JOIN login_usuario u USING (id_usuario) WHERE u.email = 'edu@x.test'")[0]["n"] == 0)
    r = post(novo, "/confirmar-email", data={"email": "edu@x.test", "codigo": codigo_edu})
    check("depois de esgotar, nem o código certo vale", r.status_code == 400)
    limitador.limpar()
    post(novo, "/confirmar-email/reenviar", data={"email": "edu@x.test"})
    check("pedir outro código cria uma linha nova", q("SELECT tentativas FROM codigo_verificacao c JOIN login_usuario u USING (id_usuario) WHERE u.email = 'edu@x.test'")[0]["tentativas"] == 0)
    ult = re.search(r"\b(\d{6})\b", emails_para("edu@x.test")[-1][2]).group(1)
    r = post(novo, "/confirmar-email", data={"email": "edu@x.test", "codigo": ult})
    check("o código novo funciona", r.status_code == 200 and q("SELECT email_verificado FROM login_usuario WHERE email = 'edu@x.test'")[0]["email_verificado"] is True)

    # ---- esqueci a senha
    limitador.limpar()
    sessao_antiga = cliente("carla@x.test")
    limitador.limpar()
    anon2 = appmod.app.test_client()
    post(anon2, "/esqueci-senha", data={"email": "carla@x.test"})
    cs = q("SELECT c.tipo, c.tentativas FROM codigo_verificacao c JOIN login_usuario u USING (id_usuario) WHERE u.email = 'carla@x.test'")
    check("esqueci a senha cria o código do tipo 'senha'", len(cs) == 1 and cs[0]["tipo"] == "senha")
    cod_senha = re.search(r"\b(\d{6})\b", emails_para("carla@x.test")[-1][2]).group(1)
    r = post(anon2, "/redefinir-senha", data={"email": "carla@x.test", "codigo": "000000" if cod_senha != "000000" else "111111", "senha": "novasenha1"})
    check("código errado: recusado e conta a tentativa", r.status_code == 400 and q("SELECT c.tentativas FROM codigo_verificacao c JOIN login_usuario u USING (id_usuario) WHERE u.email = 'carla@x.test'")[0]["tentativas"] == 1)
    r = post(anon2, "/redefinir-senha", data={"email": "carla@x.test", "codigo": cod_senha, "senha": "novasenha1"})
    check("código certo troca a senha e apaga o código", r.status_code in (302, 303)
          and q("SELECT COUNT(*) AS n FROM codigo_verificacao c JOIN login_usuario u USING (id_usuario) WHERE u.email = 'carla@x.test'")[0]["n"] == 0)
    limitador.limpar()
    check("a senha nova entra", post(appmod.app.test_client(), "/login", data={"email": "carla@x.test", "senha": "novasenha1"}).status_code in (302, 303))
    check("a sessão aberta antes da troca de senha deixa de valer", sessao_antiga.get("/comprar").status_code in (302, 303))

    # nenhum participante solto no fim de tudo
    solto = q("SELECT COUNT(*) AS n FROM participante p WHERE NOT EXISTS (SELECT 1 FROM ingresso i WHERE i.id_participante = p.id_participante)")[0]["n"]
    check("no fim, nenhum participante ficou solto", solto == 0, solto)
    conn.close()
finally:
    banco.fechar()

print("\n%s" % ("%d FALHA(S)" % falhas if falhas else "tudo certo"))
sys.exit(1 if falhas else 0)
