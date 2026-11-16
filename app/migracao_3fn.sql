-- Migração do banco da versão anterior (tabela `evento`, pedido com titular_* e
-- acompanhante_*) para o schema normalizado em 3FN (app/schema.sql).
--
-- Converte os dados existentes sem perder nada e roda numa transação: se qualquer
-- passo falhar, nada é alterado. Rode UMA vez, ANTES de subir o código novo:
--
--   psql "$DATABASE_URL" -f app/migracao_3fn.sql
--   (ou cole no SQL Editor do Supabase)
--
-- Faça um backup antes (no Supabase: Database > Backups). O código novo e o
-- banco antigo não funcionam juntos, e vice-versa.
--
-- O que muda:
--   evento                          -> festa (dados da festa) + lote (preço)
--   pedido.titular_*, rg, whatsapp,
--     acompanhante_*                -> participante + ingresso (1 linha por pessoa)
--   pedido.quantidade               -> contagem de ingresso
--   pedido.expirado                 -> status_pedido = 'expirado'
--   pedido.id_evento                -> pedido.id_lote
--   login_usuario.email_verificacao_*, reset_*  -> codigo_verificacao

BEGIN;

-- Garante a coluna da função "encerrar vendas" no banco antigo (o código novo a usa).
ALTER TABLE evento ADD COLUMN IF NOT EXISTS vendas_encerradas BOOLEAN NOT NULL DEFAULT FALSE;

-- ---------------------------------------------------------------------------
-- 1. Tabelas novas (mesma definição de schema.sql)
-- ---------------------------------------------------------------------------

CREATE TABLE festa (
    id_festa           SERIAL PRIMARY KEY,
    nome_festa         VARCHAR(160) NOT NULL,
    data_festa         DATE,
    hora_festa         TIME,
    endereco           VARCHAR(200),
    bairro             VARCHAR(100),
    capacidade_maxima  INTEGER NOT NULL DEFAULT 60,
    usuario_cadastro   INTEGER REFERENCES login_usuario(id_usuario),
    data_cadastro      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE lote (
    id_lote                   SERIAL PRIMARY KEY,
    id_festa                  INTEGER NOT NULL REFERENCES festa(id_festa),
    numero_lote               INTEGER NOT NULL DEFAULT 1,
    valor_entrada             NUMERIC(10,2) NOT NULL,
    quantidade_max_ingressos  INTEGER NOT NULL DEFAULT 2,
    promo_casadinha           BOOLEAN NOT NULL DEFAULT FALSE,
    vendas_encerradas         BOOLEAN NOT NULL DEFAULT FALSE,
    data_cadastro             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id_festa, numero_lote)
);

CREATE TABLE participante (
    id_participante  SERIAL PRIMARY KEY,
    nome             VARCHAR(120) NOT NULL,
    email            VARCHAR(160),
    rg               VARCHAR(20),
    whatsapp         VARCHAR(20)
);

CREATE TABLE codigo_verificacao (
    id_codigo    SERIAL PRIMARY KEY,
    id_usuario   INTEGER NOT NULL REFERENCES login_usuario(id_usuario) ON DELETE CASCADE,
    tipo         VARCHAR(10) NOT NULL CHECK (tipo IN ('email', 'senha')),
    codigo_hash  VARCHAR(255) NOT NULL,
    expira_em    TIMESTAMPTZ NOT NULL,
    tentativas   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (id_usuario, tipo)
);

-- ---------------------------------------------------------------------------
-- 2. evento -> festa + lote
--    A festa é uma só: usa o nome/data/endereço/capacidade do lote mais recente
--    (o que valia na tela) e quem cadastrou/quando do primeiro lote.
-- ---------------------------------------------------------------------------

INSERT INTO festa (nome_festa, data_festa, hora_festa, endereco, bairro, capacidade_maxima, usuario_cadastro, data_cadastro)
SELECT ult.nome_evento, ult.data_evento, ult.hora_evento, ult.endereco, ult.bairro, ult.capacidade_maxima,
       pri.usuario_cadastro, pri.data_cadastro
FROM (SELECT * FROM evento ORDER BY data_cadastro DESC, id_evento DESC LIMIT 1) ult
CROSS JOIN (SELECT * FROM evento ORDER BY data_cadastro ASC, id_evento ASC LIMIT 1) pri;

-- Os lotes mantêm o mesmo id do antigo evento (os pedidos continuam apontando certo).
INSERT INTO lote (id_lote, id_festa, numero_lote, valor_entrada, quantidade_max_ingressos,
                  promo_casadinha, vendas_encerradas, data_cadastro)
SELECT e.id_evento, (SELECT id_festa FROM festa ORDER BY id_festa LIMIT 1), e.numero_lote, e.valor_entrada,
       e.quantidade_max_ingressos, e.promo_casadinha, e.vendas_encerradas, e.data_cadastro
FROM evento e;

SELECT setval(pg_get_serial_sequence('lote', 'id_lote'), COALESCE((SELECT MAX(id_lote) FROM lote), 1), (SELECT COUNT(*) > 0 FROM lote));

-- ---------------------------------------------------------------------------
-- 3. pedido -> pedido + participante + ingresso
-- ---------------------------------------------------------------------------

-- colunas temporárias só para ligar cada participante criado ao seu pedido
ALTER TABLE participante ADD COLUMN _pedido INTEGER, ADD COLUMN _papel VARCHAR(12);

-- titular: no pedido do slot 1 é a própria conta; no slot 2 e na cortesia são os
-- dados guardados em titular_*.
INSERT INTO participante (nome, email, rg, whatsapp, _pedido, _papel)
SELECT COALESCE(p.titular_nome, u.nome), COALESCE(p.titular_email, u.email), p.rg, p.whatsapp, p.id_pedido, 'titular'
FROM pedido p
JOIN login_usuario u ON u.id_usuario = p.id_usuario;

-- acompanhante: pedidos de 2 ingressos. Se por algum motivo o nome não foi guardado,
-- cria um acompanhante genérico para a contagem de ingressos continuar igual.
INSERT INTO participante (nome, email, rg, whatsapp, _pedido, _papel)
SELECT COALESCE(NULLIF(p.acompanhante_nome, ''), 'Acompanhante (nome não informado)'),
       p.acompanhante_email, p.acompanhante_rg, p.acompanhante_whatsapp, p.id_pedido, 'acompanhante'
FROM pedido p
WHERE p.quantidade >= 2 OR p.acompanhante_nome IS NOT NULL;

CREATE TABLE ingresso (
    id_ingresso      SERIAL PRIMARY KEY,
    id_pedido        INTEGER NOT NULL REFERENCES pedido(id_pedido) ON DELETE CASCADE,
    id_participante  INTEGER NOT NULL REFERENCES participante(id_participante),
    papel            VARCHAR(12) NOT NULL CHECK (papel IN ('titular', 'acompanhante')),
    UNIQUE (id_pedido, papel)
);

INSERT INTO ingresso (id_pedido, id_participante, papel)
SELECT _pedido, id_participante, _papel FROM participante WHERE _pedido IS NOT NULL;

ALTER TABLE participante DROP COLUMN _pedido, DROP COLUMN _papel;

-- expirado deixa de ser coluna e vira um status (a restrição antiga não conhece
-- 'expirado'; a nova é criada mais abaixo)
ALTER TABLE pedido DROP CONSTRAINT IF EXISTS pedido_status_pedido_check;
UPDATE pedido SET status_pedido = 'expirado' WHERE status_pedido = 'cancelado' AND expirado;

-- id_evento -> id_lote (mesmos números)
ALTER TABLE pedido ADD COLUMN id_lote INTEGER;
UPDATE pedido SET id_lote = id_evento;
ALTER TABLE pedido ALTER COLUMN id_lote SET NOT NULL;
ALTER TABLE pedido ADD CONSTRAINT pedido_id_lote_fkey FOREIGN KEY (id_lote) REFERENCES lote(id_lote);

DROP INDEX IF EXISTS ux_pedido_usuario_evento_slot_ativo;

ALTER TABLE pedido
    DROP COLUMN id_evento,
    DROP COLUMN titular_nome,
    DROP COLUMN titular_email,
    DROP COLUMN rg,
    DROP COLUMN whatsapp,
    DROP COLUMN quantidade,
    DROP COLUMN expirado,
    DROP COLUMN acompanhante_nome,
    DROP COLUMN acompanhante_email,
    DROP COLUMN acompanhante_rg,
    DROP COLUMN acompanhante_whatsapp;

ALTER TABLE pedido ADD CONSTRAINT pedido_status_pedido_check
    CHECK (status_pedido IN ('pendente', 'confirmado', 'cancelado', 'expirado'));

CREATE UNIQUE INDEX ux_pedido_usuario_lote_slot_ativo
    ON pedido (id_usuario, id_lote, slot)
    WHERE status_pedido NOT IN ('cancelado', 'expirado') AND NOT cortesia;

-- Apagar ingresso/pedido não deixa participante solto
CREATE OR REPLACE FUNCTION remover_participante_sem_ingresso() RETURNS trigger AS $$
BEGIN
    DELETE FROM participante
    WHERE id_participante = OLD.id_participante
      AND NOT EXISTS (SELECT 1 FROM ingresso WHERE id_participante = OLD.id_participante);
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ingresso_remove_participante
    AFTER DELETE ON ingresso
    FOR EACH ROW EXECUTE FUNCTION remover_participante_sem_ingresso();

-- ---------------------------------------------------------------------------
-- 4. Códigos de verificação: colunas de login_usuario -> codigo_verificacao
--    (só os que ainda estão pendentes, com hash e validade)
-- ---------------------------------------------------------------------------

INSERT INTO codigo_verificacao (id_usuario, tipo, codigo_hash, expira_em, tentativas)
SELECT id_usuario, 'email', email_verificacao_codigo_hash, email_verificacao_codigo_expira, email_verificacao_tentativas
FROM login_usuario
WHERE email_verificacao_codigo_hash IS NOT NULL AND email_verificacao_codigo_expira IS NOT NULL;

INSERT INTO codigo_verificacao (id_usuario, tipo, codigo_hash, expira_em, tentativas)
SELECT id_usuario, 'senha', reset_codigo_hash, reset_codigo_expira, reset_tentativas
FROM login_usuario
WHERE reset_codigo_hash IS NOT NULL AND reset_codigo_expira IS NOT NULL;

ALTER TABLE login_usuario
    DROP COLUMN email_verificacao_codigo_hash,
    DROP COLUMN email_verificacao_codigo_expira,
    DROP COLUMN email_verificacao_tentativas,
    DROP COLUMN reset_codigo_hash,
    DROP COLUMN reset_codigo_expira,
    DROP COLUMN reset_tentativas;

-- ---------------------------------------------------------------------------
-- 5. Tabela antiga e views de leitura
-- ---------------------------------------------------------------------------

DROP TABLE evento;

CREATE OR REPLACE VIEW v_evento AS
SELECT l.id_lote AS id_evento,
       l.id_lote,
       f.id_festa,
       f.nome_festa AS nome_evento,
       f.data_festa AS data_evento,
       f.hora_festa AS hora_evento,
       f.endereco,
       f.bairro,
       l.numero_lote,
       l.valor_entrada,
       l.quantidade_max_ingressos,
       f.capacidade_maxima,
       l.promo_casadinha,
       l.vendas_encerradas,
       f.usuario_cadastro,
       l.data_cadastro
FROM lote l
JOIN festa f ON f.id_festa = l.id_festa;

CREATE OR REPLACE VIEW v_pedido AS
SELECT p.id_pedido,
       p.id_usuario,
       p.id_lote AS id_evento,
       p.slot,
       p.valor,
       (SELECT COUNT(*)::int FROM ingresso i WHERE i.id_pedido = p.id_pedido) AS quantidade,
       CASE WHEN p.status_pedido = 'expirado' THEN 'cancelado' ELSE p.status_pedido END AS status_pedido,
       (p.status_pedido = 'expirado') AS expirado,
       p.cortesia,
       p.mp_payment_id,
       p.data_pedido,
       t.nome     AS titular_nome,
       t.email    AS titular_email,
       t.rg       AS rg,
       t.whatsapp AS whatsapp,
       a.nome     AS acompanhante_nome,
       a.email    AS acompanhante_email,
       a.rg       AS acompanhante_rg,
       a.whatsapp AS acompanhante_whatsapp
FROM pedido p
LEFT JOIN ingresso it       ON it.id_pedido = p.id_pedido AND it.papel = 'titular'
LEFT JOIN participante t    ON t.id_participante = it.id_participante
LEFT JOIN ingresso ia       ON ia.id_pedido = p.id_pedido AND ia.papel = 'acompanhante'
LEFT JOIN participante a    ON a.id_participante = ia.id_participante;

COMMIT;
