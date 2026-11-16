"""Migração para a 3FN, testada num PostgreSQL de verdade.

Cria um banco no formato ANTIGO (tests/fixtures/schema_anterior.sql), grava dados
variados (lotes, pedidos de 1 e 2 ingressos, 2º pedido para outra pessoa, cortesia,
expirado, códigos pendentes), roda app/migracao_3fn.sql e confere:
  - nenhum dado se perde e os pedidos continuam iguais na view v_pedido;
  - a estrutura migrada é idêntica à de um banco novo criado só com app/schema.sql;
  - as regras do banco (índices únicos, trigger de limpeza) funcionam.

Requer `pip install pgserver`; sem isso o teste é pulado.
"""
import sys
from pathlib import Path

RAIZ = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tests"))
import banco_real as br  # noqa: E402

if not br.disponivel():
    br.pular("(instale `pgserver` para rodar os testes com banco real)")

import psycopg2  # noqa: E402

falhas = 0


def check(nome, cond, detalhe=""):
    global falhas
    print(("OK   " if cond else "FALHOU ") + nome + ((" -> " + str(detalhe)) if (detalhe and not cond) else ""))
    if not cond:
        falhas += 1


SEED_ANTIGO = """
INSERT INTO login_usuario (id_usuario, nome, email, email_confirmado, email_verificado, data_cadastro,
                           email_verificacao_codigo_hash, email_verificacao_codigo_expira, email_verificacao_tentativas,
                           reset_codigo_hash, reset_codigo_expira, reset_tentativas) VALUES
 (1, 'Admin Um',   'admin@x.test',  TRUE,  TRUE,  now(), NULL, NULL, 0, NULL, NULL, 0),
 (2, 'Ana Lima',   'ana@x.test',    TRUE,  TRUE,  now(), NULL, NULL, 0, NULL, NULL, 0),
 (3, 'Bruno Costa','bruno@x.test',  TRUE,  TRUE,  now(), NULL, NULL, 0, NULL, NULL, 0),
 (4, 'Carla Dias', 'carla@x.test',  TRUE,  TRUE,  now(), NULL, NULL, 0, NULL, NULL, 0),
 (5, 'Diego Reis', 'diego@x.test',  FALSE, FALSE, now(), 'hash-email', now() + interval '10 min', 2, NULL, NULL, 0),
 (6, 'Eva Rocha',  'eva@x.test',    TRUE,  TRUE,  now(), NULL, NULL, 0, 'hash-reset', now() + interval '5 min', 1);
SELECT setval('login_usuario_id_usuario_seq', 6);
INSERT INTO hierarquia (id_usuario, hierarquia) VALUES (1,'superadm'),(2,'cliente'),(3,'cliente'),(4,'cliente'),(5,'cliente'),(6,'cliente');

INSERT INTO evento (id_evento, nome_evento, data_evento, hora_evento, endereco, bairro, numero_lote, valor_entrada,
                    quantidade_max_ingressos, capacidade_maxima, usuario_cadastro, data_cadastro, promo_casadinha) VALUES
 (1, 'Festa Antiga', '2026-10-30', '19:00', 'Rua Velha, 1', 'Centro', 1, 30, 2, 60, 1, now() - interval '3 days', FALSE),
 (2, 'Back to Halloween', '2026-10-31', '20:00', 'Rua Nova, 2', 'Bairro Novo', 2, 50, 2, 40, 1, now() - interval '1 day', TRUE);
SELECT setval('evento_id_evento_seq', 2);

INSERT INTO pedido (id_pedido, id_usuario, id_evento, slot, titular_nome, titular_email, rg, whatsapp, valor, quantidade,
                    status_pedido, expirado, mp_payment_id, acompanhante_nome, acompanhante_email, acompanhante_rg,
                    acompanhante_whatsapp, cortesia) VALUES
 (1, 2, 1, 1, NULL, NULL, '1234567', '81999990001', 30, 1, 'confirmado', FALSE, 'MP1', NULL, NULL, NULL, NULL, FALSE),
 (2, 3, 2, 1, NULL, NULL, '2345678', '81999990002', 50, 2, 'pendente',   FALSE, NULL,  'Bia Costa', 'bia@x.test', '3456789', '81999990003', FALSE),
 (3, 2, 2, 2, 'Fulano Silva', 'fulano@x.test', '9999999', '81999990004', 50, 1, 'confirmado', FALSE, 'MP2', NULL, NULL, NULL, NULL, FALSE),
 (4, 1, 2, 1, 'Convidado Zé', 'ze@x.test', '4444444', '81999990005', 0, 1, 'confirmado', FALSE, NULL, NULL, NULL, NULL, NULL, TRUE),
 (5, 4, 2, 1, NULL, NULL, '5555555', '81999990006', 50, 1, 'cancelado', TRUE,  NULL, NULL, NULL, NULL, NULL, FALSE),
 (6, 5, 1, 1, NULL, NULL, '6666666', '81999990007', 30, 1, 'cancelado', FALSE, NULL, NULL, NULL, NULL, NULL, FALSE),
 (7, 6, 2, 1, NULL, NULL, '7777777', '81999990008', 50, 2, 'confirmado', FALSE, NULL, NULL, NULL, NULL, NULL, FALSE);
SELECT setval('pedido_id_pedido_seq', 7);
"""


def estrutura(conn):
    """Colunas, índices, restrições e views de um banco, em forma comparável."""
    with conn.cursor() as cur:
        cur.execute("""SELECT table_name, column_name, data_type, is_nullable,
                              COALESCE(column_default, '') AS padrao
                       FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2""")
        colunas = {(r["table_name"], r["column_name"], r["data_type"], r["is_nullable"], r["padrao"]) for r in cur.fetchall()}
        cur.execute("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
        indices = {(r["indexname"], r["indexdef"]) for r in cur.fetchall()}
        cur.execute("""SELECT conrelid::regclass::text AS tabela, conname, pg_get_constraintdef(oid) AS def
                       FROM pg_constraint WHERE connamespace = 'public'::regnamespace""")
        restricoes = {(r["tabela"], r["conname"], r["def"]) for r in cur.fetchall()}
        cur.execute("SELECT viewname, definition FROM pg_views WHERE schemaname = 'public'")
        views = {(r["viewname"], " ".join(r["definition"].split())) for r in cur.fetchall()}
        cur.execute("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
        gatilhos = {r["tgname"] for r in cur.fetchall()}
    return colunas, indices, restricoes, views, gatilhos


def main():
    banco = br.BancoReal()
    try:
        url_antigo = banco.criar("antigo")
        url_novo = banco.criar("novo")

        # --- banco antigo + dados
        br.rodar_sql(url_antigo, br.ler_arquivo(RAIZ / "tests" / "fixtures" / "schema_anterior.sql"))
        br.rodar_sql(url_antigo, SEED_ANTIGO)
        antigo = br.conectar(url_antigo)
        with antigo.cursor() as cur:
            cur.execute("SELECT * FROM pedido ORDER BY id_pedido")
            pedidos_antes = {r["id_pedido"]: dict(r) for r in cur.fetchall()}
            cur.execute("SELECT id_usuario, nome, email FROM login_usuario")
            contas = {r["id_usuario"]: dict(r) for r in cur.fetchall()}
            cur.execute("SELECT * FROM evento ORDER BY id_evento")
            eventos_antes = {r["id_evento"]: dict(r) for r in cur.fetchall()}
        antigo.close()

        # --- migração
        migracao = br.ler_arquivo(RAIZ / "app" / "migracao_3fn.sql")
        try:
            br.rodar_sql(url_antigo, migracao)
            check("a migração roda sem erro", True)
        except psycopg2.Error as e:
            check("a migração roda sem erro", False, e)
            return

        # --- banco novo só com schema.sql
        br.rodar_sql(url_novo, br.ler_arquivo(RAIZ / "app" / "schema.sql"))
        check("schema.sql roda num banco vazio", True)
        # idempotente: rodar de novo não estraga (CREATE ... IF NOT EXISTS)
        try:
            br.rodar_sql(url_novo, br.ler_arquivo(RAIZ / "app" / "schema.sql"))
            check("schema.sql pode ser rodado duas vezes", True)
        except psycopg2.Error as e:
            check("schema.sql pode ser rodado duas vezes", False, e)

        mig = br.conectar(url_antigo)
        novo = br.conectar(url_novo)

        # --- estrutura idêntica
        e_mig, e_novo = estrutura(mig), estrutura(novo)
        for rotulo, a, b in zip(("colunas", "índices", "restrições", "views", "gatilhos"), e_mig, e_novo):
            check(f"estrutura migrada = banco novo ({rotulo})", a == b, f"só no migrado: {sorted(a - b)[:3]} | só no novo: {sorted(b - a)[:3]}")

        with mig.cursor() as cur:
            # --- evento -> festa + lote
            cur.execute("SELECT * FROM festa")
            festas = cur.fetchall()
            check("existe uma única festa", len(festas) == 1)
            f = festas[0]
            check("a festa usa os dados do lote mais recente (nome, data, capacidade)",
                  f["nome_festa"] == "Back to Halloween" and f["capacidade_maxima"] == 40 and str(f["data_festa"]) == "2026-10-31")
            check("a festa guarda quem cadastrou e quando (do 1º lote)",
                  f["usuario_cadastro"] == 1 and f["data_cadastro"] == eventos_antes[1]["data_cadastro"])
            cur.execute("SELECT * FROM lote ORDER BY id_lote")
            lotes = cur.fetchall()
            check("os 2 lotes mantêm id, número, preço e promoção",
                  [(l["id_lote"], l["numero_lote"], float(l["valor_entrada"]), l["promo_casadinha"]) for l in lotes]
                  == [(1, 1, 30.0, False), (2, 2, 50.0, True)])
            check("os lotes apontam para a festa", all(l["id_festa"] == f["id_festa"] for l in lotes))
            cur.execute("SELECT * FROM v_evento ORDER BY id_evento DESC LIMIT 1")
            ve = cur.fetchone()
            check("v_evento devolve o lote ativo com os dados da festa",
                  ve["id_evento"] == 2 and ve["nome_evento"] == "Back to Halloween" and ve["capacidade_maxima"] == 40
                  and ve["endereco"] == "Rua Nova, 2" and ve["promo_casadinha"] is True)

            # --- pedidos iguais na view
            cur.execute("SELECT * FROM v_pedido ORDER BY id_pedido")
            depois = {r["id_pedido"]: dict(r) for r in cur.fetchall()}
            check("todos os 7 pedidos continuam existindo", sorted(depois) == sorted(pedidos_antes))
            for pid, antes in pedidos_antes.items():
                d = depois[pid]
                nome_conta = contas[antes["id_usuario"]]["nome"]
                email_conta = contas[antes["id_usuario"]]["email"]
                esperado_acomp = antes["acompanhante_nome"] or ("Acompanhante (nome não informado)" if antes["quantidade"] == 2 else None)
                ok = (
                    d["id_usuario"] == antes["id_usuario"] and d["id_evento"] == antes["id_evento"] and d["slot"] == antes["slot"]
                    and d["valor"] == antes["valor"] and d["quantidade"] == antes["quantidade"]
                    and d["status_pedido"] == antes["status_pedido"] and d["expirado"] == antes["expirado"]
                    and d["cortesia"] == antes["cortesia"] and d["mp_payment_id"] == antes["mp_payment_id"]
                    and d["data_pedido"] == antes["data_pedido"]
                    and d["titular_nome"] == (antes["titular_nome"] or nome_conta)
                    and d["titular_email"] == (antes["titular_email"] or email_conta)
                    and d["rg"] == antes["rg"] and d["whatsapp"] == antes["whatsapp"]
                    and d["acompanhante_nome"] == esperado_acomp
                    and d["acompanhante_email"] == antes["acompanhante_email"]
                    and d["acompanhante_rg"] == antes["acompanhante_rg"]
                    and d["acompanhante_whatsapp"] == antes["acompanhante_whatsapp"]
                )
                check(f"pedido #{pid} igual ao de antes na view v_pedido", ok, {k: (antes.get(k), d.get(k)) for k in d if antes.get(k) != d.get(k)})

            cur.execute("SELECT status_pedido FROM pedido WHERE id_pedido = 5")
            check("pedido cancelado por tempo virou status 'expirado' na tabela", cur.fetchone()["status_pedido"] == "expirado")
            cur.execute("SELECT status_pedido FROM pedido WHERE id_pedido = 6")
            check("pedido cancelado à mão continua 'cancelado'", cur.fetchone()["status_pedido"] == "cancelado")

            # --- ingressos e participantes
            cur.execute("SELECT id_pedido, COUNT(*) AS n FROM ingresso GROUP BY 1")
            por_pedido = {r["id_pedido"]: r["n"] for r in cur.fetchall()}
            check("a contagem de ingressos de cada pedido bate com a quantidade antiga",
                  all(por_pedido.get(pid, 0) == a["quantidade"] for pid, a in pedidos_antes.items()))
            cur.execute("SELECT COUNT(*) AS n FROM participante")
            check("1 participante por ingresso (nenhum solto)", cur.fetchone()["n"] == sum(a["quantidade"] for a in pedidos_antes.values()))

            # --- códigos
            cur.execute("SELECT id_usuario, tipo, codigo_hash, tentativas FROM codigo_verificacao ORDER BY id_usuario")
            codigos = [(r["id_usuario"], r["tipo"], r["codigo_hash"], r["tentativas"]) for r in cur.fetchall()]
            check("códigos pendentes migrados (e-mail do Diego, senha da Eva)",
                  codigos == [(5, "email", "hash-email", 2), (6, "senha", "hash-reset", 1)], codigos)
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='login_usuario'")
            colunas = {r["column_name"] for r in cur.fetchall()}
            check("login_usuario não tem mais as colunas de código", not any(c.startswith(("email_verificacao_", "reset_")) for c in colunas))
            cur.execute("SELECT to_regclass('public.evento') AS t")
            check("a tabela evento foi removida", cur.fetchone()["t"] is None)
        mig.commit()

        # --- regras do banco depois da migração (no banco migrado)
        def tentar(sql, params=None):
            c = br.conectar(url_antigo)
            try:
                with c.cursor() as cur:
                    cur.execute(sql, params)
                c.commit()
                return None
            except psycopg2.Error as e:
                c.rollback()
                return e
            finally:
                c.close()

        # sequências continuam de onde estavam
        c = br.conectar(url_antigo)
        with c.cursor() as cur:
            cur.execute("INSERT INTO lote (id_festa, numero_lote, valor_entrada) VALUES (%s, 3, 60) RETURNING id_lote", (f["id_festa"],))
            check("o próximo lote recebe o id 3 (sequência acertada)", cur.fetchone()["id_lote"] == 3)
            cur.execute("INSERT INTO pedido (id_usuario, id_lote, slot, valor) VALUES (3, 3, 1, 60) RETURNING id_pedido")
            check("o próximo pedido recebe o id 8", cur.fetchone()["id_pedido"] == 8)
        c.commit()
        c.close()

        erro = tentar("INSERT INTO pedido (id_usuario, id_lote, slot, valor) VALUES (3, 3, 1, 60)")
        check("índice único: 2º pedido ativo no mesmo slot é recusado", isinstance(erro, psycopg2.errors.UniqueViolation))
        erro = tentar("INSERT INTO pedido (id_usuario, id_lote, slot, valor, mp_payment_id) VALUES (4, 2, 1, 50, 'MP1')")
        check("índice único: o mesmo pagamento Pix não confirma 2 pedidos", isinstance(erro, psycopg2.errors.UniqueViolation))
        erro = tentar("INSERT INTO pedido (id_usuario, id_lote, slot, valor, status_pedido) VALUES (4, 2, 1, 50, 'qualquer')")
        check("CHECK: status inválido é recusado", isinstance(erro, psycopg2.errors.CheckViolation))
        erro = tentar("INSERT INTO ingresso (id_pedido, id_participante, papel) SELECT 1, id_participante, 'titular' FROM participante LIMIT 1")
        check("UNIQUE: um pedido não tem 2 titulares", isinstance(erro, psycopg2.errors.UniqueViolation))
        erro = tentar("DELETE FROM lote WHERE id_lote = 1")
        check("FK: não dá para apagar lote com pedido", isinstance(erro, psycopg2.errors.ForeignKeyViolation))

        # expirado não conta no limite: 2º pedido ativo é permitido se o anterior expirou
        erro = tentar("INSERT INTO pedido (id_usuario, id_lote, slot, valor) VALUES (4, 2, 1, 50)")
        check("pedido expirado libera o slot da conta", erro is None, erro)

        # trigger: apagar pedido remove participantes que ficariam soltos
        c = br.conectar(url_antigo)
        with c.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM participante")
            antes_n = cur.fetchone()["n"]
            cur.execute("DELETE FROM pedido WHERE id_pedido = 2")  # titular + acompanhante
            cur.execute("SELECT COUNT(*) AS n FROM participante")
            depois_n = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM ingresso WHERE id_pedido = 2")
            sobrou = cur.fetchone()["n"]
        c.commit()
        c.close()
        check("apagar o pedido apaga os ingressos e os 2 participantes dele", sobrou == 0 and antes_n - depois_n == 2, (antes_n, depois_n))

        novo.close()
        mig.close()
    finally:
        banco.fechar()


main()
print("\n%s" % ("%d FALHA(S)" % falhas if falhas else "tudo certo"))
sys.exit(1 if falhas else 0)
