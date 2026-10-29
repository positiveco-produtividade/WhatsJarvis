# Épico: porta-fechada — Acesso controlado ao Jarvis

> **Status:** Draft · **Owner:** Caio · **Criado:** 2026-10-20
> **PRD:** §3 (perfis e lista positiva), §4 (princípio "acesso antes de tudo"), §7 (segurança, LGPD, regras WhatsApp)
> **Plano:** etapa 5 — Incremento 1

## Objetivo

Garantir que só números cadastrados e com aceite registrado conversem com o
Jarvis, e que isso seja verificado **antes** de qualquer custo (transcrição,
busca, modelo de linguagem). É a fundação de segurança sobre a qual todas as
funções de conteúdo serão construídas.

## Requisitos cobertos

- RF-17 — bloquear número fora da lista antes de qualquer processamento pelo modelo
- RF-20 (parcial) — cadastro, revalidação e revogação de usuários (sem tela; via comando/CSV neste épico)
- Regras 1–7 da lista positiva (PRD §3)
- Segurança: validação da assinatura do webhook, limite de 60 msg/h, rastro por mensagem
- LGPD: aceite do aviso de privacidade registrado com data e hora

## Histórias

| ID | História |
|---|---|
| porta-fechada.1 | Receber mensagens da WhatsApp Cloud API e aplicar a lista positiva com opt-in |

## Fora deste épico

Respostas com conteúdo (produto, preço, argumentos), painel web, áudio.

## Critério de pronto do épico

Teste de intrusão sem falhas: zero conteúdo entregue a número fora da lista,
revogação efetiva em ≤ 1 minuto, mensagens sem assinatura válida descartadas.
