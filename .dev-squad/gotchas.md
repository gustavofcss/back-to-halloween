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
