---
task_id: CEREBRO-1
status: draft
phase: 3-tasks
requirements_approved_by: Caio
requirements_approved_at: 2026-10-27
design_approved_by: Caio
design_approved_at: 2026-10-28
---

# Spec — Cérebro do Jarvis (CEREBRO-1)

> **Story:** [cerebro.1](../../../stories/cerebro.1.md) · **Epic:** [cerebro](../../../epics/cerebro.md)
> **Status:** Fase 1 (Requisitos, CB-1 a CB-29) **aprovada por Caio em 2026-10-27**. **Fase 2 aprovada por Caio em 2026-10-28** (design: seções 4, 5, 9 e 10, `research.json`, `complexity.json`), incluindo as 4 alterações no painel.1, memória de 3 trocas / 30 min e urgências. Fase 3 (tarefas: [`../task.md`](../task.md), 35 tarefas em 10 ondas) redigida em 2026-10-28, **aguardando aprovação** antes da Fase 4. Sem código.
> **Size:** L (21 pontos, ~140 h; 106,5 h em 35 tarefas + reserva; ver `complexity.json`). Critérios completos e testáveis: `requirements.json`.

## 1. Objetivo

Hoje todo texto de vendedor autorizado recebe "em construção" (`UnderConstructionProvider`). Esta história instala um `AnswerProvider` real que responde **somente com o que está na fonte de conhecimento**, assinado pelo agente da marca, e escala ao time o que a fonte não cobre. Hoje a fonte é a FAQ do painel (112 perguntas do Guia, mais os itens Nielsen e GMR já publicados); no futuro será um banco de dados com todas as informações, por isso o cérebro lê a fonte por uma **interface trocável** (D1). Segue o princípio "nunca inventa" (PRD §4, regra inviolável 2). O piloto técnico (`piloto/faq.js`, F1 de tokens, limiar 0,55, detecção de marca) é a referência de comportamento, validado pelo Caio em 2026-10-23 (5/5).

## 2. Escopo

**Dentro:** interface de fonte de conhecimento (FAQ como primeira implementação); IA que entende a pergunta, recupera os itens e responde fundamentada só neles, com busca por palavras como primeiro filtro e reserva; resposta com assinatura do agente e linha de fonte; "não sei" + ticket `kind='question'` escalado; ticket das respondidas; preço só quando o valor está na fonte; pedido de acesso quando a marca não está no cadastro; contexto das últimas 2 a 3 trocas do mesmo vendedor; avaliação 👍/👎 (botões, gravação, KPI no painel, e ticket escalado com agradecimento no 👎); tratamento de injection, cumprimento, ambiguidade e tema interno; privacidade, teto de custo, latência, rastro e gate de avaliação.

**Fora:** ver `out_of_scope` em `requirements.json` (banco de dados em si, cálculo de preço, arquivos, menu interativo, comentário na avaliação, mudança no limite de 60 msg/h, áudio, resposta pelo painel, outros idiomas).

## 3. Comportamento esperado

| Situação | Resultado |
|---|---|
| Pergunta casa com item da fonte | Resposta fundamentada só nos itens, assinatura, linha de fonte se houver; botões 👍/👎; ticket `answered_bot` |
| Sem casamento / `bot_responde=false` / marca desligada | "Ainda não sei responder isso. Anotei a pergunta para a equipe." + ticket escalado, sem alerta e sem e-mail (D3) |
| Marca fora do cadastro do vendedor | Informa que ainda não tem acesso às informações dessa marca e que a equipe foi acionada; ticket de pedido de acesso; nunca revela conteúdo; sem alerta e sem e-mail (D6) |
| Pergunta de preço | Responde o valor **se estiver na fonte**, repetido fielmente; nunca calculado nem estimado pela IA; sem valor na fonte = "não sei" + ticket Preço (D4) |
| Item Nielsen/GMR da FAQ | Usado como os demais, com "(Fonte: …)" e período (D7) |
| Follow-up ("e o de limão?") | Usa as últimas 2 a 3 trocas do mesmo vendedor para entender; a fonte segue filtrada pelo cadastro (D13) |
| Cumprimento / agradecimento | Saudação curta; sem ticket; sem botões |
| Marca ambígua | Pergunta a marca uma vez, em texto; sem chute |
| Injection | Recusa de uma frase + alerta; regras e perfil intactos |
| Toque em 👍 | Só grava a avaliação; não conta nas 60 msg/h; sem resposta e sem ticket |
| Toque em 👎 | Grava a avaliação; agradecimento fixo curto; ticket escalado (Escalados) com a pergunta e a resposta dada; sem comentário, sem alerta, sem e-mail; toques repetidos não duplicam (D14) |
| Gemini fora do ar ou teto de US$ 100/mês atingido | Cai para só busca por palavras (sem IA e sem contexto); segue respondendo |

Escalonamento por tema (PRD §7): Produto/regulatório → Qualidade, 1 dia útil; Argumento → Marca, 1 dia útil; Preço/política → Comercial, 4 h úteis; Campanha/mix → Trade, 1 dia útil; Acesso e cadastro → Administrador, 4 h úteis. Hoje o Caio recebe todos (PRD §10, 2026-10-19); o ticket guarda a classificação e a urgência.

---

## 4. Design

### 4.1 Arquitetura e fluxo de dados

Novo pacote `backend/app/services/brain/` (serviços; **não importa `app.panel`**, exigência de `tests/panel/unit/test_architecture.py`). Nenhuma mudança na tabela de decisão de acesso (PF 4.4.2). Módulos de apoio fora do pacote: `services/ratings.py` (avaliação 👍/👎) e `services/question_tickets.py` (inserção de ticket de pergunta, compartilhada por `brain/effects.py` e `ratings.py`; criado na Fase 3 para não editar `services/tickets.py` de porta-fechada.1).

```
Meta -> webhook (assinatura, 200) -> Pipeline.handle_message  [1 transação: rastro, identidade, acesso, 60/h]
   texto ALLOWED  -> reply_kind='pending' + AnswerRequest(trace_id, ctx)          (pipeline._text, inalterado)
   toque BUTTON (active) -> ramo IGNORED_TYPE: se payload RATE_* válido -> services/ratings (4.6)
AppProcessor._answer (tarefa de fundo, fora de transação)
   BrainProvider.answer(ctx)
      1 guardas sem custo   (saudação, injection, tema interno, cálculo)         brain/intents.py
      2 marca e acesso      (marca citada x cadastro; Flags.brand_enabled)       intents + flags
      3 recuperação         (KnowledgeSource.search -> candidatos pontuados)     brain/source.py, matching.py
      4 decisão             (direto | IA | não sei | pergunta de marca)          brain/provider.py
      5 IA (se couber)      (Gemini JSON: intent, item_ids, answer_text)         brain/llm.py, prompt.py, memory.py, meter.py
      6 verificação         (ids, números, R$, tamanho, tema)                    brain/fidelity.py
      7 montagem            (partes <= 700, sem corte, assinatura, fonte, botões)           brain/assembly.py
      -> Answer(kind, parts, effects)
   answer_and_enqueue: transação 2 curta = effects.apply (ticket, brain_run, alerta) + enqueue das partes + reply_kind
   despachante envia na ordem (outbox -> Meta); pausa e revogação checadas no despacho (inalterado)
```

Princípios: (1) nada que custa acontece antes de `allowed` (CB-22: o cérebro só é chamado pelo `AnswerRequest`); (2) o Gemini é opcional: todo caminho tem reserva por palavras (CB-14); (3) a fidelidade é verificada por código, não por confiança no modelo (CB-2).

### 4.2 Contrato com o ponto de extensão (`answering.py`)

Mudanças **aditivas** (testes existentes que fazem `Answer("kind", "texto")` continuam válidos):

- `AnswerContext` ganha `trace_id: int | None = None` (o pipeline passa `ctx.trace_id`; o cérebro precisa dele para memória e ticket). `AnswerRequest` já o tem (`answering.py:61-66`).
- `Answer` ganha `parts: tuple[str, ...] = ()` (se vazio, vale `text`; quantas partes forem precisas, nunca corta, D9) e `effects: AnswerEffects | None = None`.
- `AnswerEffects` (Protocol): `async def apply(self, conn, trace_id: int, user_id: int) -> AppliedEffects`, que roda **dentro da transação 2** e devolve `AppliedEffects(ticket_id: int | None, ticket_number: str | None, rate: bool)`. Cria ticket, `brain_runs` e alerta; só depois `answer_and_enqueue` enfileira as partes (a última com botões se `rate`, usando o `ticket_id`).
- `answer_and_enqueue` passa a devolver `tuple[int, ...]` (ids de saída em ordem; vazio se o provedor falhar). `main.AppProcessor._answer` despacha `await self._dispatcher(ids, ())`. A guarda `reply_kind='pending'` (`_SET_REPLY_KIND`) continua dando idempotência: reexecutar a mesma mensagem levanta `InvalidValue` e **desfaz a transação inteira** (ticket e saídas incluídos). `message_trace.ticket_number` passa a ser preenchido no mesmo `UPDATE`.
- Nenhuma transação aberta durante a chamada ao Gemini (mesma regra de hoje).

### 4.3 Interface da fonte de conhecimento (`brain/source.py`, `brain/faq_source.py`)

```python
@dataclass(frozen=True)
class KnowledgeItem:            # unidade recuperada; independente de FAQ
    ref: str                    # "faq:123" (fonte:id); estável para rastro
    version: int
    brand: str                  # slug de core/values.BRANDS ou "todas"
    category: str               # PRODUTO | PRECO | ARGUMENTO | MATERIAL | CAMPANHA
    title: str                  # pergunta cadastrada / rótulo do registro
    text: str                   # texto aprovado que pode ser dito (inclui "(Fonte: ...)" quando houver)
    origin: str                 # guia|manual|import|seed|nielsen|gmr (informativo)
    answerable: bool            # False = bot_responde=false ou sem texto

@dataclass(frozen=True)
class Scored:
    item: KnowledgeItem
    score: float                # 0..1 da busca por palavras

class KnowledgeSource(Protocol):
    name: str
    async def search(self, query: str, *, brands: Sequence[str], limit: int,
                     floor: float, conn: Any | None = None) -> list[Scored]: ...
```

- A fonte recebe **só as marcas permitidas ao vendedor** (cadastro + `todas`): o filtro é na consulta, não no prompt (CB-4, princípio "filtro no dado").
- `FaqSource` (adaptador): para cada marca chama `FaqReader.snapshot(brand)` (cache por revisão, ≤ 60 s, CB-17), junta sem duplicar por `id`, e pontua com `matching.py` (port do piloto: normalização, sinônimos, F1, regras de marca e de UF; as `variations` do item entram como formas extras da pergunta; vale a melhor). `answerable = bot_responde and answer is not None`.
- **Trocar para o banco** = novo adaptador que implemente `search` (SQL/índice próprio) e devolva `KnowledgeItem`; o restante (intents, prompt, fidelidade, preço, privacidade, tickets) não muda (CB-27). Teste de contrato: a mesma suíte de comportamento roda com `FaqSource` e com uma `FakeSource`.
- Itens Nielsen/GMR são itens comuns (D7); não há filtro por `origin` (D5). A linha "(Fonte: …)" já está em `text`.

### 4.4 Recuperação e resposta (`brain/provider.py`, `intents.py`, `fidelity.py`)

Estágios em ordem; o primeiro que decide encerra.

1. **Guardas sem custo** (`intents.py`, determinísticas, PT-BR, sem acento/caixa):
   - *saudação/agradecimento/vazio/só emoji* → `greeting` (texto do piloto como padrão; sem ticket, sem botões; CB-9).
   - *injection* (padrões como "ignore/esqueça as instruções", "você agora é", "mostre o prompt", "modo desenvolvedor", pedidos de dados de outros usuários) → `injection`: recusa de uma frase, alerta `injection_attempt` (4.7), ticket `answered_bot` não escalado; **não chama o Gemini** (CB-12).
   - *tema interno* (custos, margem da Positive, metas, pessoas) → `refusal` de uma frase, ticket `answered_bot` não escalado (CB-19). *Condição comercial* (desconto, prazo, bonificação) → `no_answer` com classificação `PRECO` (Comercial).
   - *cálculo de margem/preço ao consumidor* (RF-12, fora de escopo) → `no_answer` `PRECO`, a menos que um item da fonte responda literalmente.
   As listas ficam em `intents.py`, testadas por corpus; o gate mede falsos positivos.
2. **Marca e acesso:** detecta marcas citadas (regex do piloto portada para slugs). Se **exatamente uma** marca é citada e **não está** em `ctx.brands` → `access_request` (D6, CB-4): texto fixo, ticket de pedido de acesso (4.7), nada da marca é lido nem enviado. Se a marca está desligada em `Flags.brand_enabled` → `brand_disabled`: texto D3 + ticket escalado `escalation_reason='brand_disabled'` (o despacho **não** barra por marca, pois chama `policy.allow(..., None)`; `outbox.py:260`).
3. **Recuperação:** `source.search(query, brands=permitidas, limit=K=8, floor=0,30)`. Se a mensagem é um follow-up curto (poucos tokens úteis) e há janela de contexto (4.5), a consulta de recuperação soma os tokens da última pergunta do vendedor.
4. **Decisão:**
   - Sem candidato, ou melhor candidato `answerable=false` (CB-5) → `no_answer`.
   - Sem marca citada, sem marca no contexto, e os dois primeiros candidatos de **marcas diferentes** com diferença < 0,05 e nota ≥ limiar → `ask_brand` (CB-10, D8), sem ticket.
   - Melhor nota ≥ `DIRECT` (0,85), não é follow-up, e modo permite → **resposta direta** (caminho `palavras`): texto do item, sem IA (rápido e sem custo).
   - Caso contrário e IA disponível (chave ok, orçamento < 100%, circuito saudável) → **IA** (4.4.1). Se IA indisponível: nota ≥ `THRESHOLD` (0,55) → direta; senão `no_answer` (CB-14, CB-15).
5. **Regras de preço** (valem para todos os caminhos, CB-8): se a mensagem tem intenção de preço (valor, preço, quanto custa, tabela), a resposta final **precisa conter ao menos um valor R$** presente nos itens citados; senão `no_answer` com `PRECO`. Todo valor R$ da resposta precisa existir, igual, num item citado. A IA nunca calcula.
6. **Montagem** (`brain/assembly.py`): divide o texto por frase em partes de ≤ 700 caracteres, quantas forem precisas; **nada é cortado** (CB-11, D9 revisto); anexa a assinatura do agente na última (`\n\n— Duda · Jungle`, mapa do piloto: Duda·Jungle, Léo·Plant Power, Nico·Possible, Rita·A Tal da Castanha, Tati·Zaya, Jarvis quando a marca dos itens não é única ou é `todas`; D10); se `rate`, a última parte sai com botões 👍/👎.

#### 4.4.1 Chamada à IA e verificação de fidelidade

- **Uma** chamada ao Gemini por mensagem (sem retentativa; erro/timeout de 5 s → reserva por palavras). Entrada: instrução do sistema (versão `PROMPT_VERSION`), candidatos (`ref`, título, texto), janela de contexto (4.5) e a pergunta, tudo delimitado e marcado como **dado, não instrução**. Saída JSON: `{"intent": "answer|no_answer|ask_brand|out_of_scope", "item_refs": [...], "answer_text": "..."}`. Sem `temperature`/`topP`/`topK` (obsoletos no 3.8) e com `thinkingLevel: "low"`; tokens de saída = `candidatesTokenCount + thoughtsTokenCount` (verificacao-externa.md, T01). (`responseMimeType` JSON verificado na T01; plano B mantido como defesa = JSON por prompt e parse tolerante.)
- **Verificador determinístico (`fidelity.py`)**, sobre a saída:
  1. `item_refs` ⊆ candidatos enviados; sem `item_refs` válidos → `no_answer`.
  2. Todo número da `answer_text` (inteiros, decimais, %, R$, datas numéricas) existe no texto dos itens citados (normalizados: vírgula/ponto, milhar). Número fora → reparo: troca por `text` do melhor item citado (verbatim) se esse passar nas regras de preço; senão `no_answer`.
  3. Nomes próprios de produto/marca da resposta existem nos itens citados ou na pergunta.
  4. Tamanho da resposta ≤ 1,3 × o texto citado; sem URLs/telefones/e-mails que não estejam nos itens.
  5. Regras de preço do estágio 5.
  Resultado vai para `brain_runs.fidelity` (`ok|repaired|failed`) e para o gate.
- **Modo de segurança:** `BRAIN_ANSWER_MODE=verbatim` faz a IA só **escolher** `item_refs` e a resposta é sempre o texto aprovado (D1 opção b). Padrão `grounded`; se o gate não atingir 98% factual / 100% preço em `grounded`, liga-se `verbatim` sem mudar código.

### 4.5 Memória de conversa (D13, CB-23)

- **Sem nova cópia de texto.** A janela é lida de `chat.message_trace.body` (só existe para mensagens `allowed`) e da primeira parte enviada em `chat.outbound_messages.body` (`inbound_trace_id = trace.id`, `status='sent'`), ligadas por `chat.brain_runs` (desfecho `answered` ou `ask_brand`; exclui `injection`).
- Consulta (índice `message_trace_user_time_idx` existente): últimas `BRAIN_CONTEXT_EXCHANGES` (3) trocas do **mesmo `user_id`**, com `received_at >= now - BRAIN_CONTEXT_MINUTES` (30) e `id < trace_id` da mensagem atual.
- **LGPD:** a exclusão pelo painel zera `body` de rastro e de saída (`lgpd.py:218-237`), então a janela de quem foi excluído fica vazia sozinha. Só entra texto de quem está `active`; nunca de outro vendedor.
- **Segurança:** o filtro de fonte usa sempre o cadastro **atual**; o contexto só ajuda a entender a pergunta, nunca amplia acesso. O contexto é enviado como dado delimitado; trocas sinalizadas como `injection` não entram.
- Sem IA (falha ou orçamento esgotado): follow-up é tratado sem contexto (CB-23, CB-14).
- Tamanho (3 trocas) e janela (30 min) aprovados pelo Caio em 2026-10-28; são configuração, calibráveis pelo gate.

### 4.6 Avaliação 👍/👎 (D14, CB-24, CB-25, CB-29)

- **Botões:** a última parte de resposta de conteúdo (resultado `answered`) sai como `brain_answer_rate` com `reply_buttons` = `[{"id":"RATE_UP:<ticket_id>","title":"👍 Útil"},{"id":"RATE_DOWN:<ticket_id>","title":"👎 Não útil"}]`. Mesmo padrão do aviso de privacidade (`OPTIN_ACCEPT:<id>`, `values.py:159-179`); `outbox._send_once` envia por `Channel.send_reply_buttons` (`channel.py:54-56`). Helpers `rate_up_payload`, `rate_down_payload`, `parse_rating_payload` em `core/values.py`. Respostas sem conteúdo (não sei, saudação, recusa, pergunta de marca, pedido de acesso, agradecimento) saem como `brain_notice`, **sem botões**.
- **Entrada:** hoje `access.decide` devolve `IGNORED_TYPE` para botão de usuário `active` e `Pipeline._on_active` o trata **antes** do limite de 60/h (`pipeline.py:420-424`). O novo comportamento é um ramo **dentro** desse bloco: se `msg.kind is BUTTON` e `parse_rating_payload(payload)` é válido → `services/ratings.record_rating(ctx...)`; senão segue como `IGNORED_TYPE`. Consequência: o toque **não conta** no limite (`counted_in_limit=false`, `decision_reason='rating_up|rating_down'`), nunca é barrado por ele e não chama o cérebro nem o Gemini. Usuário `pending_optin`, `revoked` etc. já são tratados antes (silêncio), então "usuário não ativo é ignorado" vale sem código novo.
- **Gravação (mesma transação do rastro do toque):** valida que o ticket existe e `ticket.user_id = user.id` (senão ignora: botão de outro/adulterado); `INSERT INTO chat.answer_ratings ... ON CONFLICT (ticket_id) DO NOTHING RETURNING id`. **Primeira avaliação vale**: repetição ou botão oposto depois não insere nada, não cria ticket nem agradecimento.
- **👍:** só grava.
- **👎:** se o insert aconteceu, cria o **ticket do 👎** e enfileira o agradecimento: `brain_notice` com `TEXT_BRAIN_RATING_THANKS` ("Obrigado, avisei a equipe para melhorar essa resposta."), entregue pelo despachante via `MessageOutcome.outbound_ids` (como o `media_ack`). Pausa do Jarvis cala o agradecimento (não é isento), mas o ticket é criado. Sem alerta, sem e-mail (a entrega `group` segue a chave, como todo ticket).
- **KPI (CB-26):** `useful% = up / (up + down)` por período e marca (join com `ops.v_tickets` para a marca); cobertura = avaliadas / respostas com botões (`brain_runs.outcome='answered'`). Em `metrics_read.py`/`api/metrics.py`; sem avaliações continua "sem avaliações · meta ≥ 85%".

### 4.7 Chamados (tickets) e alertas

Sem classificação nem `kind` novos (decisão que resolve a pergunta aberta de D6/CB-4; justificativas em 5). Todos usam `kind='question'`, `ticket_number` da `ops.ticket_seq` e uma entrega `group` (modelo `create_media_ticket`, `tickets.py:51-69`), inseridos por `services/question_tickets.py`.

| Desfecho | `status` | `escalated` / urgência | `classification` | `escalation_reason` | `message` / `summary` |
|---|---|---|---|---|---|
| `answered` | `answered_bot` | não | categoria do item citado | — | pergunta / —; `confidence`=nota; `meta`={refs, versões} |
| `no_answer` | `awaiting` | sim / `urgente` se `PRECO`, senão `pouco_urgente` | categoria do melhor candidato, por palavras-chave, ou `NAO_IDENTIFICADO` | `no_match`, `not_answerable`, `no_price_in_source`, `commercial_condition`, `calc_not_supported` | pergunta / — |
| `brand_disabled` | `awaiting` | sim / `pouco_urgente` | idem | `brand_disabled` | pergunta / — |
| `access_request` | `awaiting` | sim / `urgente` (Administrador, 4 h úteis) | `NAO_IDENTIFICADO` | `brand_access_request` | pergunta / "Pedido de acesso à marca <Marca>"; `brand`=marca pedida |
| `injection`, `refusal` | `answered_bot` | não | `NAO_IDENTIFICADO` | `injection` / `out_of_scope` | pergunta / — |
| `greeting`, `ask_brand` | **sem ticket** | — | — | — | só `brain_runs` |
| 👎 | `awaiting` | sim / `pouco_urgente` | **herdada** do ticket avaliado | `rating_down` | pergunta original / **resposta dada** (para a equipe corrigir a fonte); `meta`={rated_ticket_number, refs}; `brand` herdada |

- **LGPD:** pergunta em `message`, resposta em `summary` (a exclusão zera os dois, `lgpd.py:238-243`); nada em `meta` que identifique a pessoa.
- **Dedup do pedido de acesso:** se já existe ticket `awaiting` com `escalation_reason='brand_access_request'` do mesmo vendedor e marca, não cria outro (responde o mesmo texto). Dedup do 👎: pelo `UNIQUE(ticket_id)` de `answer_ratings`.
- **Escalados:** sem coluna nova. Pedido de acesso cai em "Sem classificação" (comportamento de `escalados._column_key`; **aceito pelo Caio em 2026-10-28**); o 👎 aparece na coluna da classificação original. O texto legível do pedido de acesso está em `summary`.
- **Alertas** (`raise_alert`, `email=True`; só problemas de sistema/segurança): `injection_attempt` (`warn`, `dedup_key=injection:<user_id>`, janela 1 h, payload só ids/contagens); `brain_budget` (80% do mês, `dedup_key=brain_budget_80:<AAAA-MM>`, `dedup_window=None`); `brain_api_failure` (Gemini com falhas repetidas, `dedup_key` global, janela 1 h). O `api_failure` do monitor conta falhas de **envio** na Meta, por isso o do Gemini é um tipo próprio (rótulos novos em `panel/api/alerts.py:60-73`). Escalados e pedidos de acesso **nunca** viram alerta.

### 4.8 Custo (D11, CB-15)

- `brain/meter.py`: cada chamada ao Gemini devolve `usageMetadata` (`promptTokenCount`, `candidatesTokenCount`, padrão visto em `panel/assistant/gemini.py:100-102`); custo = tokens × preço de `BRAIN_PRICE_IN/OUT_PER_MTOK_USD` (configuração obrigatória para ligar a IA; **preços não estão no repositório e não são inventados aqui**; a fonte e a data do preço ficam registradas na T01). Gravado em `chat.brain_runs.cost_usd` na transação 2.
- **Gasto do mês** = `SUM(cost_usd)` de `brain_runs` com `created_at` no mês de `America/Sao_Paulo` (mesmo fuso das Métricas); cache em memória de 30 s por processo.
- **Antes de chamar o Gemini:** gasto ≥ US$ 100 (`BRAIN_MONTHLY_BUDGET_USD`) → pula a IA (reserva por palavras) até virar o mês. Gasto ≥ 80% → `raise_alert('brain_budget', 'warn', ...)` uma vez no mês.
- **Separado do Assistente do painel:** o Assistente tem limite e tabela próprios (`assistant_daily_limit`, `ops.assistant_questions`); o cérebro soma só `brain_runs`. Concorrência pode estourar o teto por poucas chamadas: aceito (a estimativa já é aproximada).
- Cada mensagem grava tokens e custo estimado desde o dia 1, também com a IA desligada (zero).

### 4.9 Privacidade (D12, CB-13)

- `brain/llm.py` é o **único** módulo que fala com o Gemini; recebe um `LlmRequest(system, candidates, context, question)` montado sem `AnswerContext`: nunca recebe `user_id`, telefone, BSUID, nome, empresa ou gestor (o teste intercepta o HTTP e varre o corpo).
- `brain/redaction.py` mascara na pergunta e no histórico telefones, e-mails e CPF/CNPJ por padrão regular antes do envio ("sinais óbvios"); nomes próprios de terceiros não são removidos (limite aceito, D12).
- Cliente replica o padrão do painel: host em allow-list (`generativelanguage.googleapis.com`), sem redirecionamento, chave no cabeçalho `x-goog-api-key`, **nunca** loga prompt, resposta nem corpo de erro (só status e classe da exceção). Timeout 5 s.
- Os itens da fonte enviados já foram filtrados por marca do cadastro (4.3).
- Contrato LGPD com o fornecedor (sem treino) e dados fora do Brasil: **pendência do dono** (D12), fora do código.

### 4.10 Rastro e observabilidade (CB-20)

- Uma linha em `chat.brain_runs` por mensagem tratada (ver 4.11): desfecho, caminho (`palavras|ia|nenhum`), refs/versões, melhor nota, marca, tokens, custo, tamanho do contexto, resultado da fidelidade, flags (`injection`, `price_intent`, `calc_intent`, `follow_up`), versões de prompt e modelo, tempos por etapa (busca, IA, verificação). Sem texto de pergunta nem de resposta.
- Latência ponta a ponta continua em `message_trace.latency_ms` (gravada pelo despacho); respostas em várias partes registram a latência da última (aceito, afeta p95 só marginalmente).
- Logs estruturados (`brain_decision`, `brain_llm_call`, `brain_budget`) com ids e contagens; nunca telefone nem texto (`core/log_redaction.py` já existe).

### 4.11 Modelo de dados (rascunho de migrations, PostgreSQL 16)

Bloco **201–209** reservado ao cérebro (PF 001–099, painel 101–107). Imutáveis depois de aplicadas.

```sql
-- 201_brain_outbound.sql
-- remove o CHECK de kind atual achando-o pelo catálogo (não presume o nome padrão do PG):
DO $$ DECLARE c text; BEGIN
  FOR c IN SELECT conname FROM pg_constraint
           WHERE conrelid = 'chat.outbound_messages'::regclass AND contype = 'c'
             AND pg_get_constraintdef(oid) LIKE '%welcome_template%'
  LOOP EXECUTE format('ALTER TABLE chat.outbound_messages DROP CONSTRAINT %I', c); END LOOP;
END $$;
ALTER TABLE chat.outbound_messages DROP CONSTRAINT IF EXISTS outbound_messages_kind_check;
ALTER TABLE chat.outbound_messages ADD CONSTRAINT outbound_messages_kind_check CHECK (kind IN (
  'welcome_template','optin_reminder','optin_confirmation','optin_declined','under_construction',
  'rate_limit_notice','media_ack','opt_out_confirmation',
  'brain_answer','brain_answer_rate','brain_notice'));
ALTER TABLE chat.outbound_messages ADD COLUMN IF NOT EXISTS reply_buttons jsonb;   -- [{"id":..., "title":...}]

-- 202_brain_runs_ratings.sql
CREATE TABLE IF NOT EXISTS chat.brain_runs (
  id             bigserial PRIMARY KEY,
  trace_id       bigint NOT NULL UNIQUE REFERENCES chat.message_trace(id) ON DELETE CASCADE,
  ticket_id      bigint REFERENCES chat.tickets(id) ON DELETE SET NULL,
  outcome        text NOT NULL CHECK (outcome IN ('answered','no_answer','brand_disabled',
                    'access_request','injection','refusal','greeting','ask_brand')),
  path           text NOT NULL CHECK (path IN ('palavras','ia','nenhum')),
  brand          text,
  item_refs      text[] NOT NULL DEFAULT '{}',          -- 'faq:123@v4'
  best_score     numeric(4,3),
  llm_calls      smallint NOT NULL DEFAULT 0,
  tokens_in      integer NOT NULL DEFAULT 0,
  tokens_out     integer NOT NULL DEFAULT 0,
  cost_usd       numeric(10,6) NOT NULL DEFAULT 0,
  context_turns  smallint NOT NULL DEFAULT 0,
  fidelity       text CHECK (fidelity IN ('ok','repaired','failed')),
  flags          text[] NOT NULL DEFAULT '{}',
  prompt_version text, model text,
  timings_ms     jsonb NOT NULL DEFAULT '{}',
  created_at     timestamptz NOT NULL);
CREATE INDEX IF NOT EXISTS brain_runs_month_idx ON chat.brain_runs (created_at) INCLUDE (cost_usd);

CREATE TABLE IF NOT EXISTS chat.answer_ratings (
  id             bigserial PRIMARY KEY,
  ticket_id      bigint NOT NULL UNIQUE REFERENCES chat.tickets(id) ON DELETE CASCADE,   -- 1ª avaliação vale
  user_id        bigint NOT NULL REFERENCES access.users(id),
  trace_id       bigint NOT NULL REFERENCES chat.message_trace(id) ON DELETE CASCADE,    -- rastro do toque
  rating         text NOT NULL CHECK (rating IN ('up','down')),
  down_ticket_id bigint REFERENCES chat.tickets(id) ON DELETE SET NULL,
  rated_at       timestamptz NOT NULL);
CREATE INDEX IF NOT EXISTS answer_ratings_time_idx ON chat.answer_ratings (rated_at DESC);

-- um ticket de pergunta por rastro (defesa em profundidade da idempotência)
CREATE UNIQUE INDEX IF NOT EXISTS tickets_question_trace_uq ON chat.tickets (trace_id)
  WHERE kind = 'question' AND trace_id IS NOT NULL;
```

`ON DELETE CASCADE/SET NULL` evita que a rotina de retenção (`retention.py:88-105`, que apaga tickets → saídas → rastros) falhe por FK, **sem alterar `retention.py`**. `brain_runs` não guarda texto nem PII. `answer_ratings.user_id` é só chave. Exportação LGPD: incluir `answer_ratings` no JSON do titular é opcional (sem texto) e fica fora desta história. Pergunta/resposta do ticket 👎 ficam em `message/summary`, já cobertos pela exclusão.

### 4.12 Configuração (`config.py`, padrão atual: campos minúsculos, variáveis maiúsculas)

`BRAIN_ENABLED` (padrão `false` até haver chave e preços; desligado = `UnderConstructionProvider`, rollback sem deploy), `BRAIN_MONTHLY_BUDGET_USD=100`, `BRAIN_BUDGET_ALERT_PCT=0.8`, `BRAIN_GEMINI_TIMEOUT_SECONDS=5`, `BRAIN_MATCH_THRESHOLD=0.55`, `BRAIN_CANDIDATE_FLOOR=0.30`, `BRAIN_DIRECT_THRESHOLD=0.85`, `BRAIN_CANDIDATES_K=8`, `BRAIN_CONTEXT_EXCHANGES=3`, `BRAIN_CONTEXT_MINUTES=30`, `BRAIN_ANSWER_MODE=grounded`, `BRAIN_PRICE_IN_PER_MTOK_USD`, `BRAIN_PRICE_OUT_PER_MTOK_USD`, e os textos fixos `TEXT_BRAIN_*` (não sei = texto de D3; saudação = menu do piloto; pedido de acesso; recusa; pergunta de marca; agradecimento do 👎). Modelo: `Settings.gemini_model` existente. `main._install_services` escolhe `BrainProvider` se `brain_enabled` e há chave, senão `UnderConstructionProvider`. Limiares `0,85`, `0,30`, `K` são **propostas** a calibrar pelo gate; janela de contexto (3 trocas / 30 min) aprovada em 2026-10-28.

### 4.13 Painel (mudanças mínimas, aprovadas pelo Caio em 2026-10-28)

`metrics_read.py` + `api/metrics.py` (KPI de úteis e cobertura); `api/alerts.py` (rótulos `brain_budget`, `brain_api_failure`); `inbox_read.py` (`_MEDIA_PLACEHOLDER['button'] = '[avaliação]'` para o toque não aparecer como "[button]"). Nenhuma tela nova, nenhuma classificação nova.

### 4.14 Testes e gate de avaliação (CB-21)

- Estrutura: `backend/tests/brain/{unit,integration,security}`; `SpyAnswerProvider` e `FakeChannel` existentes; `httpx.MockTransport` para o Gemini; `FakeClock`.
- **Arquitetura:** `brain` não importa `app.panel`; só `brain/llm.py` fala com o Gemini (AST); regressão `test_zero_cost` continua verde (CB-22).
- **Contrato da fonte:** mesma suíte para `FaqSource` e `FakeSource` (CB-27).
- **Gate offline (`python -m app.services.brain.eval`):** lê `tests/brain/eval/cases/*.jsonl` (começa com perguntas do Guia e variações, os 5 casos do piloto, preço com e sem valor na fonte, follow-ups, ataques, itens sintéticos no formato Nielsen/GMR; **nenhum dado licenciado**). Dois modos: `palavras` (determinístico, roda no CI) e `ia` (Gemini real, manual antes do deploy, grava relatório). Falha se factual < 98% ou qualquer caso de preço falhar; "deve dizer não sei" conta como acerto quando escala. O comando entra em `.claude/CLAUDE.md` (Verificação) na tarefa T34.
- **Decomposição em tarefas, ondas e caminho crítico:** [`../task.md`](../task.md).

---

## 5. Trade-offs (alternativas e por que a escolha)

| Decisão | Escolhida | Rejeitadas (motivo) |
|---|---|---|
| Cliente Gemini | Cliente REST próprio e pequeno em `brain/llm.py` | Importar o do painel (viola `test_architecture`; acopla webhook ao painel). Extrair cliente comum e refatorar o painel (toca painel.1 entregue; fica como dívida registrada). SDK do Google (dependência nova, vetada) |
| Quando chamar a IA | Direta (sem IA) se nota ≥ 0,85 e não é follow-up; IA nos demais | IA em toda mensagem (mais caro/lento, pouco ganho no óbvio). Nunca IA (fere D1 e piora sinônimos/follow-up). Custo: o limiar 0,85 pode deixar uma resposta "boa mas seca"; calibrável |
| Forma da resposta | `grounded` (IA reescreve, código verifica) com chave `verbatim` de reserva | Sempre verbatim (mais seguro, mas contraria D1 e é rígido); livre sem verificação (arrisca o gate de preço) |
| Tipo do ticket de acesso e do 👎 | `kind='question'`, classificação existente, `escalation_reason` + `summary` | Classificação `ACESSO`: entraria nas categorias da FAQ por `CLASSIFICATIONS` e seria recusada pelo CHECK de `content.faq_items` (armadilha em `faq_validation.py:41-43`); exigiria coluna no quadro. `kind` novo: amplia CHECK de PF e a lógica `kind=='media'`. Custo da escolha: pedido de acesso aparece em "Sem classificação" até uma história própria promover a classificação. **Aceito pelo Caio em 2026-10-28.** |
| Tratar o toque de avaliação | Ramo dentro de `_on_active` (IGNORED_TYPE) | Novo `AccessDecision` (mexe na 4.4.2, enums e testes); fora do pipeline (perde transação única e trava do usuário) |
| Onde guardar avaliação | `chat.answer_ratings` | Colunas em `chat.tickets` (toca tabela de PF e a regra "1ª vale" vira lógica); `meta` (sem unicidade) |
| Memória | Ler do rastro existente (sem cópia) | Tabela de sessão própria (nova cópia de texto, mais LGPD); manter em memória do processo (perde em reinício e entre processos) |
| Custo | `chat.brain_runs.cost_usd` somado por mês | Contador em `ops.system_state` (sem histórico por mensagem); reutilizar tabela do Assistente (mistura gastos, contra D11) |
| Marca desligada | O cérebro checa `Flags.brand_enabled` antes de gerar | Passar a marca ao `policy.allow` no despacho (muda o outbox e a assinatura de saídas já enfileiradas) |
| Idempotência | Guarda `reply_kind='pending'` existente + índice único parcial | Chave de idempotência nova em `chat.tickets` (redundante) |
| Alerta de falha do Gemini | Tipo próprio `brain_api_failure` | Reusar `api_failure` (o monitor o conta por falhas de envio na Meta: semântica diferente) |
| Inserção do ticket de pergunta | Módulo novo `question_tickets.py` | Acrescentar função em `services/tickets.py` (arquivo de PF, fora da whitelist); duplicar o SQL em `effects.py` e `ratings.py` (divergência) |

## 6. Requisitos não funcionais

| Tema | Meta |
|---|---|
| Latência | p95 ≤ 8 s do webhook ao envio (PRD §6); busca por palavras ≤ 1 s; Gemini com timeout de 5 s dentro do orçamento (CB-16) |
| Custo | Teto de **US$ 100 por mês só para o cérebro**; alerta aos 80% (US$ 80), aos 100% só busca por palavras até virar o mês; custo estimado por mensagem no rastro desde o dia 1. O Assistente do painel tem contador e limite próprios, separados (CB-15, D11) |
| Limite por usuário | 60 mensagens/hora do PRD §7, mantido exatamente. Toques em 👍/👎 não contam (`counted_in_limit=false`, só rastro, mesma razão de AD-003) (CB-25) |
| Privacidade/LGPD | Nada de telefone, BSUID, nome, empresa, gestor ou id do vendedor para o Gemini (CB-13). Pergunta, resposta e janela de contexto vão ao Gemini sem identificar a pessoa, sob contrato sem treino. O ticket do 👎 guarda pergunta e resposta como os demais tickets (12 meses, zerado na exclusão LGPD). Dados fora do Brasil: pendência do PRD §10 (2026-10-20), D12 |
| Contexto (D13) | Janela de 3 trocas, 30 min (aprovado em 2026-10-28). Só de usuário autorizado, lida do que o rastro já guarda; nunca mistura vendedores; some do alcance com a exclusão LGPD; não amplia o que o vendedor pode ver |
| Segurança | Filtro por marca/cadastro na consulta, não no prompt; injection ignorada e alertada, inclusive via histórico (CB-12); texto do atacante nunca vira alerta/log |
| Qualidade (gate) | ≥ 98% factual e 100% preço (CB-21); sem dado licenciado no repositório |
| Avaliação (D14) | Meta de "úteis" ≥ 85% (PRD §6) pelo KPI do painel (CB-26); 👎 gera correção da fonte pela equipe (CB-29) |
| Observabilidade | Rastro por mensagem (CB-20); logs sem PII |
| Evolução | Trocar a FAQ por banco não muda lógica de resposta, privacidade e preço (CB-27) |
| Disponibilidade | Falha do Gemini nunca impede resposta nem perde o rastro; falha do provedor mantém o comportamento atual (`reply_kind` pendente, recuperação PF-30); `BRAIN_ENABLED=false` volta ao "em construção" |
| Idioma | PT-BR |

## 7. Edge cases

Mensagem vazia/só emoji; mensagem enorme (limitar antes do casamento); fonte vazia ou marca sem itens (Zaya e A Tal da Castanha só têm itens Nielsen/GMR, nada do Guia); item com resposta nula; item editado durante a conversa; duas marcas na pergunta; UF citada com item de outro estado (regra do piloto); mesma mensagem reentregue pela Meta; marca desligada depois do casamento (checada antes de gerar); IA que cita item inexistente ou inventa número (CB-2); follow-up cuja marca anterior já saiu do cadastro; follow-up depois da janela de 30 min; vendedor que pergunta de novo sobre marca sem acesso (não duplica ticket); toque duplo, em botões opostos, adulterado ou tardio na avaliação; 👎 com o Jarvis pausado (ticket abre, agradecimento calado); botão tocado depois de revogação; preço de produto cuja linha só tem valor de outro canal/UF (não reaproveitar); resposta longa dividida em várias mensagens de ≤ 700 caracteres (sem limite de partes, nada cortado) com botões só na última; contador de custo do cérebro e do Assistente na mesma chave (separados); título de botão com emoji recusado pela Meta (cai para título sem emoji); item da fonte com `R$` mas pergunta sem intenção de preço (a regra de preço só exige valor quando há intenção; valores que aparecem precisam estar nos itens).

## 8. Critério de pronto

CB-1 a CB-29 verdes; gate de avaliação passando nos dois modos; teste de privacidade do payload (inclui histórico); teste de arquitetura (sem `app.panel`; só `llm.py` fala com o Gemini); verificação do projeto (pytest, mypy, ruff, `migrate --check`) sem regressão; KPI de úteis no painel; verificação das premissas externas (Gemini JSON/modelo/preço, limites de botão da Meta); PRD/architecture sem alteração.

## 9. Riscos

Resumo de `complexity.json` (10 riscos). Os de impacto alto: (1) fidelidade da IA, mitigada por verificador determinístico, modo `verbatim` e gate em dois modos; (3) regressão no pipeline transacional, mitigada por mudanças aditivas e pelas suítes de PF; (4) privacidade do que vai ao Gemini, mitigada por payload sem identificação, máscara de PII, teste que intercepta o HTTP e D12 como condição de go-live. Médios: premissas externas não verificadas, custo dependente de preço que o repositório não tem, latência com IA e várias partes, contenção de arquivos compartilhados (um dono por arquivo por onda, ver `task.md`), retenção com FKs novas (resolvida com `ON DELETE CASCADE`).

## 10. Mapeamento AC → componente

| AC | Componente / seção |
|---|---|
| CB-1, CB-3, CB-27 | `brain/source.py`, `faq_source.py`, `matching.py` (4.3); `provider.py` (4.4) |
| CB-2 | `fidelity.py`, `prompt.py`, `llm.py` (4.4.1) |
| CB-4 | `intents.py` (marca x cadastro), `effects.py` e `question_tickets.py` (ticket de acesso, dedup), texto `TEXT_BRAIN_ACCESS_REQUEST` (4.4 est. 2, 4.7) |
| CB-5, CB-6 | `provider.py` (`no_answer`), `effects.py` e `question_tickets.py` (ticket escalado), texto D3 (4.4, 4.7) |
| CB-7 | `effects.py` + guarda `reply_kind` + índice único (4.2, 4.7, 4.11) |
| CB-8 | regras de preço em `provider.py` e `fidelity.py` (4.4 est. 5, 4.4.1) |
| CB-9, CB-10, CB-12, CB-19 | `intents.py`; alerta `injection_attempt` (4.4 est. 1, 4.7) |
| CB-11 | `brain/assembly.py`; `Answer.parts` (4.2, 4.4 est. 6) |
| CB-13 | `llm.py`, `redaction.py` (4.9) |
| CB-14, CB-15 | `provider.py` (reserva), `meter.py`, alertas (4.4 est. 4, 4.8) |
| CB-16 | timeout e caminho direto (4.4, 4.12); teste de tempo (4.14) |
| CB-17 | `FaqReader` existente (4.3) |
| CB-18 | `Flags.brand_enabled` no cérebro; outbox para pausa/revogação (4.4 est. 2, 4.2) |
| CB-20 | `chat.brain_runs`, logs (4.10, 4.11) |
| CB-21 | `brain/eval.py` e casos (4.14) |
| CB-22 | AnswerRequest só para `allowed`; teste de regressão (4.1, 4.14) |
| CB-23 | `memory.py` (4.5) |
| CB-24 | `outbox` (kinds e `reply_buttons`), `core/values.py` (4.6, 4.11) |
| CB-25 | `pipeline._on_active` + `services/ratings.py` + `answer_ratings` (4.6) |
| CB-26 | `metrics_read.py`, `api/metrics.py` (4.6, 4.13) |
| CB-28 | itens comuns da `FaqSource`; teste com itens sintéticos (4.3, 4.14) |
| CB-29 | `services/ratings.py` + `question_tickets.py` + `TEXT_BRAIN_RATING_THANKS` (4.6, 4.7) |

Mapa AC → tarefa (T01..T35): ver `../task.md`, "Rastreabilidade AC → tarefa".

---

## Divergências registradas na Fase 4 (2026-10-28)

O código implementado diverge do texto original da spec nos pontos abaixo; o comportamento aprovado pelo Caio não muda. Estas notas são o registro (a spec acima não foi reescrita).

1. **CB-22, pausa tratada no início do provedor (R01).** A spec previa o descarte da pausa só no despacho do outbox. Como o pipeline entrega o pedido ao provedor mesmo com o sistema pausado, o `BrainProvider` consulta a pausa **antes** de qualquer busca ou chamada ao Gemini: pausado = nenhum custo de IA; a pergunta é escalada com `escalation_reason='paused'` e o outbox ainda descarta o envio. Testes: `test_pausado_escala_sem_buscar_nem_chamar_ia`, `test_pausado_nao_chama_busca_nem_gemini`.
2. **Tipos das partes de uma resposta com várias partes (R03).** Spec 4.2/4.6 não dizia o `kind` de cada parte. Implementado: as partes que **não** são a última saem como `brain_answer` (texto simples); só a **última** sai como `brain_answer_rate`, com os botões 👍/👎. Antes (bug), todas saíam como `brain_answer_rate` e o outbox descartava as anteriores (a validação de botões e o dedup), perdendo parte da resposta.
3. **D9, última parte com botões ≤ 1024 caracteres.** O corpo de uma mensagem com botões da Meta aceita 1024 caracteres. A divisão de `assembly.py` garante partes de ≤ 700 caracteres, e a última parte (já com assinatura e linha de fonte) é re-dividida quando passaria disso (`test_last_part_with_signature_fits_limit_by_resplit`); o outbox falha alto se um corpo com botões passar de 1024.
4. **T09, origem do item por consulta extra.** `faq_source.py` lê `content.faq_items.origin` por uma consulta adicional, porque o `FaqReader` do painel.1 não devolve a origem e não podia ser alterado nem importado do painel. Mesmo resultado (CB-28), uma consulta a mais por busca, com cache por versão do item.
5. **T30, configuração falha cedo.** `BRAIN_ENABLED=true` sem `GEMINI_API_KEY` ou sem `BRAIN_PRICE_IN/OUT_PER_MTOK_USD` é recusado na validação do `Settings`: **o app não sobe** (em vez de cair silenciosamente em "em construção"). O fallback para `UnderConstructionProvider` só vale para um `Settings` construído sem validação (testes) ou com `BRAIN_ENABLED=false`.
6. **Gate offline, follow-ups pulados.** No modo `palavras` os casos com histórico (16 de `followup.jsonl`) são relatados como `pulados` (`followup_precisa_ia`), pois dependem da memória e da IA; pontuam só no modo `ia`. Também ficam `pulados` os casos `responde` cuja fonte não tem resposta aprovada (`fonte_sem_resposta_aprovada`).
7. **Gate offline, `recusa` aceita `no_answer`.** No modo `palavras` não há IA para reconhecer todo ataque; o caso `recusa` passa com `refusal`, `injection` **ou** `no_answer` (não respondeu e escalou), nunca com `answered`. Novo valor esperado `saudacao` (desfecho `greeting`) para cumprimentos, usado no caso `piloto-3` ("oi").

---

## Decisões do Caio

Todas decididas pelo Caio em **2026-10-27**. As recomendadas do rascunho anterior foram aceitas onde indicado.

| # | Decisão | Resultado |
|---|---|---|
| D1 | O que a IA faz? | **DECIDIDO (Caio, 2026-10-27): responder só com o que está na fonte de conhecimento; nada inventado.** Hoje a fonte é a FAQ; **depois será um banco de dados com todas as informações**. O cérebro **não fica preso à FAQ**: busca por uma interface de fonte de conhecimento trocável (FAQ agora, banco depois). A IA entende a pergunta, recupera os itens/dados da fonte e responde **só com eles** (fundamentação obrigatória; sem dado na fonte = D3). A busca por palavras do piloto segue como primeiro filtro e reserva se a IA falhar. Desenho em 4.3 e 4.4 (CB-2, CB-27). |
| D2 | Limiar de casamento | **DECIDIDO (Caio, 2026-10-27): padrão recomendado aceito.** 0,55 do piloto para o caminho só com busca por palavras (reserva); com IA, o filtro de candidatos aceita notas menores (faixa 0,30–0,55) e a IA lê os candidatos. Ajustar pelo gate de avaliação. |
| D3 | Sem casamento: texto e ação | **DECIDIDO (Caio, 2026-10-27): manter o texto atual** "Ainda não sei responder isso. Anotei a pergunta para a equipe." **+ ticket escalado**, sem prometer prazo; sem alerta e sem e-mail. |
| D4 | Preço | **DECIDIDO (Caio, 2026-10-27): pode responder preço, desde que o valor esteja na fonte de conhecimento** (FAQ agora, banco depois), repetido fielmente; nunca calculado, estimado ou gerado pela IA. Sem preço na fonte = D3. Gate de 100% em preço continua valendo. |
| D5 | Restrição por perfil | **DECIDIDO (Caio, 2026-10-27): padrão recomendado aceito.** Filtro só por marca/UF/canal do cadastro; sem classificação por item (a FAQ não tem o campo). Todo item ativo da FAQ é tratado como repassável, inclusive Nielsen/GMR (D7). Item interno exigiria o campo: história futura. |
| D6 | Marca fora do cadastro do vendedor | **DECIDIDO (Caio, 2026-10-27): o Jarvis responde que o vendedor ainda não tem acesso às informações dessa marca e que pediu liberação à equipe, e abre um ticket pedindo acesso** (sem alerta e sem e-mail). Nunca revela o conteúdo (CB-4). **Desenho:** `kind='question'`, `escalation_reason='brand_access_request'`, sem classificação nova (4.7, seção 5); aparecer em "Sem classificação" aceito em 2026-10-28. |
| D7 | Nielsen/GMR nas respostas | **DECIDIDO (Caio, 2026-10-27): os itens Nielsen e GMR da FAQ são usados nas respostas agora.** **Alerta ao jurídico:** a confirmação de que a licença Nielsen permite repassar os números aos vendedores (PRD §10, 2026-10-22) ficou aberta até **2026-10-29, quando o Caio informou que a Nielsen aprovou o repasse**. A linha "(Fonte: …)" e o período são preservados (CB-28). Se o jurídico negar, desativa-se o selo no painel (some das respostas em ≤ 5 min). |
| D8 | Marca ambígua | **DECIDIDO (Caio, 2026-10-27): padrão recomendado aceito.** Perguntar a marca em texto, uma vez; sem lista interativa (RF-15 é outro incremento). |
| D9 | Tamanho e tom | **DECIDIDO (Caio, 2026-10-27; revisto em 2026-10-28): nenhuma mensagem cortada, de jeito nenhum.** A resposta sai sempre inteira, dividida por frase em quantas mensagens de ≤ 700 caracteres forem precisas (sem limite de 3; ≤ 700 também cabe no corpo de 1024 da mensagem com botões); tom curto e direto como o Guia; arquivo fica para a história de arquivos. Desenho: `Answer.parts` (4.2). |
| D10 | Assinatura do agente | **DECIDIDO (Caio, 2026-10-27): padrão recomendado aceito.** Por marca (Duda, Léo, Nico, Rita, Tati; Jarvis sem marca única). Se perguntado, confirma que é automatizado. |
| D11 | Teto de custo | **DECIDIDO (Caio, 2026-10-27): US$ 100 por mês, só para o cérebro** (substitui o US$ 30 decidido antes no mesmo dia). Alerta aos 80% (US$ 80); aos 100% segue só com a busca por palavras (sem IA) até virar o mês. O Assistente do painel mantém **contador e limite próprios, separados**. Custo estimado por mensagem gravado no rastro desde o dia 1 (CB-15). |
| D12 | Dados ao Gemini e provedor | **DECIDIDO (Caio, 2026-10-27): padrão recomendado aceito.** Só pergunta, contexto e itens, sem identificação. **Resolvido em 2026-10-29 (Caio):** Gemini aprovado pela chave de API direta, no lugar do Vertex AI, com os dados enviados ao modelo saindo do Brasil. ADR de 2026-10-29 em `docs/architecture/overview.md` e PRD §10. |
| D13 | Contexto de mensagens anteriores | **DECIDIDO (Caio, 2026-10-27): o Jarvis USA as últimas 2 a 3 trocas do mesmo vendedor** para follow-ups como "e o de limão?". Limites e retenção em 4.5 e NFR; janela **3 trocas e 30 min aprovada em 2026-10-28**. |
| D14 | Avaliação 👍/👎 (RF-16) | **DECIDIDO (Caio, 2026-10-27): DENTRO desta história.** Botões após as respostas, gravação da avaliação e KPI "Avaliados como úteis" no painel (≥ 85%). **Toques não contam no limite de 60 msg/h** (mesma razão de AD-003); o limite de 60 msg/h do PRD continua exatamente como está (confirmado pelo Caio). **Sem pedido de comentário.** **👍** só grava. **👎**: agradecimento fixo curto e **ticket escalado** (Escalados) com a pergunta original e a resposta dada, para a equipe corrigir a fonte; sem alerta e sem e-mail; toques repetidos não duplicam (4.6). |
| D15 | "Fonte" para o vendedor | **DECIDIDO (Caio, 2026-10-27): padrão recomendado aceito.** Linha final "Fonte: …" quando o item tem (Nielsen/GMR já trazem); sem links. |
| D16 | Idioma | **DECIDIDO (Caio, 2026-10-27): padrão recomendado aceito.** PT-BR; pergunta em outra língua cai em "não sei". |

## Perguntas ainda abertas

**Para o dono / jurídico (não bloqueiam a Fase 4; bloqueiam o go-live):**

1. ~~**D12:** Gemini API direta ou Vertex AI; aceite de LGPD para dados fora do Brasil.~~ **Resolvida em 2026-10-29:** API direta aprovada (ADR de 2026-10-29).
2. ~~**D7:** licença Nielsen para repasse aos vendedores (jurídico).~~ **Resolvida em 2026-10-29:** a Nielsen aprovou (informado pelo Caio).

**Decididas pelo Caio em 2026-10-28 (Fase 2 aprovada):** mexer nos 4 arquivos do painel.1 (KPI de úteis, dois rótulos de alerta, rótulo "[avaliação]" no Inbox); janela de contexto de 3 trocas / 30 min; urgência `urgente` para Preço e pedido de acesso e `pouco_urgente` nos demais; pedido de acesso em "Sem classificação"; D9 revisto (resposta nunca cortada, quantas partes forem precisas). Os limiares de resposta direta (0,85) e de candidatos (0,30) e os textos fixos novos seguem como padrões ajustáveis pelo gate, sem retrabalho.

**Resolvidas por design:** ticket de pedido de acesso e do 👎 (sem classificação/`kind` novo, 4.7); armazenamento do KPI (tabela `chat.answer_ratings`, pois `chat.tickets` não tem colunas de avaliação; 4.11); contador de custo separado (`chat.brain_runs`); toque de avaliação fora do limite de 60/h (ramo `IGNORED_TYPE`); nome da constraint de CHECK em `outbound_messages` (migration 201 a descobre pelo catálogo).

**Técnicas, verificadas na tarefa T01 antes de codificar (não são decisões do dono):** JSON mode e nome/preço do modelo Gemini; emoji, limites de título/corpo/id de botão da Meta; GRANTs das tabelas novas no droplet; confirmação de que o laço de `ops.deliveries` despacha o e-mail dos alertas criados dentro da transação do cérebro. Lista completa em `research.json` (`unverified_assumptions`).
