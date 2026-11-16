<div align="center">

# 🎃 Back to Halloween

**Convite, venda de ingressos por Pix e painel de portaria para festas pequenas e controladas.**

Cadastro com aprovação · Pix direto na chave, sem taxa de checkout · confirmação automática do pagamento · painel admin completo

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supabase-4169E1?logo=postgresql&logoColor=white)
![Mercado Pago](https://img.shields.io/badge/Mercado%20Pago-API%20de%20Pagamentos-00B1EA)
![Testes](https://img.shields.io/badge/testes-240%20verifica%C3%A7%C3%B5es-brightgreen)
![Status](https://img.shields.io/badge/status-encerrado-lightgrey)

<img src="docs/img/tela-inicial.png" alt="Tela inicial do Back to Halloween: TV de tubo, estantes de fitas VHS e o convite" width="420">

</div>

---

## Por que existe

Vender ingresso de uma festa pequena costuma acabar em planilha, print de
comprovante e conferência manual. O Back to Halloween resolve isso de ponta a
ponta: a pessoa se cadastra, o organizador aprova, ela paga um Pix e o sistema
**confirma sozinho**, controla as vagas e entrega a lista de quem entra.

Foi usado em produção para vender os ingressos da festa de Halloween que deu
nome ao projeto. O código fica aqui como vitrine de uma aplicação web completa,
do banco ao visual.

## O que entrega

| Para quem vai à festa | Para quem organiza |
|---|---|
| Cadastro por e-mail e senha (com código de 6 dígitos) ou conta Google | Aprovação de cada cadastro antes de liberar a compra |
| Compra em poucos toques, com promoção "casadinha" (2 ingressos por um valor fechado) | Pedidos com confirmação, cancelamento e ações em lote |
| Pix por QR Code ou "copia e cola", com contagem regressiva de 5 minutos | **Confirmação automática** do Pix pela API do Mercado Pago |
| Ingresso em formato de fita VHS, também por e-mail | Controle de vagas e de lotes (preço por lote, liga/desliga da promoção) |
| Histórico em "Meus pedidos" | Ingressos de cortesia, e-mail em massa e planilha `.xlsx` para a portaria |
| Visual retrô anos 80/90/2000 em todas as telas | Hierarquia de usuários (cliente, admin, superadmin) e despesas de material |

## Telas

<table>
<tr>
<td width="50%"><img src="docs/img/compra-ingresso.png" alt="Tela de compra de ingresso"><br><sub><b>Compra</b>: dados, quantidade e promoção casadinha</sub></td>
<td width="50%"><img src="docs/img/painel-admin-pedidos.png" alt="Painel admin de pedidos"><br><sub><b>Painel admin</b>: pedidos, vagas e confirmação (dados fictícios)</sub></td>
</tr>
</table>

## Como o pagamento funciona

O Pix cai **direto na chave** do organizador, sem o Checkout (que cobra ~1%). Como
não existe pedido no Mercado Pago, o sistema consulta a API de Pagamentos,
encontra o Pix aprovado com o mesmo valor depois da criação do pedido e
confirma. Um índice único no banco garante que **o mesmo pagamento nunca
confirma dois pedidos**, e uma falha da API **nunca cancela** o pedido de quem
já pagou.

```mermaid
sequenceDiagram
    participant C as Cliente
    participant S as Site
    participant MP as Mercado Pago
    C->>S: compra o ingresso
    S-->>C: QR Code Pix
    C->>MP: paga o Pix
    loop a cada 5 s
        C->>S: verificar pagamento
        S->>MP: Pix aprovados?
        MP-->>S: lista
    end
    S-->>C: ingresso confirmado + e-mail
```

Detalhes, regras e limites conhecidos em [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md).

## Integrações

| Serviço | Uso |
|---|---|
| **Mercado Pago** (API de Pagamentos) | Conciliação dos Pix pagos direto na chave: busca paginada, cache curto, timeout e pausa após falha |
| **Brevo** (e-mail transacional) | Códigos de verificação, ingresso confirmado e avisos aos admins, enviados em segundo plano |
| **Google OAuth** | Login com Google (Authlib) |

## Modelo de dados

Cinco tabelas em PostgreSQL: `login_usuario`, `hierarquia`, `evento` (cada linha
é um lote), `pedido` e `despesa_material`. As regras críticas moram no banco:

- `SELECT ... FOR UPDATE` no evento: duas compras simultâneas não passam da capacidade;
- índice único parcial `(id_usuario, id_evento, slot)`: sem pedido duplicado por duplo clique;
- índice único parcial em `mp_payment_id`: um pagamento, um pedido.

| Modelo conceitual | Modelo lógico |
|---|---|
| <img src="docs/img/modelo-conceitual.png" alt="Modelo conceitual"> | <img src="docs/img/modelo-logico.png" alt="Modelo lógico"> |

O modelo atual é pragmático e **não está em 3FN**. Uma proposta normalizada
(`festa`, `lote`, `participante`, `ingresso`, `codigo_verificacao`) está desenhada
em [conceitual](docs/img/proposta-3fn-conceitual.png) e
[lógico](docs/img/proposta-3fn-logico.png), mas não foi implementada.

## Segurança e desempenho

- Senhas com scrypt; códigos de 6 dígitos com tentativas reservadas de forma
  atômica no banco; limite de requisições por IP nos endpoints sensíveis.
- Cookies `SameSite=Lax`, checagem de `Origin` em POST, cabeçalhos de segurança,
  proteção contra open redirect e sessões revogadas ao trocar a senha.
- Pool de conexões, cache de 1 ano nos estáticos (URL com hash de conteúdo) e
  fontes hospedadas no próprio site.

## Começando

Pré-requisitos: Python 3.12 e um PostgreSQL.

```bash
pip install -r requirements.txt
psql "$DATABASE_URL" -f app/schema.sql     # cria as tabelas
cp .env.example .env                        # preencha os valores
cd app && python app.py                     # http://127.0.0.1:5000
```

Sem as credenciais do Google e do Brevo o site sobe, mas o login completo não
funciona. Para criar o primeiro admin: `python app/tornar_admin.py email@exemplo.com`
(nenhuma rota HTTP faz isso, de propósito).

### Configuração

Todas as variáveis estão comentadas em [`.env.example`](.env.example).

| Variável | Para quê |
|---|---|
| `DATABASE_URL` | Conexão com o PostgreSQL |
| `SECRET_KEY` | Assinatura da sessão do Flask |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Login com Google |
| `BREVO_API_KEY` / `BREVO_FROM_EMAIL` / `BREVO_FROM_NOME` | E-mails transacionais |
| `PIX_KEY_30` / `PIX_KEY_50` / `PIX_KEY_60` | Chave Pix de cada valor de pedido |
| `MERCADOPAGO_ACCESS_TOKEN` | Consulta dos Pix aprovados |
| `BASE_URL` | URL pública do site (links dos e-mails) |
| `DB_POOL_MAX` | Opcional: conexões por processo (padrão 5) |

### Testes

```bash
python tests/run_all.py
```

240 verificações em 9 suítes, sem precisar de banco: Postgres, Mercado Pago,
Brevo e Google são simulados. Cobrem conciliação do Pix, limites de vagas e de
conta, segurança dos códigos de login, falhas de integração e desempenho do
cache e do pool.

### Deploy

O [`render.yaml`](render.yaml) descreve o Web Service no Render. As variáveis
`sync: false` (segredos) são preenchidas à mão no painel. O Start Command deve
ser igual ao do arquivo:

```
cd app && gunicorn app:app --workers 2 --threads 4 --timeout 30
```

## Estrutura

```
app/
  app.py            # app Flask, rotas públicas, cabeçalhos e cache
  auth.py           # cadastro, login, códigos, Google OAuth
  pedidos.py        # compra, Pix e conciliação com o Mercado Pago
  admin.py          # painel administrativo
  email_utils.py    # e-mails (Brevo)
  limitador.py      # limite de requisições por IP
  db.py             # pool de conexões Postgres
  config.py         # variáveis de ambiente
  schema.sql        # schema do banco
  templates/        # páginas (Jinja)
  static/           # CSS, JS, fontes e imagens
docs/               # arquitetura e imagens (telas e modelos de dados)
tests/              # suítes de teste e runner (run_all.py)
render.yaml         # deploy no Render
```

## Documentação

- [Arquitetura](docs/ARQUITETURA.md): fluxo do Pix, regras no banco, segurança e limites conhecidos
- [Histórico de mudanças](CHANGELOG.md)

## Próximos passos (se o projeto continuasse)

- Webhook do Mercado Pago, para confirmar o Pix mesmo com a aba fechada.
- Normalizar o modelo de dados (3FN), como na proposta de `docs/`.
- Limitador de requisições com armazenamento compartilhado (Redis) para várias instâncias.
- Painel de portaria com leitura de QR Code no check-in.

## Licença

Todos os direitos reservados. Nenhuma licença de código aberto foi concedida.
