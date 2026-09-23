# Memória do projeto — Horror Night Party

## Convenção AJAX do admin (definida em 2026-09-22, fase "admin sem recarregar página")

Esta é a convenção obrigatória para TODA mutação em `/admin/*`. Rotas novas
devem seguir o mesmo formato — não invente um segundo padrão.

### A. Detecção AJAX

As rotas de mutação do admin são **JSON-only** e **exigem** o header
`X-Requested-With: XMLHttpRequest`. Sem o header a rota responde
`400 {"ok": false, "codigo": "regra"}`.

Motivo: (1) o `admin_required` precisa de um discriminador de qualquer jeito
(as páginas GET do admin continuam redirecionando para o login em HTML,
as mutações precisam devolver 401 JSON); (2) exigir um header customizado
é, por si só, uma mitigação de CSRF — um `<form>` cross-site não consegue
definir headers e um `fetch` cross-origin com header customizado dispara
preflight CORS, que o app não responde; (3) não existe fallback sem JS a
preservar, porque os templates do admin foram convertidos no mesmo passe —
manter um branch HTML morto só apodreceria.

### B. Envelope JSON (único para todas as rotas, single e lote)

Sucesso (HTTP 200):

```json
{
  "ok": true,
  "aplicados": [ { "id": 12, "...campos específicos da rota": null } ],
  "ignorados": [ { "id": 13, "motivo": "texto pt-BR pro usuário" } ],
  "totais":    { "...agregados da página, recalculados após a mutação": 0 },
  "aviso":     null
}
```

- `aplicados` — ids que MUDARAM DE VERDADE no banco (rowcount real, nunca
  otimismo). O JS só mexe no DOM com base nessa lista.
- `ignorados` — ids pedidos que não mudaram, com o motivo legível.
- `aplicados` vazio + `ignorados` cheio ainda é `ok: true` / HTTP 200. O JS
  mostra os motivos no banner de aviso e **não toca no DOM**.
- `totais` — agregados que a página exibe (KPIs, contadores "N pessoa(s)",
  "N resultado(s)"), recalculados DEPOIS da mutação e DENTRO do mesmo
  `with db.get_conn()` (zero conexão extra ao Postgres).
- `aviso` — substitui o `flash()`. Falha de envio de e-mail é aviso, NUNCA
  `ok: false` — o dado já foi gravado e comitado.

Erro (HTTP 4xx/5xx):

```json
{ "ok": false, "erro": "mensagem pt-BR", "codigo": "auth|proibido|regra|nao_encontrado|servidor", "login_url": "/login" }
```

`login_url` só aparece quando `codigo == "auth"`.

### C. Status codes, sessão expirada e CSRF

| Situação | Status | codigo |
|---|---|---|
| Sucesso (inclusive 0 linhas afetadas) | 200 | — |
| Regra de negócio / request inválido (rebaixar a si mesmo, sem header, ids inválidos) | 400 | `regra` |
| Sessão expirada / não logado | 401 | `auth` |
| Logado mas não é adm | 403 | `proibido` |
| Linha não existe | 404 | `nao_encontrado` |
| Erro não tratado | 500 | `servidor` |

**Sessão expirada**: `admin_required` (app/auth.py) redireciona 302 para o
login. Um `fetch` segue o redirect e recebe o HTML do login com status 200 —
o JS interpretaria como sucesso. Por isso `app/admin.py` tem um
`@admin_bp.after_request` que, quando a request é AJAX e a resposta é um 302
para o login, reescreve para `401 {"codigo": "auth", "login_url": ...}`.
O JS, ao ver 401 com `login_url`, faz `window.location.href = login_url`
(navegação inteira, não patch de DOM).

**CSRF**: o projeto **não tem** nenhum token CSRF (sem Flask-WTF, sem token
manual) e **não define** `SESSION_COOKIE_SAMESITE` nem `SESSION_COOKIE_SECURE`.
A única proteção hoje é o default `SameSite=Lax` que os navegadores aplicam
sozinhos quando o atributo está ausente. A troca de `<form method=post>` para
`fetch` **não afrouxa nada** e na prática aperta: todo `fetch` usa
`credentials: 'same-origin'` e o header obrigatório `X-Requested-With`, que
um form cross-site não consegue enviar. Adicionar um framework de CSRF de
verdade continua sendo dívida em aberto (fora do escopo deste passe).

### Regra de ouro herdada deste passe

Endpoint de mutação **precisa reportar linhas realmente afetadas**. Vários
DELETE/UPDATE do admin têm guarda no `WHERE` (`AND status_pedido =
'cancelado'`, `AND email_confirmado = FALSE`, `AND ... hierarquia =
'cliente'`) e antes respondiam sucesso mesmo com 0 linhas. Com redirect isso
só parecia "não aconteceu nada"; com AJAX vira bug de UX de verdade (o JS
apaga a linha da tela e o registro continua no banco). Use `cur.rowcount`
ou um `SELECT` prévio do conjunto elegível.

### Fatos do stack (verificados)

- `db.get_conn()` (app/db.py) é um contextmanager que **commita na saída
  limpa** e faz rollback na exceção. Portanto o UPDATE já está comitado
  quando o e-mail é enviado depois do bloco `with` — falha de e-mail não
  desfaz a gravação, que é o comportamento desejado.
- Uma conexão psycopg2 NOVA por `get_conn()` (sem pool). O
  `context_processor` do `app.py` + o `admin_required` abrem 2–3 conexões
  extras por request. Agrupe tudo de uma rota em um único `with`.
- `jsonify` não serializa `Decimal` nem `datetime` (o Postgres devolve
  `numeric` como `Decimal`). Valores de dinheiro vão como string formatada
  (`f"{v:.2f}"`), datas como string já formatada, para bater exatamente com
  o que o Jinja imprimia.

## Implementação (2026-09-23)

O squad multi-agente bateu no limite semanal de uso logo depois de fechar a
convenção acima (nenhum código tinha sido escrito ainda). O resto -- backend
(`app/admin.py`), frontend (`_layout.html` + templates de pedidos/cadastros/
hierarquia/material) e o fix de CSS da Hierarquia -- foi implementado
diretamente na sessão principal, sem reabrir o swarm, pra não gastar cota de
novo só relendo arquivos que já tinham sido lidos.

Decisões tomadas nesse passe, pra quem for mexer depois:

- **`eventos.html` ficou de fora** (cadastrar lote novo / atualizar
  capacidade continuam `<form>` clássico com reload). São ações pontuais
  (não repetidas feito confirmar/cancelar pedido), o usuário não reclamou
  especificamente delas, e a queixa era sobre a lista/lote -- manter fora do
  escopo evitou inflar o passe sem necessidade.
- **Hierarquia: `container-type: inline-size` em `.admin-group__row`**, não
  mais um `@media (max-width: 480px)`. O bug real era a LARGURA DA COLUNA do
  card-grid (`auto-fit, minmax(240px, 1fr)`), não a largura da janela -- em
  ~1080px de janela o grid ainda criava colunas estreitas o bastante pra
  sobrepor os botões no nome/e-mail (achado real, print do usuário). Um
  `@container` na própria linha resolve mobile E desktop-intermediário com
  uma regra só, sem adivinhar breakpoints de viewport.
- **Promover/rebaixar/remover na Hierarquia só tiram a linha da tela** (não
  tentam reconstruir a linha no outro grupo via JS). A pessoa aparece no
  grupo novo no próximo refresh (até 60s, ou navegando pra outra aba e
  voltando). Reconstruir o HTML da linha nova no cliente duplicaria a lógica
  do macro `initials()` e dos botões por hierarquia -- risco de desalinhar
  do que o servidor realmente renderiza.
- **Achado real corrigido de passagem**: o auto-refresh de 60s em
  `_layout.html` (de antes desta fase) referenciava a variável `overlay` de
  outro IIFE, fora de escopo -- lançava `ReferenceError` toda vez e NUNCA
  recarregava nada. Estava morto desde que foi escrito. Corrigido junto por
  já estar mexendo nesse exato trecho.
- **Não testado com banco real** (sem `DATABASE_URL`/`.env` disponíveis
  nesta sessão). Validado com: `py_compile`, parse Jinja de todos os
  templates tocados, e um Flask test client (sem DB) cobrindo os caminhos
  que não dependem de sessão autenticada -- principalmente o
  `after_request` que reescreve redirect-pro-login em 401 JSON (o mecanismo
  mais arriscado de toda a mudança) e o handler de 500. Os caminhos que
  dependem de admin autenticado (403 proibido, 400 sem header, as próprias
  queries) foram revisados por leitura, não exercitados de ponta a ponta.
