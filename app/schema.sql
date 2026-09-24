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
    reset_codigo_hash    VARCHAR(255),        -- hash do código de "esqueci a senha", NULL fora do fluxo
    reset_codigo_expira  TIMESTAMPTZ,         -- validade do código acima (15 min)
    data_cadastro    TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_atualizacao TIMESTAMPTZ NOT NULL DEFAULT now()
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

-- Um admin pode cadastrar mais de um evento (ex.: festas futuras) -- por isso
-- "evento" tem id próprio e quem cadastrou, em vez de nome_evento fixo como
-- chave e uma linha só. "numero_lote" cobre preços por lote (1º lote, 2º
-- lote etc.) dentro do mesmo evento.
CREATE TABLE IF NOT EXISTS evento (
    id_evento                 SERIAL PRIMARY KEY,
    nome_evento               VARCHAR(160) NOT NULL,
    data_evento               DATE,
    hora_evento               TIME,
    numero_lote               INTEGER NOT NULL DEFAULT 1,
    valor_entrada             NUMERIC(10,2) NOT NULL,
    quantidade_max_ingressos  INTEGER NOT NULL DEFAULT 2,
    capacidade_maxima         INTEGER NOT NULL DEFAULT 60,
    usuario_cadastro          INTEGER REFERENCES login_usuario(id_usuario),
    data_cadastro             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pedido (
    id_pedido      SERIAL PRIMARY KEY,
    id_usuario     INTEGER NOT NULL REFERENCES login_usuario(id_usuario) ON DELETE CASCADE,
    id_evento      INTEGER NOT NULL REFERENCES evento(id_evento),
    rg             VARCHAR(20),
    whatsapp       VARCHAR(20),
    valor          NUMERIC(10,2) NOT NULL,
    quantidade     INTEGER NOT NULL CHECK (quantidade > 0),
    status_pedido  VARCHAR(20) NOT NULL DEFAULT 'pendente' CHECK (status_pedido IN ('pendente', 'confirmado', 'cancelado')),
    mp_payment_id     VARCHAR(40),  -- id do pagamento Pix criado no Mercado Pago
    mp_preference_id  VARCHAR(40),  -- id da preference do Checkout Pro (cartão)
    -- 2º ingresso (quantidade = 2) pode ser pra um acompanhante -- não
    -- precisa ser a mesma pessoa que está logada/comprando. NULL quando
    -- quantidade = 1 (só o titular).
    acompanhante_nome      VARCHAR(120),
    acompanhante_email     VARCHAR(160),
    acompanhante_rg        VARCHAR(20),
    acompanhante_whatsapp  VARCHAR(20),
    data_pedido    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- "Um pedido por pessoa" (app/pedidos.py, rota /comprar) era só checado com
-- SELECT-antes-de-INSERT -- sem essa trava no banco, dois envios concorrentes
-- do mesmo usuário (duplo clique, duas abas) podiam ler "zero pedidos" os
-- dois ANTES de qualquer um dos dois commitar o INSERT, e os dois passavam
-- (TOCTOU real, achado nesta revisão de segurança). Índice único parcial
-- (só entre pedidos não cancelados) faz o Postgres rejeitar o segundo INSERT
-- concorrente com uma UniqueViolation, que a rota captura.
CREATE UNIQUE INDEX IF NOT EXISTS ux_pedido_usuario_evento_ativo ON pedido (id_usuario, id_evento) WHERE status_pedido != 'cancelado';

CREATE TABLE IF NOT EXISTS despesa_material (
    id                SERIAL PRIMARY KEY,
    nome_material     VARCHAR(160) NOT NULL,
    valor             NUMERIC(10,2) NOT NULL,
    descricao         TEXT,
    data_compra       DATE NOT NULL DEFAULT CURRENT_DATE,
    usuario_cadastro  INTEGER REFERENCES login_usuario(id_usuario)
);
