# Gotchas -- Horror Night Party

Armadilhas de runtime já encontradas neste projeto, pra não redescobrir na
marra numa sessão futura.

## "Selecionar todos" trava depois da primeira remoção de linha (admin, seleção múltipla)

**Sintoma**: nas telas de Pedidos, Cadastros e Hierarquia do admin, depois
de apagar/confirmar/recusar/promover/rebaixar UM item (linha some do DOM via
`admin:sucesso`), a caixinha "Selecionar todos" nunca mais fica marcada
mesmo com 100% das linhas restantes selecionadas manualmente.

**Causa raiz**: o contador comparava `marcados === itens.length`, e `itens`
era um `NodeList` capturado com `document.querySelectorAll(...)` UMA VEZ no
carregamento da página (estático, não ao vivo). Remover uma linha do DOM não
reduz esse `.length` guardado.

**Fix**: recalcular o total dentro de `atualizar()` com
`document.querySelectorAll(seletor).length` a cada chamada, em vez de usar
o `itens.length` cacheado. Aplicado em `pedidos.html`, `cadastros.html`,
`hierarquia.html` (2026-09-23).

**Como recorre**: qualquer tela nova com seleção múltipla + remoção de linha
via evento customizado (`admin:sucesso` ou parecido) tem o mesmo risco se
copiar o padrão `var itens = document.querySelectorAll(...)` fora da função
que recalcula o contador.

## `int()` em Python trunca float silenciosamente

`_ids_do_body` em `app/admin.py` faz `[int(i) for i in ids]` pra validar o
corpo das rotas `*-lote`. `int(2.5)` não lança erro, vira `2` -- não é bug
de verdade aqui (a UI só manda inteiros via `parseInt`), mas é a razão pela
qual um corpo tipo `{"ids": [1, 2.5, 3]}` passa na validação de "lista de
ids inválida" (400) e só falha adiante, ao tentar tocar o banco. Se um dia
a validação de API precisar ser mais rígida (ex.: expor a rotas fora do
admin AJAX-only), trocar por uma checagem explícita de `isinstance(i, int)`.

## Rotas AJAX do admin: convenção completa

Ver `.dev-squad/memory.md`, seção "Convenção AJAX do admin" -- envelope
JSON, status codes, `after_request` que reescreve redirect-pro-login em 401
pra requests AJAX. Toda rota de mutação nova em `/admin/*` deve seguir esse
mesmo padrão.

## `\d` do Python sem `re.ASCII` casa dígito Unicode, não só 0-9

**Sintoma**: um validador de "só números" (`re.match(r"^\d+$", valor)`)
aceita RG/WhatsApp/qualquer campo numérico digitado com dígito fullwidth
(`１２３`, comum em teclado CJK) ou arábico-índico (`٩`) -- o valor "válido"
vai pro banco com caractere não-ASCII escondido no meio.

**Causa raiz**: em Python 3, `\d` em uma string pattern (não bytes) casa
qualquer caractere da categoria Unicode `Nd` por padrão -- não é limitado a
`0-9` a menos que o regex seja compilado com a flag `re.ASCII` (ou `(?a)`
inline).

**Fix**: `re.compile(padrao, re.ASCII)` em todo regex que pretende validar
"só dígito ASCII". Aplicado em `app/pedidos.py`
(`_RG_CARACTERES_RE`/`_RG_RE`/`_WHATSAPP_CARACTERES_RE`/`_WHATSAPP_RE`,
achado em QA do commit 02b0ab5, 2026-09-23).

**Como recorre**: qualquer novo campo "só números" (CPF, código de
convite, etc.) que use `\d` em vez de `[0-9]` ou `re.ASCII` tem o mesmo
risco. Testar sempre com um dígito fullwidth ou arábico-índico antes de
confiar num validador desses.

## Reskin claro->escuro: classe global vazando cor errada pra dentro de um card temático

**Sintoma**: depois de trocar os tokens `--a-cream*`/`--a-ink*` do painel
admin de "card claro + texto escuro" pra "card escuro + texto claro"
(2026-09-23), o texto de "Nenhum pedido ainda." e os rótulos do form de
material/lote continuavam escuros -- quase invisíveis no card agora escuro,
mesmo com TODAS as regras `.admin-*` já corrigidas.

**Causa raiz**: `.help-text` e `.field label` (usados dentro dos cards do
admin) são classes GLOBAIS do site, definidas fora do bloco admin do CSS,
com cor fixa pensada pro card-pergaminho claro das telas de auth/compra
(`var(--ink-muted)`/`var(--label-muted)`, tons de marrom escuro). Trocar só
os tokens `--a-*` não afeta essas classes -- elas nunca usaram token do
admin, usam token GLOBAL do site.

**Fix**: override escopado, sem tocar a regra global nem nenhum template:
```css
.admin-shell .help-text { color: var(--a-ink-muted); }
.admin-shell .field label { color: var(--a-accent); }
```

**Como recorre**: qualquer reskin de UMA área de um site que reusa classes
globais (não prefixadas com o namespace da área, tipo `.admin-*`) precisa
auditar TODA classe global usada dentro dela, não só as regras já
prefixadas/escopadas -- um grep por `class="` nos templates da área listando
classes SEM o prefixo do namespace acha essas antes de virarem bug visual.

## Comparar `>=`/`<=` num valor formatado como string em Jinja quebra a página inteira (500)

**Sintoma**: `/admin/material` estourava `TypeError: '>=' not supported
between instances of 'str' and 'int'` em TODO carregamento da página (não é
edge case -- é o caminho normal), achado no QA runtime do passe 3/3 do
redesign (`df76d9c`, 2026-09-23). `jinja2.Environment().parse()` não pega
isso -- é erro de tipo em RUNTIME, só aparece renderizando de verdade com
`render_template()`.

**Causa raiz**: `admin/material.html` (novo, do handoff) tinha
`{{ 'var(--ok)' if saldo >= 0 else 'var(--bad)' }}`, mas `saldo` chega do
backend como STRING já formatada -- `_totais_material()` em `app/admin.py`
faz `f"{saldo:.2f}"` (pensado só pra exibição, não pra comparação). Comparar
`str >= int` é `TypeError` em Python 3 (não faz coerção silenciosa como
JS faria).

**Fix**: template-only (`app/admin.py` é frontend-off-limits por regra do
handoff) -- `saldo|float >= 0` em vez de `saldo >= 0`. Qualquer outro valor
formatado como string vindo do backend (`total_arrecadado`, `total_gasto`,
`valor_total` etc.) que um dia precise de comparação numérica no template
tem o mesmo risco -- sempre `|float` ou `|int` antes de comparar.

**Como recorre**: sempre que um redesign de template usa uma variável só
pra EXIBIÇÃO ("R$ {{ x }}") e o mockup pede também uma DECISÃO condicional
sobre ela (cor verde/vermelho, `{% if %}`), checar no código Python de onde
a variável vem -- se foi formatada com `f"{...:.2f}"` ou similar, ela virou
string e qualquer comparação numérica direta no Jinja quebra a página
inteira, sempre, não só em alguns dados. `jinja2.Environment().parse()`
(checagem de sintaxe) NUNCA pega isso -- só renderizar de verdade com dados
reais (ou um harness de render com contexto fake fiel ao que a rota passa)
encontra.
