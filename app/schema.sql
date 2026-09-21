-- Schema do Horror Night Party, baseado no fluxograma do usuário.
-- Rode uma vez contra o banco Postgres (local ou Render) antes do primeiro
-- deploy: psql "$DATABASE_URL" -f app/schema.sql

CREATE TABLE IF NOT EXISTS login_usuario (
    id_usuario       SERIAL PRIMARY KEY,
    nome             VARCHAR(120) NOT NULL,
    email            VARCHAR(160) NOT NULL UNIQUE,
    senha_hash       VARCHAR(255),        -- NULL se a conta só usa login Google
    google_id        VARCHAR(60) UNIQUE,  -- NULL se a conta só usa login por senha
    email_confirmado BOOLEAN NOT NULL DEFAULT FALSE,
    data_cadastro    TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_atualizacao TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Papel do usuário (cliente/adm) -- tabela separada (não uma coluna em
-- login_usuario) porque foi assim que o usuário desenhou no fluxograma.
CREATE TABLE IF NOT EXISTS hierarquia (
    id          SERIAL PRIMARY KEY,
    id_usuario  INTEGER NOT NULL UNIQUE REFERENCES login_usuario(id_usuario) ON DELETE CASCADE,
    hierarquia  VARCHAR(20) NOT NULL DEFAULT 'cliente' CHECK (hierarquia IN ('cliente', 'adm'))
);

CREATE TABLE IF NOT EXISTS pedido (
    id_pedido      SERIAL PRIMARY KEY,
    id_usuario     INTEGER NOT NULL REFERENCES login_usuario(id_usuario) ON DELETE CASCADE,
    rg             VARCHAR(20),
    whatsapp       VARCHAR(20),
    valor          NUMERIC(10,2) NOT NULL,
    quantidade     INTEGER NOT NULL CHECK (quantidade > 0),
    status_pedido  VARCHAR(20) NOT NULL DEFAULT 'pendente' CHECK (status_pedido IN ('pendente', 'confirmado', 'cancelado')),
    data_pedido    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS despesa_material (
    id            SERIAL PRIMARY KEY,
    nome_material VARCHAR(160) NOT NULL,
    valor         NUMERIC(10,2) NOT NULL,
    descricao     TEXT,
    data_compra   DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS evento (
    nome_evento             VARCHAR(160) PRIMARY KEY,
    valor_entrada           NUMERIC(10,2) NOT NULL,
    quantidade_max_ingressos INTEGER NOT NULL DEFAULT 2
);

-- Dado inicial do evento (ajuste valor/data/local antes do deploy).
INSERT INTO evento (nome_evento, valor_entrada, quantidade_max_ingressos)
VALUES ('Horror Night Party', 30.00, 2)
ON CONFLICT (nome_evento) DO NOTHING;
