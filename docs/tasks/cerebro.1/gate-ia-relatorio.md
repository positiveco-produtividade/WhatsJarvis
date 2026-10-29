# CB-T35 — Gate com IA real e teste no número de teste

**Data:** 2026-10-29
**Executado por:** Caio (celular) e Claude (gate, conferência no banco e nos logs)
**Ambiente:** local (uvicorn :8010 via túnel do VS Code), banco `jarvis` local, número de teste da Meta, modelo `gemini-3.8-flash`, modo `grounded`, preços configurados 1,50 / 7,50 US$ por milhão de tokens.

## 1. Gate com IA real (`eval --mode ia`)

Comando: `.venv\Scripts\python -m app.services.brain.eval --mode ia` (a partir de `backend/`).

| Medida | Resultado | Meta |
|---|---|---|
| Factual | 100% | ≥ 98% |
| Preço | 20/20 (100%) | 100% |
| Follow-ups | 16/16 | — |
| Guia (162 casos) | 162/162 | — |
| Chamadas ao Gemini | 44 | — |
| Timeouts (caíram na busca por palavras) | 3 | — |
| Erros do Gemini | 0 | — |
| Custo da rodada | US$ 0,067 | — |

**APROVADO.** A primeira rodada deu 97,9%. As falhas foram corrigidas em CEREBRO-1.R04 (bf14159): o follow-up herdava a UF da pergunta anterior, o "obrigado" recebia a saudação e a pergunta genérica com empate entre marcas não pedia a marca. O modo `palavras` também ficou em 100%.

## 2. Teste no número de teste (celular)

Vendedor de teste ativo, cadastrado nas 5 marcas. Cérebro ligado (`BRAIN_ENABLED=true`).

| Mensagem | Resultado | Latência | Caminho |
|---|---|---|---|
| "Ola" / "Oi" | saudação fixa | 1,1–1,3 s | nenhum |
| "Qual o preço" | "Ainda não sei…" + ticket escalado | 1,4 s | palavras |
| "Possible não usa leite de vaca" | "Ainda não sei…" + ticket escalado | 6,3 s | ia (ver 3) |
| "Jingle tem glúten ??" | resposta da FAQ com assinatura "— Duda · Jungle" e botões | 6,2 s | ia, fidelidade `repaired` |
| 👍 Útil | avaliação `up` gravada, sem mensagem de volta | — | — |
| 👎 na mesma resposta, depois do 👍 | ignorado (vale a primeira avaliação) | — | — |
| "Obrigado" | "Por nada! Se precisar, é só mandar a pergunta." | 1,1 s | nenhum |
| "Jingle tem lactose" | "Ainda não sei…" + ticket escalado (a FAQ da Jungle não tem lactose) | 4,5 s | ia |
| "Quais sabores tem jungle" | lista de sabores com assinatura e botões | 4,8 s | ia |
| 👎 Não útil | agradecimento + ticket escalado `rating_down` / `pouco_urgente` | 1,2 s | — |

**Conferido:**
- Os botões com emoji ("👍 Útil" / "👎 Não útil") aparecem corretamente no WhatsApp (iPhone). Esta era a premissa não verificável pela documentação (`verificacao-externa.md`, item 3).
- Latência real dentro do CB-16 (p95 ≤ 8 s): o maior tempo foi 6,3 s, com a chamada ao Gemini em torno de 5 s.
- Toques nos botões não contam no limite de mensagens.
- `chat.brain_runs` grava caminho, itens, nota, tokens, custo, fidelidade e tempos por etapa.
- Custo por pergunta com IA: US$ 0,002–0,004.

## 3. Achados

1. **Sinônimos de leite.** "leite / leite de vaca" não é ligado a "lactose / lácteo / animal", então a busca escolheu um item errado e a IA, corretamente, não respondeu. Proposta: ajuste de sinônimos em `matching.py`, do mesmo tipo do R02. Aguarda o Caio.
2. **Lacuna da FAQ.** A Jungle não tem item sobre lactose. É conteúdo, não código: cadastrar pelo painel.
3. **Token temporário da Meta (24 h).** Ele venceu durante o teste (erro 190 no envio). O rastro ficou `send_failed` e nada foi perdido em silêncio. Produção precisa de token permanente (usuário do sistema).
4. **Número de teste recebe mensagens de desconhecidos.** São números BR alheios ao projeto; ficam como pedidos de acesso para apagar depois.

## 4. Decisões

- **Modo:** fica `grounded` (padrão). A fidelidade corrigiu a resposta quando necessário e não houve número ou preço inventado.
- **Limiares** 0,55 / 0,30 / 0,85 e janela de 3 trocas em 30 min mantidos; não houve sinal para recalibrar.
- **Go-live continua bloqueado** por número próprio com token permanente e os GRANTs de `jarvis_app` no droplet.
