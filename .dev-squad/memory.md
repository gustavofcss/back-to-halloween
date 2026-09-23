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

## QA funcional (2026-09-23) -- passe de verificação em runtime

Sem Postgres disponível nesta sessão também. Cobertura obtida:

- **Import + registro de rotas**: `import app` limpo com `DATABASE_URL`/
  `SECRET_KEY` fake; as 20 rotas de `/admin/*` registram sem erro.
- **Flask test client, caminhos que não tocam o banco** (via
  `auth.current_user` mockado pra pular a query de admin_required, já que
  não há Postgres pra autenticar de verdade):
  - GET anônimo nas 6 páginas do admin -> 302 pro login. OK.
  - POST anônimo + `X-Requested-With` nas 15 rotas de mutação -> 401 JSON
    `codigo: auth` + `login_url`. OK -- é o mecanismo mais arriscado da
    fase anterior (`after_request` reescrevendo redirect em 401) e
    funciona nas 15 rotas, não só numa amostra.
  - POST anônimo SEM o header -> 302 clássico (não veio 401, correto --
    o `after_request` só reescreve request AJAX).
  - GET de página (não mutação) com header AJAX, anônimo -> também vira
    401 JSON (o `after_request` é blueprint-wide, não só nas rotas com
    `@_exigir_ajax`). Comportamento correto, verificado.
  - 403 (autenticado, hierarquia cliente) e 400 sem header (autenticado
    admin) -> corretos, com `auth.current_user` mockado.
  - Guardas de auto-rebaixar/auto-apagar em `/hierarquia/<id>/rebaixar` e
    `/remover` -> 400 `regra`, checados via `session["user_id"]` (sem
    tocar banco). OK.
  - Corpo malformado nas 6 rotas `*-lote` (JSON inválido, lista solta,
    `ids` ausente, `ids` não-lista, itens não-numéricos, corpo vazio) ->
    400 `codigo: regra` nas 6 rotas x 6 variações = 36 combinações, todas
    OK, nenhuma vazou 500.
  - **Achado sem severidade de bug**: `_ids_do_body` usa `int(i)` em cada
    item de `ids` -- um float tipo `2.5` NÃO dá erro, vira `2` (trunca
    silenciosamente, é o comportamento built-in de `int()`). Não é
    alcançável pela UI real (os checkboxes sempre mandam
    `parseInt(cb.value, 10)` de ids inteiros vindos do banco), documentado
    aqui só pra quem for endurecer validação de API no futuro.
- **Rastreamento JS <-> JSON do backend** (leitura cruzada de
  `admin.py` contra os 4 templates): todos os shapes de `aplicados`
  (`id` + `status`/`hierarquia` opcional) e todas as chaves de `totais`
  batem exatamente com o que cada template lê via `data-admin-total` ou
  `item.status`/`item.id`. Nenhum mismatch de campo encontrado -- a
  reescrita das rotas em lote pra `UPDATE/DELETE ... RETURNING` (evitar
  SELECT-then-mutate com janela de corrida) não alterou o formato do
  envelope.
- **CSS `@container` da Hierarquia**: `.admin-group__row-inner` no HTML
  bate exatamente com o seletor usado no `@container (max-width: 480px)`
  em `style.css` -- confirmado por leitura estrutural cruzada (não dá pra
  screenshot sem servidor rodando com dado real, mas a estrutura de
  aninhamento e as classes batem 1:1, então a regra não é um no-op
  silencioso).
- **Bug real encontrado e corrigido**: em `pedidos.html`, `cadastros.html`
  e `hierarquia.html`, o contador "Selecionar todos" comparava
  `marcados === itens.length`, onde `itens` é um `NodeList` estático
  capturado UMA VEZ no load da página. Depois que `admin:sucesso` remove
  uma linha do DOM (apagar pedido cancelado, confirmar/recusar cadastro,
  promover/rebaixar/remover hierarquia), `itens.length` não diminui --
  fica "preso" no total original. Resultado: depois da primeira remoção de
  linha, "Selecionar todos" nunca mais marca como totalmente selecionado
  mesmo com todas as caixinhas restantes marcadas (bug cosmético de
  seleção múltipla, não afeta os dados enviados ao servidor). Corrigido
  recalculando o total ao vivo via `document.querySelectorAll(...).length`
  dentro de `atualizar()`, nas 3 páginas. Verificado: `node --check` nos
  3 blocos de script depois da correção, sem erro de sintaxe.

## QA funcional do fluxo de compra (2026-09-23) -- commit 02b0ab5

Sem Postgres disponível também (mesma limitação). Verificação de
`app/pedidos.py`, `app/auth.py`, `comprar.html`, `pagamento.html`:

- **Boot + rotas**: `import app` limpo, as 9 rotas de `pedidos.py`
  registram (inclusive as novas). Confirmado nas 3 revisões do arquivo
  (ver nota sobre edição concorrente abaixo).
- **As 7 rotas `login_required` de `pedidos.py`** (`/comprar` GET+POST,
  `/pagamento/<id>` GET, `/pagamento/<id>/confirmar`,
  `/pagamento/<id>/verificar`, `/pagamento/<id>/cartao`, `/meus-pedidos`)
  -> 302 `/login?next=...` pra request anônima, sem exceção. Todo acesso
  a banco fica atrás de `login_required`, que corta antes de qualquer
  `db.get_conn()`.
- **Bug real encontrado e corrigido**: `_RG_CARACTERES_RE`/`_RG_RE`/
  `_WHATSAPP_CARACTERES_RE`/`_WHATSAPP_RE` (`app/pedidos.py`) usavam `\d`
  sem `re.ASCII`. Em Python 3, `\d` sem essa flag casa qualquer dígito
  Unicode (categoria `Nd`), não só `0-9` ASCII -- um RG ou WhatsApp com
  dígito fullwidth (`１２３４５６７８９`) ou arábico-índico (`٩`) passava as
  duas etapas do validador inteiro e ia pro banco como caractere
  não-ASCII, quebrando exatamente o "só números" que o comentário do
  código promete. Confirmado executando `_validar_rg`/`_validar_whatsapp`
  direto com esses inputs antes/depois da correção. Fix: `re.ASCII` nos
  quatro `re.compile`.
- **`_confirmar_se_aprovado`/`_verificar_e_confirmar_pagamento`**
  testados com cursor fake (controla `rowcount`) e `_consultar_pagamento`/
  `enviar_confirmacao_pedido` mockados -- 20 asserções, todas OK:
  status não aprovado (sem UPDATE, sem e-mail); aprovado com rowcount=1
  (UPDATE + e-mail); aprovado com rowcount=0 simulando corrida onde outro
  request já confirmou (sem e-mail duplicado); pedido já `confirmado`
  (nunca chama a API do MP); sem `mp_payment_id` (idem); sem
  `MERCADOPAGO_ACCESS_TOKEN` (idem); falha da API do MP ou do envio de
  e-mail não propaga exceção (fica só no `logger.warning`).
- **Templates**: `ja_tem_pedido` (local, só no branch de bloqueio) e
  `tem_pedido` (global, `context_processor` de `app.py`, mesma query
  `status_pedido != 'cancelado'`) não têm sobreposição -- o guard
  `{% if tem_pedido and not ja_tem_pedido %}` em `comprar.html` evita
  link "Meus pedidos" duplicado quando o branch de bloqueio já mostra o
  seu próprio link. `limite_atingido` foi limpo, sem resíduo.
- **pattern/inputmode/maxlength client-side (RG e WhatsApp) são MAIS
  estritos que o validador do servidor** (servidor aceita pontuação de
  formatação e normaliza; client `pattern` exige só dígitos crus) --
  direção seguramente assimétrica (nunca deixa passar no client algo que
  o servidor rejeitaria) e combina com o placeholder "Só números". Não é
  bug, é reforço deliberado de UX; documentado aqui só porque era um dos
  pontos pedidos na verificação.
- **Achado sem ação**: `mp_configurado`, passado por `/comprar` GET e por
  `/pagamento/<id>` GET, não é referenciado em `pagamento.html` nenhuma
  vez -- variável morta, pré-existente (não faz parte do diff de
  02b0ab5), inofensiva.
- **Edição concorrente durante esta sessão de QA**: `app/pedidos.py`,
  `app/schema.sql` e `app/auth.py` mudaram no disco 3x enquanto essa
  verificação rodava (fora do commit 02b0ab5 que era o alvo) -- outro
  processo/sessão aplicou, na ordem: (1) índice único parcial
  `ux_pedido_usuario_evento_ativo` em `schema.sql` + captura de
  `psycopg2.errors.UniqueViolation` em `/comprar` pra fechar uma corrida
  TOCTOU real (dois cliques/abas concorrentes liam "zero pedidos" antes
  de qualquer INSERT comitar); (2) `_next_seguro()` em `auth.py` pra
  fechar um open redirect real em `next_url` (login e callback do Google
  não validavam que `next` fosse um caminho interno). Nenhuma dessas
  mudanças fazia parte do escopo pedido nesta verificação (só 02b0ab5),
  mas foram reverificadas de passagem (boot + rotas anônimas) porque
  aconteceram nos mesmos arquivos -- sem regressão encontrada.

## Reskin do painel admin pro mockup "Painel Admin v2" (2026-09-23)

Só CSS (`app/static/css/style.css`, bloco `.admin-shell` e seletores
`.admin-*`) + `app/templates/admin/_layout.html` (markup do `<nav>`). Nenhum
template de conteúdo (pedidos/cadastros/hierarquia/material/eventos) mudou,
`app/admin.py` **não foi tocado** (restrição explícita do pedido), toda a
convenção AJAX/JS/data-attributes documentada acima continua igual.

- **Tokens `--a-cream*`/`--a-ink*` inverteram de sentido**: antes eram
  card CLARO (creme) + texto ESCURO; agora são card ESCURO (`rgba(15,10,20,
  .9)` etc.) + texto CLARO (`#F4ECE0` etc.), pra bater com o mockup e com a
  sidebar (que já era escura). `--a-accent`/`--a-accent-dark` alinhados aos
  tokens globais do site (`--accent`/`--accent-dark`). Fontes trocadas de
  Bricolage Grotesque/DM Sans pra Baloo 2/Poppins (mesmas do resto do site,
  já self-hosted via @font-face no mesmo arquivo -- zero fonte nova).
- **Qualquer regra admin com cor HARDCODED (não `var(--a-*)`) tunada pro
  card claro de antes precisou de ajuste manual** -- a troca de token sozinha
  não bastava. Achados corrigidos: `.admin-pill` (selos de status -- fundo
  translúcido claro + texto escuro saturado virava ilegível em card escuro),
  `.admin-btn-outline` (borda/hover escuros translúcidos, quase invisíveis
  em card escuro), `.admin-group__avatar` (bege+marrom -> roxo+laranja),
  `.admin-event-card__date` (usava `var(--a-ink)` como INÍCIO do gradiente
  de fundo -- com ink virando claro, o gradiente invertia e o texto branco
  sumia; trocado pra cor fixa), `.admin-toast` (mesma armadilha -- usava
  `var(--a-ink)` como fundo, mas o toast é flutuante e precisa ficar escuro
  SEMPRE, independente do tema do card).
- **`.help-text` e `.field label` são classes GLOBAIS do site** (definidas
  fora do bloco admin, pensadas pro card-pergaminho claro das telas de
  auth/compra). Usadas cruas dentro de cards do admin (texto de "lista
  vazia" em pedidos/cadastros/eventos, rótulos do form de material/lote)
  herdavam a cor escura global -- ficava ilegível no card agora escuro.
  Corrigido com override só dentro do escopo `.admin-shell .help-text` /
  `.admin-shell .field label`, sem tocar a regra global nem nenhum
  template (nenhum desses 3 templates precisou de edição pra esse fix).
- **Nav da sidebar**: trocado o ponto colorido por ícone (pumpkin/eye/
  witch-hat/coffin/treat.png, já entregues em `static/assets/`) + pílula de
  contagem, como no mockup. **Contagem por item é uma simplificação
  deliberada**: só a seção ATIVA mostra número ao vivo (reaproveitando a
  lista que a própria página já recebe -- `pedidos`, `pendentes`,
  `usuarios`, `materiais`, `eventos` -- via `is defined` no Jinja); os
  outros 4 itens ficam sem pílula na página atual, porque buscar as 5
  contagens em TODA página do admin exigiria mexer nas rotas de
  `app/admin.py` (uma query/conexão nova por página), e o pedido foi
  explícito em não tocar nesse arquivo. Documentado aqui pra quem quiser
  fechar essa lacuna depois (ex.: um `@admin_bp.context_processor` com uma
  única query agregando as 5 contagens).
- **Barra de progresso "N de N ingressos vendidos" no rodapé da sidebar
  (do mockup) foi PULADA** -- mesma razão do item acima (só fica disponível
  hoje na página de Pedidos via `_totais_pedidos`, mostrar em toda página
  exigiria a mesma plumbing bloqueada por não tocar `admin.py`).
- **Validado sem Postgres** (mesma limitação das sessões anteriores): os 6
  templates de admin renderizados via `render_template()` com dado fake
  (Flask test_request_context + `auth.current_user` mockado), `python -m
  py_compile` nos `.py` (nenhum tocado, checado mesmo assim), parse Jinja
  dos 7 templates, contagem de chaves `{`/`}` do CSS batendo (449/449), e
  grep confirmando que `admin-ajax-form`, `data-admin-total`, `data-row-id`,
  `data-material-id`, `admin:sucesso` e as 3 funções JS
  (`confirmarAcao`/`confirmarAcaoLote`/`enviarAcaoAdmin`) continuam
  presentes nos mesmos arquivos de antes do passe.
