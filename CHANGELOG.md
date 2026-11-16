# Histórico de mudanças

Resumo das fases do projeto, do esqueleto à revisão final. O histórico completo
está nos commits.

## 2026-11-16: banco normalizado em 3FN

- `evento` virou `festa` (nome, data, endereço, capacidade) + `lote` (preço, promoção,
  vendas). Pessoas saíram do pedido para `participante` + `ingresso`; os códigos de
  verificação foram para a tabela `codigo_verificacao`; `expirado` virou status.
- Migração sem perda de dados (`app/migracao_3fn.sql`), testada contra um PostgreSQL de verdade.
- Função **encerrar/reabrir vendas** pelo painel (tela Evento).
- Testes com banco real (migração e fluxo completo do site): 338 verificações no total.
- README, documentação de arquitetura e testes reorganizados.

## 2026-10-09: revisão completa e desempenho

- **Dinheiro e vagas:** conciliação do Pix mais robusta (paginação, cache curto,
  timeout, pausa após falha); o pedido não expira se o Mercado Pago não
  respondeu; teto de ingressos por conta em todos os lotes; confirmação em lote
  que não derruba o lote inteiro por causa de um pedido.
- **Segurança:** limite de requisições por IP, tentativas de código reservadas de
  forma atômica, proteção contra open redirect, sessões revogadas ao trocar a
  senha, cabeçalhos de segurança e dependências atualizadas.
- **Painel e front:** "selecionar todos" só nos itens visíveis, confirmações
  antes de ações destrutivas, atualização automática da lista de pedidos,
  validações dos campos do evento e contagem regressiva do pagamento por relógio.
- **Desempenho:** pool de conexões, cache longo dos arquivos estáticos, fontes
  hospedadas no próprio site e menos consultas por página.

## 2026-10-08

- Aviso por e-mail aos admins de cadastro aguardando aprovação e de pedido novo.

## 2026-10-05 e 2026-10-06

- Promoção casadinha (2 ingressos por valor fechado) com liga/desliga no admin.
- E-mails no tema VHS; planilha da portaria em `.xlsx`.
- Teto de ingressos por conta e dois ingressos exibidos nas telas.
- Segurança: limite de tentativas nos códigos, CSRF por `Origin`, conta Google.
- Expiração de pedido abandonado, trava de vagas e edição de despesas de material.
- Deploy com gunicorn (2 processos × 4 threads).

## 2026-09-25 a 2026-10-01

- Novo visual "Back to Halloween" (tema VHS anos 80/90/2000) no site e no admin.
- Página de convite refeita, com a TV, as estantes de fitas e o holofote.
- Pix direto na chave, sem o Checkout do Mercado Pago.

## 2026-09-21 e 2026-09-22

- Esqueleto da aplicação (Flask, PostgreSQL, `render.yaml`).
- Autenticação, compra de ingresso, painel admin e primeira integração com o
  Mercado Pago.
- Aprovação manual de cadastros e capacidade do evento.
