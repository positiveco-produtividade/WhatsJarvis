# História cerebro.1 — Responder o vendedor a partir da base de conhecimento

> **Épico:** [cerebro](../epics/cerebro.md) · **Status:** Draft · **Owner:** Caio · **Criado:** 2026-10-27 · **Atualizado:** 2026-10-27 (decisões D1–D16 e respostas às perguntas abertas)

## Como / quero / para

**Como** vendedor autorizado,
**quero** mandar uma pergunta de texto no WhatsApp e receber na hora a resposta aprovada, só com o que existe na base de conhecimento,
**para** responder o comprador sem depender de ninguém; e, quando o Jarvis não souber, ser avisado de que a equipe foi acionada.

## O que muda para quem usa

- Texto de usuário ativo deixa de receber "em construção" e passa a receber a resposta da base, assinada pelo agente da marca (Duda, Léo, Nico, Rita, Tati ou Jarvis), com a fonte quando houver.
- A IA entende a pergunta e responde **só** com o que está na base (hoje a FAQ, com itens do Guia, Nielsen e GMR; depois um banco de dados). Nada é inventado.
- Sem resposta segura: "Ainda não sei responder isso. Anotei a pergunta para a equipe." e um ticket escalado aparece no painel (Escalados), sem alerta e sem e-mail.
- Preço: o Jarvis responde se o valor está na base, repetido igual; nunca calcula nem estima. Sem valor na base, escala para o Comercial.
- Pergunta sobre marca que o vendedor ainda não tem no cadastro: o Jarvis avisa que ainda não há acesso a essa marca e que a equipe foi acionada; abre ticket de pedido de acesso.
- Follow-ups curtos ("e o de limão?") funcionam: o Jarvis lembra as últimas 2 a 3 trocas do mesmo vendedor.
- Depois de cada resposta com conteúdo, botões 👍/👎. Os toques não contam no limite de 60 mensagens por hora. 👍 só registra. 👎 gera um agradecimento curto ("Obrigado, avisei a equipe para melhorar essa resposta.") e um ticket escalado com a pergunta e a resposta dada, para a equipe corrigir a fonte; sem pedir comentário. O painel passa a mostrar "Avaliados como úteis" em Métricas.
- Tickets de pergunta (`kind='question'`) alimentam Tickets, Inbox, Escalados, Métricas e Relatórios (hoje só mostram mídia).
- Teto de custo de IA do cérebro: US$ 100 por mês (alerta a 80%); ao atingir, o Jarvis continua respondendo só com a busca por palavras. O Assistente do painel tem limite próprio.

## Depende de

porta-fechada.1 (ponto `AnswerProvider`, outbox, política de resposta, `chat.tickets`, `send_reply_buttons`) e painel.1 (FAQ com 112 perguntas e itens Nielsen/GMR, `FaqReader`, Métricas, chave do Gemini em configuração). Piloto técnico validado em 2026-10-23 como referência de comportamento. Pendências de produção: LGPD/provedor do Gemini (D12) e licença Nielsen (D7).

## Critérios de aceite (alto nível)

1. Pergunta coberta pela base recebe a resposta aprovada, em ≤ 8 s (p95), sem fato inventado.
2. Pergunta fora da base vira ticket escalado, o vendedor é avisado e nenhum alerta/e-mail é gerado.
3. Preço só sai se estiver na base; nunca calculado.
4. Marca fora do cadastro gera pedido de acesso, sem revelar conteúdo.
5. Nenhum telefone ou nome do vendedor vai ao Gemini.
6. Tentativa de injection não muda regras nem perfil e gera alerta.
7. Gate de avaliação automatizado: ≥ 98% factual, 100% preço.
8. Edição da FAQ no painel vale em ≤ 5 minutos.
9. Avaliação 👍/👎 gravada, sem contar no limite de 60 msg/h; 👎 abre ticket escalado sem duplicar; KPI "Avaliados como úteis" visível no painel.
10. Custo mensal de IA do cérebro limitado a US$ 100 (alerta a 80%).

Detalhe: `docs/tasks/cerebro.1/spec/` (CB-1 a CB-29, decisões D1–D16).
