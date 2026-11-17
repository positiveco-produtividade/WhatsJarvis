# Épico: retorno-humano — A equipe responde os escalados

> **Status:** Draft · **Owner:** Caio · **Criado:** 2026-11-17
> **PRD:** §5 (escalonamento), §10 (2026-10-21, "escalonamento por grupo"; 2026-10-23, "grupo da equipe desligado")

## Objetivo

Fechar o ciclo dos casos que o Jarvis não responde. Hoje o vendedor ouve "anotei a
pergunta para a equipe", o ticket fica em Escalados e **concluir não envia nada ao
vendedor** (`tickets_admin.conclude`). Este épico faz a resposta humana chegar ao
vendedor **pelo número do Jarvis, na mesma conversa**, e transforma essas respostas
em sugestões de novos itens da FAQ.

## Por que agora

Achado do mapeamento de 06/11 (`docs/mapeo-general-2026-11-06.md`) e do `STATE.md`:
sem retorno humano, toda pergunta escalada é uma pergunta perdida, o que contradiz o
job-to-be-done do PRD ("responder o comprador na hora… sem depender de ninguém").

## Escolha do Caio (2026-11-17): A + C + D

- **A. Responder pelo painel:** caixa de resposta no Inbox e no ticket; o texto sai pelo número do Jarvis.
- **C. Grupo da equipe:** cada escalado vira aviso no grupo de WhatsApp, no estilo do AUTO-1, com a pergunta e um link que abre o Inbox nela. O grupo é **só aviso** (revisto pelo Caio no mesmo dia: responder por reply ou prefixo deixaria o grupo confuso).
- **D. Resposta vira sugestão de FAQ:** as respostas humanas aparecem em Relatórios como sugestões de itens; nada entra na FAQ sem aprovação.

## Histórias

| ID | História |
|---|---|
| retorno-humano.1 | Avisar escalados no grupo, responder pelo Inbox e sugerir itens de FAQ a partir das respostas |

## Fora deste épico

Responder pelo grupo; respostas automáticas geradas por IA para escalados; resposta com
mídia (foto, áudio, arquivo); mensagens ativas a quem não escreveu; papel novo de
"respondente" no painel.

## Critério de pronto do épico

Um vendedor faz uma pergunta que o Jarvis não sabe; a equipe recebe o aviso no grupo,
abre o link, responde pelo Inbox, e o vendedor recebe a resposta na mesma conversa do Jarvis; o
ticket fica concluído com autor; a dupla pergunta/resposta aparece em Relatórios como
sugestão de FAQ e, aprovada, vira item ativo.
