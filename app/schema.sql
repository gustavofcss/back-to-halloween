-- Schema do Back to Halloween, normalizado até a 3FN.
-- Rode uma vez contra o banco Postgres (local ou Render) antes do primeiro
-- deploy: psql "$DATABASE_URL" -f app/schema.sql
--
-- Banco que já existia na versão anterior (com a tabela `evento`): NÃO rode este
-- arquivo; rode app/migracao_3fn.sql, que converte os dados sem perder nada.
--
-- Tabelas:
--   login_usuario, codigo_verificacao, hierarquia   -> contas e papéis
--   festa, lote                                     -> o evento e seus lotes de preço
--   pedido, ingresso, participante                  -> vendas e pessoas
--   despesa_material                                -> despesas do evento
-- Views de leitura (v_evento, v_pedido): juntam as tabelas acima no formato que
-- as telas e os e-mails consomem. Só leitura: as escritas vão nas tabelas.

CREATE TABLE IF NOT EXISTS login_usuario (
    id_usuario       SERIAL PRIMARY KEY,
    nome             VARCHAR(120) NOT NULL,
    email            VARCHAR(160) NOT NULL UNIQUE,
    senha_hash       VARCHAR(255),        -- NULL se a conta só usa login Google
    google_id        VARCHAR(60) UNIQUE,  -- NULL se a conta só usa login por senha
    email_confirmado BOOLEAN NOT NULL DEFAULT FALSE,  -- aprovação manual do admin (fila "Cadastros pendentes")
    email_verificado BOOLEAN NOT NULL DEFAULT FALSE,  -- dono provou que é dono do e-mail (código); login Google já entra TRUE
    data_cadastro    TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_atualizacao TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Códigos de 6 dígitos (confirmar e-mail e "esqueci a senha"). Eram dois grupos
-- de colunas repetidos em login_usuario (email_verificacao_* e reset_*); aqui é
-- uma linha por código ativo. Sem linha = sem código pendente. Depois de 5 erros
-- o código é invalidado (auth.py, _MAX_TENTATIVAS_CODIGO).
CREATE TABLE IF NOT EXISTS codigo_verificacao (
    id_codigo    SERIAL PRIMARY KEY,
    id_usuario   INTEGER NOT NULL REFERENCES login_usuario(id_usuario) ON DELETE CASCADE,
    tipo         VARCHAR(10) NOT NULL CHECK (tipo IN ('email', 'senha')),
    codigo_hash  VARCHAR(255) NOT NULL,
    expira_em    TIMESTAMPTZ NOT NULL,
    tentativas   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (id_usuario, tipo)
);

-- Papel do usuário (cliente/adm/superadm) -- tabela separada (não uma coluna
-- em login_usuario) porque foi assim que o usuário desenhou no fluxograma.
-- "superadm" é um único usuário fixo, promovido só via script (ver
-- tornar_superadmin.py), nunca pela UI -- protegido contra rebaixamento
-- mesmo por outro admin (ver hierarquia_rebaixar em admin.py).
CREATE TABLE IF NOT EXISTS hierarquia (
    id          SERIAL PRIMARY KEY,
    id_usuario  INTEGER NOT NULL UNIQUE REFERENCES login_usuario(id_usuario) ON DELETE CASCADE,
    hierarquia  VARCHAR(20) NOT NULL DEFAULT 'cliente' CHECK (hierarquia IN ('cliente', 'adm', 'superadm'))
);

-- A festa: o que vale pro evento inteiro (nome, data, hora, endereço, capacidade).
-- Antes isso se repetia em cada lote (tabela `evento`) e o código precisava
-- copiar os dados a cada lote novo e atualizar só o mais recente.
CREATE TABLE IF NOT EXISTS festa (
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

-- O lote: o que muda com o preço (1º lote R$ 30, 2º lote R$ 50...). O lote mais
-- recente é o que está à venda.
CREATE TABLE IF NOT EXISTS lote (
    id_lote                   SERIAL PRIMARY KEY,
    id_festa                  INTEGER NOT NULL REFERENCES festa(id_festa),
    numero_lote               INTEGER NOT NULL DEFAULT 1,
    valor_entrada             NUMERIC(10,2) NOT NULL,
    quantidade_max_ingressos  INTEGER NOT NULL DEFAULT 2,
    -- Promoção casadinha: 2 ingressos no mesmo pedido por config.PROMO_CASADINHA_VALOR.
    promo_casadinha           BOOLEAN NOT NULL DEFAULT FALSE,
    -- Com TRUE ninguém cria pedido novo (quem já tem pedido pendente ainda paga).
    vendas_encerradas         BOOLEAN NOT NULL DEFAULT FALSE,
    data_cadastro             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id_festa, numero_lote)
);

-- O pedido guarda só o que é do pedido: quem comprou, qual lote, quanto foi
-- pago, em que situação está e qual pagamento Pix o quitou. Quem vai usar cada
-- ingresso mora em ingresso/participante.
--   * valor é o preço pago NA ÉPOCA (histórico), de propósito: não se recalcula
--     a partir do lote, que pode mudar depois.
--   * a quantidade de ingressos é a contagem de linhas em `ingresso` (não é coluna).
--   * 'expirado' é um status (cancelamento automático por estourar a janela de
--     pagamento de 5 min), não um booleano à parte.
--   * slot 1 = o pedido principal da conta; slot 2 = um 2º pedido pra OUTRA
--     pessoa. No máximo 2 pedidos ativos por conta+lote (índice abaixo).
--   * cortesia: ingresso criado pelo admin, valor 0, já nasce 'confirmado'.
--     id_usuario é o ADMIN que criou; o convidado é o participante do ingresso.
CREATE TABLE IF NOT EXISTS pedido (
    id_pedido      SERIAL PRIMARY KEY,
    id_usuario     INTEGER NOT NULL REFERENCES login_usuario(id_usuario) ON DELETE CASCADE,
    id_lote        INTEGER NOT NULL REFERENCES lote(id_lote),
    slot           INTEGER NOT NULL DEFAULT 1 CHECK (slot IN (1, 2)),
    valor          NUMERIC(10,2) NOT NULL,
    status_pedido  VARCHAR(20) NOT NULL DEFAULT 'pendente'
                   CHECK (status_pedido IN ('pendente', 'confirmado', 'cancelado', 'expirado')),
    cortesia       BOOLEAN NOT NULL DEFAULT FALSE,
    mp_payment_id  VARCHAR(40),  -- id do pagamento Pix aprovado no Mercado Pago
    data_pedido    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Quem vai à festa. Uma pessoa por linha: nome, e-mail, RG e WhatsApp dependem da
-- pessoa, não do pedido (antes eram colunas repetidas "titular_*" e "acompanhante_*").
CREATE TABLE IF NOT EXISTS participante (
    id_participante  SERIAL PRIMARY KEY,
    nome             VARCHAR(120) NOT NULL,
    email            VARCHAR(160),
    rg               VARCHAR(20),
    whatsapp         VARCHAR(20)
);

-- Um ingresso liga um pedido a um participante. Cada pedido tem no máximo um
-- titular e um acompanhante.
CREATE TABLE IF NOT EXISTS ingresso (
    id_ingresso      SERIAL PRIMARY KEY,
    id_pedido        INTEGER NOT NULL REFERENCES pedido(id_pedido) ON DELETE CASCADE,
    id_participante  INTEGER NOT NULL REFERENCES participante(id_participante),
    papel            VARCHAR(12) NOT NULL CHECK (papel IN ('titular', 'acompanhante')),
    UNIQUE (id_pedido, papel)
);

-- Apagar um ingresso (ou um pedido, que apaga seus ingressos em cascata) não pode
-- deixar participante solto: remove o participante que ficou sem nenhum ingresso.
CREATE OR REPLACE FUNCTION remover_participante_sem_ingresso() RETURNS trigger AS $$
BEGIN
    DELETE FROM participante
    WHERE id_participante = OLD.id_participante
      AND NOT EXISTS (SELECT 1 FROM ingresso WHERE id_participante = OLD.id_participante);
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ingresso_remove_participante ON ingresso;
CREATE TRIGGER trg_ingresso_remove_participante
    AFTER DELETE ON ingresso
    FOR EACH ROW EXECUTE FUNCTION remover_participante_sem_ingresso();

-- "Até 2 pedidos por conta" era só checado com SELECT-antes-de-INSERT; sem
-- trava no banco, dois envios concorrentes do mesmo usuário (duplo clique, duas
-- abas) passavam os dois. Índice único parcial (só pedidos ativos e não
-- cortesia): no máximo um pedido ativo por conta+lote+slot. O Postgres rejeita o
-- INSERT concorrente com UniqueViolation, que a rota captura.
CREATE UNIQUE INDEX IF NOT EXISTS ux_pedido_usuario_lote_slot_ativo
    ON pedido (id_usuario, id_lote, slot)
    WHERE status_pedido NOT IN ('cancelado', 'expirado') AND NOT cortesia;

-- Pix direto na chave (pedidos.py, _tentar_conciliar_pix_direto): o mesmo
-- pagamento real do Mercado Pago nunca pode ser reivindicado por dois pedidos
-- (2 pessoas pagando o mesmo valor quase juntas). Parcial: mp_payment_id fica
-- NULL até alguém pagar.
CREATE UNIQUE INDEX IF NOT EXISTS ux_pedido_mp_payment_id ON pedido (mp_payment_id) WHERE mp_payment_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS despesa_material (
    id                SERIAL PRIMARY KEY,
    nome_material     VARCHAR(160) NOT NULL,
    valor             NUMERIC(10,2) NOT NULL,
    descricao         TEXT,
    data_compra       DATE NOT NULL DEFAULT CURRENT_DATE,
    usuario_cadastro  INTEGER REFERENCES login_usuario(id_usuario)
);

-- ---------------------------------------------------------------------------
-- Views de leitura (as telas, os e-mails e a planilha leem daqui)
-- ---------------------------------------------------------------------------

-- O "evento" que o código lê: o lote com os dados da festa.
-- id_evento = id_lote (o evento à venda é sempre o lote mais recente).
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

-- O pedido completo: situação, titular e acompanhante. status_pedido mostra
-- 'cancelado' para o pedido expirado (e expirado = TRUE), que é como as telas
-- sempre enxergaram; para filtrar na tabela pedido, use os dois status.
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
