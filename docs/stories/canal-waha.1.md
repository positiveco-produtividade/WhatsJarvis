# História canal-waha.1 — Vendedores pelo WAHA, Meta como segunda opção

> **Épico:** [canal-waha](../epics/canal-waha.md) · **Status:** Draft (requisitos aprovados por Caio em 2026-11-13; design em andamento) · **Owner:** Caio · **Criado:** 2026-11-13

Fonte de verdade: `docs/tasks/canal-waha.1/spec/spec.md` e `requirements.json`.
Em caso de divergência, vale o spec.

## Como / quero / para

**Como** responsável pelo Jarvis,
**quero** que os vendedores conversem com ele por um número operado pelo WAHA,
mantendo a Meta pronta para ser ligada por configuração,
**para** colocar o Jarvis no ar sem esperar as pendências da Meta, sem perder
nenhuma das garantias de acesso, privacidade e rastro já construídas.

## Escopo (resumo)

- Chave `CHANNEL_PROVIDER` (`waha` padrão, `meta` opcional).
- Webhook do WAHA com assinatura HMAC, filtros (grupos, mensagens próprias), idempotência e o mesmo pipeline de acesso.
- Opt-in iniciado pelo vendedor (link `wa.me`), aceite por texto.
- Sem avaliação 👍/👎 no WAHA; "qual marca?" por opção numerada.
- Envio com "visto" e "digitando", partes em ordem, status de entrega pelos `acks`.
- Alerta quando a sessão do WAHA cai ou o número sofre restrição.

## Critérios de aceite

WA-1 a WA-20 em [`spec.md`](../tasks/canal-waha.1/spec/spec.md) seção 3.
