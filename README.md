# Horror Night Party

Site pessoal de convite e venda de ingresso da festa de Halloween. Login por
conta (e-mail/senha, com confirmação por e-mail) ou Google, compra de
ingresso com pagamento via Pix (confirmado manualmente), painel admin pra
gerenciar pedidos, hierarquia de usuários e despesas de material.

Projeto isolado -- banco Postgres próprio, sem relação com nenhum outro
projeto ou credencial de trabalho.

## Rodando localmente

1. `pip install -r requirements.txt`
2. Suba um Postgres local (ou use a "External Database URL" de um banco
   Render de dev) e aplique o schema: `psql "$DATABASE_URL" -f app/schema.sql`
3. Copie `.env.example` pra `.env` e preencha os valores (ver comentários no
   próprio arquivo -- precisa de credenciais Google OAuth e SMTP pra login
   completo funcionar; sem isso, o site sobe mas login não funciona).
4. `cd app && python app.py` -- sobe em http://127.0.0.1:5000

## Deploy (Render)

`render.yaml` já define o Web Service + banco Postgres. No painel do Render:
"New" > "Blueprint", aponte pro repositório -- ele lê o `render.yaml`
automaticamente. Depois do primeiro deploy, preencha manualmente (painel >
Environment) as variáveis marcadas `sync: false` no `render.yaml`
(credenciais Google, SMTP, chave Pix) -- essas não vão pro Render sozinhas,
de propósito, por serem segredos.

Depois do banco criado, rode o `app/schema.sql` contra a "External Database
URL" do banco Render (uma vez só, antes do primeiro uso real).

## Estrutura

```
app/
  app.py            # Flask app
  config.py         # variáveis de ambiente
  db.py             # conexão Postgres
  schema.sql        # schema do banco
  templates/        # páginas (Jinja)
  static/           # CSS/JS/assets
front/              # mockups de design (referência, não faz parte do app publicado)
```
