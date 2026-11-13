# Épico: canal-waha — WhatsApp dos vendedores via WAHA

> **Status:** Draft · **Owner:** Caio · **Criado:** 2026-11-13
> **PRD:** §7 (regras WhatsApp) · **Substitui em parte:** ADR 2026-10-20 ("WAHA só para avisos internos"); ADR novo na Fase 2

## Objetivo

Colocar o Jarvis no ar com os vendedores usando o **WAHA** (já operado no droplet
para o AUTO-1) como canal principal, sem depender da conta, do número verificado,
do token permanente e do modelo aprovado da Meta. A **WhatsApp Cloud API da Meta
continua no código como segunda opção**, escolhida por configuração, para o futuro.

## Por que agora

O go-live pela Meta está parado em pendências externas (B-001 do `docs/STATE.md`):
número próprio, empresa verificada, forma de pagamento, token permanente e
aprovação do modelo de boas-vindas. O WAHA já roda no servidor e não exige nada disso.

## Riscos aceitos ao escolher o WAHA

- Canal **não oficial** (automação do WhatsApp Web): o número pode ser restrito ou banido; se cair, o Jarvis fica sem canal até religar ou trocar de número.
- Sem botões confiáveis: opt-in e "qual marca?" passam a ser por texto; a avaliação 👍/👎 sai do canal WAHA.
- O WAHA recomenda que o número **nunca inicie conversa**: o opt-in passa a começar pelo vendedor.

## Histórias

| ID | História |
|---|---|
| canal-waha.1 | Receber e responder vendedores pelo WAHA, com a Meta como segunda opção por configuração |

## Fora deste épico

Remover o código da Meta; envio em massa ou campanhas; grupos; baixar mídia; áudio.

## Critério de pronto do épico

Com `CHANNEL_PROVIDER=waha`, um vendedor cadastrado completa o opt-in, pergunta,
e recebe a resposta, tudo pelo WAHA; com `CHANNEL_PROVIDER=meta`, a suíte
atual passa sem alteração.
