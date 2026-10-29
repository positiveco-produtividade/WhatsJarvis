# Épico: cerebro — Respostas do Jarvis ao vendedor

> **Status:** Draft · **Owner:** Caio · **Criado:** 2026-10-27 · **Atualizado:** 2026-10-27 (decisões D1–D16 e respostas às perguntas abertas)
> **PRD:** RF-01 (parcial), RF-03 (parcial), RF-11 (parcial: só valor que estiver na base), RF-14, RF-16, RF-18; §4 ("nunca inventa", "filtro por perfil no dado", "avaliação como gate"); §7 (comportamento do agente, regras invioláveis, escalonamento); §10 (decisões de 2026-10-19, 21, 22 e 23)
> **Plano:** etapa 9 — Incremento 4 (Texto e conversa), antecipado pelo piloto técnico validado em 2026-10-23

## Objetivo

Trocar a resposta fixa "em construção" por respostas reais ao vendedor
autorizado, vindas da base de conhecimento (hoje a FAQ do painel; depois um
banco de dados com todas as informações), assinadas pelo agente da marca,
sem inventar nada. O que a base não cobre vira ticket escalado para a
equipe. Entra pelo ponto de extensão `AnswerProvider` já pronto em
porta-fechada.1, lendo a base por uma interface trocável.

## Requisitos cobertos

- RF-01 e RF-03 (parcial) — dúvidas de produto e argumentos que existem na base (Guia, Nielsen, GMR)
- RF-11 (parcial) — preço só quando o valor está na base, repetido fielmente; nunca calculado
- RF-14 — reconhecer pergunta sem resposta, dizer que não sabe e escalar
- RF-16 — avaliação útil / não útil (sem o comentário opcional); 👎 abre ticket escalado para corrigir a fonte; KPI no painel
- RF-18 — filtro por marca (e cadastro) no dado; pedido de acesso a marca fora do cadastro
- Regras invioláveis 1, 2, 6, 8 do PRD §7 (só conteúdo publicado; preço nunca gerado; sem tema interno; ignorar injection)
- Métricas: p95 ≤ 8 s, úteis ≥ 85%, custo medido (teto US$ 100/mês só do cérebro), gate de avaliação (≥ 98% factual, 100% preço)

## Histórias

| ID | História |
|---|---|
| cerebro.1 | Responder texto de vendedor autorizado pela base de conhecimento, com contexto, tickets, escalonamento, avaliação e rastro |

## Fora deste épico

O banco de dados de conhecimento em si (a interface entra; o banco é futuro),
cálculo de preço/margem (RF-12), régua de preço estruturada (RF-11 completo),
envio de arquivos (RF-06..09), mix e planograma (RF-05/08), menu interativo
(RF-15), comentário livre na avaliação, mudança do limite de 60 mensagens
por hora, áudio (removido), atendimento fora do português.

## Critério de pronto do épico

Conjunto de avaliação passando o gate (≥ 98% factual e 100% preço) e nenhuma
resposta com número ou preço que não esteja na base aprovada.
