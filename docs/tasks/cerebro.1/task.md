---
task_id: CEREBRO-1
phase: 3-tasks
status: draft
spec: spec/spec.md
total_tasks: 35
total_waves: 10
estimated_hours: 106.5
generated: 2026-10-28
---

# Task breakdown - CEREBRO-1 (Cerebro do Jarvis)

Fonte: `spec/spec.md` (Fase 1 aprovada em 2026-10-27; Fase 2 aprovada por Caio em 2026-10-28, com D9 revisto: resposta nunca cortada, dividida em quantas partes de ate 700 caracteres forem precisas), `spec/requirements.json` (CB-1..CB-29), `spec/complexity.json`, `spec/research.json`. Aguardando aprovacao da Fase 3 antes de qualquer codigo.

## Convencoes

- **IDs:** `CB-T01..CB-T35` (prefixo `CB-T` para nao colidir com os criterios `CB-n`; mesmo estilo de `PN-Txx` do painel). Duracao 1-4 h, incluindo os testes da propria tarefa (T01 e T35 sao de documentacao/manual, sem codigo). Commit: `type(cerebro): CEREBRO-1.Txx - descricao`. Checkbox de estado: `[ ]` pendente, `[~]` em andamento, `[x]` pronta; `[P]` = paralela na onda.
- **Caminhos** relativos a raiz do repo. `B/` = `backend/`, `BT/` = `backend/tests/brain/`, `BP/` = `backend/tests/panel/`. Todo comando roda a partir de `backend\` (CLAUDE.md, Verificacao).
- **Whitelist:** a Fase 4 so toca os arquivos da coluna "Arquivos" da tarefa. Testes de outra tarefa so mudam quando listados ("se necessario" = apenas se o teste existente quebrar por causa da mudanca).
- **Verificacao padrao** de toda tarefa com codigo: o `pytest` indicado + `.venv\Scripts\python -m ruff check app tests` + `.venv\Scripts\python -m mypy app`. Tarefas de migration somam `.venv\Scripts\python -m app.db.migrate --check` (contra o banco de teste). A suite inteira (`.venv\Scripts\python -m pytest`) roda no fim de cada onda e em T34. Testes `db` usam `jarvis_test` via `TEST_DATABASE_URL` e nunca o banco do droplet.
- **Compatibilidade:** codigo 3.12 / PostgreSQL 16 (dev em 3.14 / PG 18). Imports absolutos (`from app...`). Sem dependencia nova: httpx, asyncpg e pydantic-settings ja estao no projeto (`complexity.json`).
- **Arquitetura:** o pacote `app/services/brain/` e `app/services/{ratings,question_tickets}.py` **nao importam `app.panel`** (`tests/panel/unit/test_architecture.py`). Apenas `brain/llm.py` fala com o Gemini.
- **Privacidade nos testes:** nenhum dado licenciado (Nielsen/GMR) no repositorio: itens sinteticos no formato deles. Nenhum `.env` e lido ou escrito pelo agente.
- **Arquivos compartilhados** (`answering.py`, `pipeline.py`, `outbox.py`, `config.py`, `main.py`, `values.py`, `provider.py`): nunca em duas tarefas da mesma onda (ver "Verificacao das ondas").

## Desvios do design (para escopos disjuntos; mesmo comportamento)

1. **`brain/assembly.py`** (novo, T18): divisao em partes de ate 700 caracteres por frase, assinatura e linha de fonte saem de `provider.py` para modulo proprio (puro, testavel sozinho). Spec 4.4 est. 6 continua valendo.
2. **`services/question_tickets.py`** (novo, T16): insercao de ticket `kind='question'` (numero J-nnnn, entrega `group`) compartilhada por `effects.py` e `ratings.py`; evita editar `services/tickets.py` de porta-fechada.1.
3. **Migration 201** remove o CHECK de `kind` achando-o pelo catalogo (`pg_constraint`), sem presumir o nome padrao do PG (premissa da spec 4.11). A criacao do CHECK novo usa o nome `outbound_messages_kind_check`.
4. **`provider.py` em duas tarefas** (T23 caminho por palavras; T28 caminho com IA), em ondas diferentes porque o arquivo e um so.
5. **`main.py` em duas tarefas** (T12: apenas `AppProcessor._answer`, indispensavel porque `answer_and_enqueue` passa a devolver tupla; T30: instalacao do provedor), em ondas diferentes.
6. **`BT/factories.py` e `BT/__init__`** nascem em T02 (infra de teste do cerebro: usuario ativo com marcas, rastro `pending`, itens de FAQ, transporte falso do Gemini).
7. **T01** (verificacao das premissas externas) vira a primeira tarefa; **T35** (gate com IA real e teste no numero de teste) e manual e depende da chave do Gemini.

---

## F0 - Preparacao, dados e contrato de saida

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| CB-T01 `[x]` | Verificar as premissas externas (so documentacao) | 2 | - | `docs/tasks/cerebro.1/spec/verificacao-externa.md` | CB-2, CB-13, CB-24 | Checklist do `research.json` (`unverified_assumptions`): Gemini `responseMimeType` JSON/`temperature`/`usageMetadata`, nome e preco do modelo `gemini-3.8-flash` (fonte e data), emoji e limites (titulo 20, corpo 1024, id 256) dos botoes da Meta, GRANTs, laco de `ops.deliveries`. Cada item "verificado" ou "nao verificavel" com fonte. Sem pytest (sem codigo). Resultado alimenta T11, T13 e T35 |
| CB-T02 `[x]` `[P]` | Migration 201 (kinds e botoes na saida) + infra de teste do cerebro | 3 | - | `B/migrations/201_brain_outbound.sql`, `BT/__init__.py`, `BT/unit/__init__.py`, `BT/integration/__init__.py`, `BT/security/__init__.py`, `BT/conftest.py`, `BT/factories.py`, `BT/integration/test_migration_201.py` | CB-24 (base) | `pytest tests/brain/integration/test_migration_201.py -q` (aceita os tres kinds novos e recusa kind invalido; coluna `reply_buttons`; migration idempotente; `migrate --check`) |
| CB-T03 `[x]` `[P]` | Configuracao do cerebro e textos fixos | 2 | - | `B/app/config.py`, `B/configuracao-modelo.txt`, `BT/unit/test_config_brain.py` | CB-6, CB-15, CB-16 (base) | `pytest tests/brain/unit/test_config_brain.py -q` (padroes da spec 4.12: `BRAIN_ENABLED=false`, US$ 100, 80%, 5 s, 0,55/0,30/0,85, K=8, 3 trocas, 30 min, modo `grounded`; `BRAIN_ENABLED=true` exige chave e precos; `BRAIN_ANSWER_MODE` so `grounded`/`verbatim`; textos D3 e agradecimento do 👎) |
| CB-T04 `[x]` `[P]` | Valores: kinds novos, desfechos e payload de avaliacao | 2 | - | `B/app/core/values.py`, `B/tests/unit/test_values.py` | CB-24, CB-25 (payload) | `pytest tests/unit/test_values.py -q` (`OutboundKind`/`ReplyKind` com `brain_answer`, `brain_answer_rate`, `brain_notice`; enum de desfechos; `rate_up_payload`, `rate_down_payload`, `parse_rating_payload` aceitam so `RATE_UP:<n>`/`RATE_DOWN:<n>` com digitos ASCII) |
| CB-T10 `[x]` | Migration 202 (`brain_runs`, `answer_ratings`, indice de ticket) | 3.5 | T02 | `B/migrations/202_brain_runs_ratings.sql`, `BT/integration/test_migration_202.py` | CB-7, CB-20, CB-25, CB-26 (base) | `pytest tests/brain/integration/test_migration_202.py -q` (CHECKs; `UNIQUE(ticket_id)` da avaliacao; indice unico parcial de ticket `question` por rastro nao afeta `media`; `ON DELETE CASCADE/SET NULL` rodando `retention.purge` real sobre linhas de `brain_runs` e `answer_ratings` sem FK; `migrate --check`). **Serializada depois de T02** |
| CB-T11 `[x]` | Outbox: kinds do cerebro e envio com botoes | 3 | T02, T04 | `B/app/services/outbox.py`, `BT/integration/test_outbox_brain.py`, `B/tests/integration/test_outbox_dispatch.py` (se necessario) | CB-24, CB-18, CB-11 (corpo <= 700) | `pytest tests/brain/integration/test_outbox_brain.py tests/integration/test_outbox_dispatch.py tests/integration/test_outbox_enqueue.py -q` (`REQUIRED_STATUS`=active para os tres kinds; `enqueue(..., reply_buttons=)` grava JSON; `_send_once` usa `send_reply_buttons` so para `brain_answer_rate`; `_validate_content` falha alto sem botoes; pausa descarta os tres kinds; revogado antes do envio descarta) |
| CB-T12 `[x]` | Contrato do `AnswerProvider`: partes, efeitos, trace_id, tupla | 4 | T04, T11 | `B/app/services/answering.py`, `B/app/main.py` (somente `AppProcessor._answer`), `B/tests/integration/test_answering.py`, `BT/integration/test_answering_effects.py`, `B/tests/integration/test_app_e2e.py` (se necessario) | CB-7, CB-11, CB-24, CB-18 | `pytest tests/brain/integration/test_answering_effects.py tests/integration/test_answering.py tests/security/test_zero_cost.py -q` (`Answer("k","t")` continua valido; `parts` vira N saidas na ordem, so a ultima com botoes se `rate`; `effects.apply` roda na transacao 2 antes do enfileirar e devolve ticket; `message_trace.ticket_number` gravado; reexecutar a mesma mensagem desfaz tudo (ticket e saidas); provedor que falha segue `pending`; retorno `tuple[int,...]` e `AppProcessor` despacha a tupla) |

## F1 - Fonte, recuperacao e guardas (sem IA)

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| CB-T05 `[x]` `[P]` | Busca por palavras (port do piloto) e marcas | 4 | - | `B/app/services/brain/__init__.py`, `B/app/services/brain/matching.py`, `BT/unit/test_matching.py` | CB-1, CB-3, CB-10 (base) | `pytest tests/brain/unit/test_matching.py -q` (normalizacao NFD, stopwords, sinonimos, F1 simetrico, `variations` como formas extras; regra de UF e de "no Brasil"; deteccao de marca em slugs com os produtos da Plant Power; os 5 casos validados do piloto com itens sinteticos; margem entre 1o e 2o candidato) |
| CB-T06 `[x]` | Guardas determinicas e classificacao | 4 | T05 | `B/app/services/brain/intents.py`, `BT/unit/test_intents.py` | CB-8, CB-9, CB-12, CB-19 | `pytest tests/brain/unit/test_intents.py -q` (saudacao/agradecimento/vazio/so emoji; padroes de injection em PT-BR; tema interno vs condicao comercial (desconto, prazo, bonificacao); intencao de preco e de calculo de margem; classificacao por palavras-chave e urgencia da tabela 4.7: `urgente` para Preco e acesso, `pouco_urgente` nos demais; corpus de falsos positivos de frases normais de vendedor) |
| CB-T09 `[x]` | Fonte de conhecimento e adaptador da FAQ | 3 | T05 | `B/app/services/brain/source.py`, `B/app/services/brain/faq_source.py`, `BT/unit/test_source_contract.py`, `BT/integration/test_faq_source.py` | CB-1, CB-4, CB-5, CB-17, CB-27, CB-28 | `pytest tests/brain/unit/test_source_contract.py tests/brain/integration/test_faq_source.py -q` (mesma suite de comportamento com `FaqSource` e `FakeSource`; so marcas do cadastro + `todas`; sem duplicar item entre marcas; `answerable=false` para `bot_responde=false`; edicao/desativacao do item reflete via revisao; itens com origem `nielsen`/`gmr` e "(Fonte: ...)" preservados; modulo nao importa `app.panel`) |
| CB-T18 `[x]` `[P]` | Montagem da resposta: partes, assinatura, fonte | 2.5 | - | `B/app/services/brain/assembly.py`, `BT/unit/test_assembly.py` | CB-11, CB-24 (flag `rate`) | `pytest tests/brain/unit/test_assembly.py -q` (divide por frase em quantas partes de <= 700 forem precisas, **nunca corta**; frase unica maior que 700 divide em limite de palavra sem perder texto; assinatura por marca (Duda·Jungle, Leo·Plant Power, Nico·Possible, Rita·A Tal da Castanha, Tati·Zaya, Jarvis para marca unica ausente ou `todas`) so na ultima parte; propriedade: concatenar as partes devolve o texto original) |

## F2 - IA: privacidade, cliente, prompt, memoria e custo

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| CB-T07 `[x]` | Mascara de dados pessoais | 1.5 | - | `B/app/services/brain/redaction.py`, `BT/unit/test_redaction.py` | CB-13 | `pytest tests/brain/unit/test_redaction.py -q` (telefone BR com/sem +55, e-mail, CPF e CNPJ mascarados; texto comum e numeros de produto/preco intactos) |
| CB-T08 `[x]` `[P]` | Verificador de fidelidade | 4 | - | `B/app/services/brain/fidelity.py`, `BT/unit/test_fidelity.py` | CB-2, CB-8 | `pytest tests/brain/unit/test_fidelity.py -q` (`item_refs` fora dos candidatos; numero/%/R$/data fora dos itens citados com normalizacao de milhar e virgula; nome de produto/marca inventado; limite de 1,3x o texto citado; URL/telefone/e-mail fora dos itens; reparo para o texto do melhor item; regra de preco: intencao de preco exige valor R$ presente nos itens; IA simulada que inventa um numero e um preco) |
| CB-T13 `[x]` | Cliente Gemini (REST, JSON) | 4 | T01, T03 | `B/app/services/brain/llm.py`, `BT/unit/test_llm.py` | CB-2, CB-13, CB-14 | `pytest tests/brain/unit/test_llm.py -q` (`httpx.MockTransport`: resposta valida, HTTP 500, timeout de 5 s, JSON invalido, corpo vazio; allow-list `generativelanguage.googleapis.com` e sem redirect; chave so no cabecalho; `usageMetadata` lida; nunca loga prompt/resposta/erro (caplog); `LlmRequest` nao tem campo de usuario) |
| CB-T14 `[x]` | Memoria de conversa | 3 | T10, T03 | `B/app/services/brain/memory.py`, `BT/integration/test_memory.py` | CB-23, CB-12, CB-13 | `pytest tests/brain/integration/test_memory.py -q` (ultimas 3 trocas do mesmo `user_id` dentro de 30 min e `id` menor que o do rastro atual; nunca de outro vendedor; so desfecho `answered`/`ask_brand`; exclui `injection`; corpo anulado pela exclusao LGPD some da janela; usuario nao `active` nao entra; ordem cronologica) |
| CB-T15 `[x]` | Medidor de custo e teto mensal | 3.5 | T10, T03 | `B/app/services/brain/meter.py`, `BT/integration/test_meter.py` | CB-15, CB-20 | `pytest tests/brain/integration/test_meter.py -q` (custo = tokens x preco configurado; gasto do mes no fuso America/Sao_Paulo com `FakeClock` e virada de mes; aviso unico aos 80% (US$ 80) com `dedup_key` mensal e e-mail; aos 100% `ia_permitida=False` ate virar o mes; cache de 30 s; nao soma `ops.assistant_questions`) |
| CB-T17 `[x]` | Prompt e montagem do pedido a IA | 2.5 | T13, T07 | `B/app/services/brain/prompt.py`, `BT/unit/test_prompt.py` | CB-2, CB-12, CB-13 | `pytest tests/brain/unit/test_prompt.py -q` (`PROMPT_VERSION`; pergunta, historico e candidatos delimitados e marcados como dado; texto de ataque permanece dado; mascara aplicada a pergunta e historico; JSON de saida `intent`/`item_refs`/`answer_text`; modo `verbatim` pede so `item_refs`) |

## F3 - Chamados, avaliacao e entrada de botoes

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| CB-T16 `[x]` `[P]` | Servico de ticket de pergunta | 3 | T10 | `B/app/services/question_tickets.py`, `BT/integration/test_question_tickets.py` | CB-6, CB-7, CB-4 (dedup) | `pytest tests/brain/integration/test_question_tickets.py -q` (insere `kind='question'` com numero de `ops.ticket_seq`, entrega `group` `pending`/`panel_only` conforme a chave, nunca e-mail; `escalated` exige urgencia; pergunta em `message` e texto legivel/resposta em `summary`, nada em `meta` que identifique; `find_open_access_request(user, brand)` para o dedup; indice unico por rastro) |
| CB-T21 `[x]` | Servico de avaliacao 👍/👎 | 4 | T16, T10, T04 | `B/app/services/ratings.py`, `BT/integration/test_ratings.py` | CB-25, CB-29 | `pytest tests/brain/integration/test_ratings.py -q` (grava uma vez por ticket; repeticao e botao oposto depois nao inserem; ticket de outro vendedor, inexistente ou payload adulterado e ignorado; 👍 so grava; 👎 cria ticket escalado com classificacao e marca herdadas, `escalation_reason='rating_down'`, pergunta em `message`, resposta dada em `summary`, `urgency` `pouco_urgente`, sem alerta e sem e-mail; devolve o id do agradecimento) |
| CB-T22 `[x]` | Efeitos do desfecho: ticket, `brain_runs`, alerta | 4 | T16, T10, T06, T12 | `B/app/services/brain/effects.py`, `BT/integration/test_effects.py` | CB-4, CB-5, CB-6, CB-7, CB-12, CB-19, CB-20 | `pytest tests/brain/integration/test_effects.py -q` (um `AnswerEffects.apply` por desfecho conforme a tabela 4.7: `answered`, `no_answer` com motivos, `brand_disabled`, `access_request` com dedup por vendedor+marca, `injection`/`refusal` sem escalar, `greeting`/`ask_brand` sem ticket; grava `brain_runs` sem texto; alerta `injection_attempt` com dedup de 1 h e e-mail, sem texto do atacante; escalados nunca geram alerta; devolve `rate=True` so em `answered`) |
| CB-T25 `[x]` | Pipeline: toque de avaliacao e trace_id | 4 | T12, T21, T04 | `B/app/services/pipeline.py`, `BT/integration/test_pipeline_rating.py`, `B/tests/integration/test_pipeline_core.py` (se necessario) | CB-25, CB-29, CB-22 | `pytest tests/brain/integration/test_pipeline_rating.py tests/integration/test_pipeline_core.py tests/integration/test_rate_limit.py tests/security/test_zero_cost.py -q` (ramo dentro do bloco `IGNORED_TYPE` de `_on_active`, antes do limite de 60/h; `counted_in_limit=false` e `decision_reason='rating_up/down'`; usuario no limite ainda avalia; 60 msg/h inalterado; 👎 enfileira agradecimento `brain_notice` em `outbound_ids` e pausa o descarta no despacho; usuario `pending_optin`/`revoked` segue em silencio; botao nao reconhecido continua `ignored_type`; `AnswerContext.trace_id` preenchido em `_text`) |

## F4 - Orquestrador

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| CB-T23 `[x]` | `BrainProvider`: estagios 1-4 e montagem (caminho por palavras) | 4 | T06, T09, T18, T22, T12 | `B/app/services/brain/provider.py`, `BT/integration/test_provider_palavras.py` | CB-1, CB-3, CB-4, CB-5, CB-6, CB-8, CB-9, CB-10, CB-12, CB-18, CB-19, CB-22 | `pytest tests/brain/integration/test_provider_palavras.py -q` (guardas sem custo; marca citada fora do cadastro -> pedido de acesso sem ler item da marca; `Flags.brand_enabled` desligada -> `brand_disabled`; busca na fonte so com marcas permitidas; resposta direta com nota >= 0,85; sem IA, nota >= 0,55 responde e abaixo disso escala; ambiguidade de marca; regra de preco (R$ so da fonte); `bot_responde=false` escala; assinatura e fonte; sem acesso a Gemini neste caminho) |
| CB-T28 `[x]` | `BrainProvider`: caminho com IA, contexto, custo e reserva | 4 | T23, T13, T17, T14, T15, T08, T07 | `B/app/services/brain/provider.py`, `BT/integration/test_provider_ia.py` | CB-2, CB-14, CB-15, CB-16, CB-23, CB-8 | `pytest tests/brain/integration/test_provider_ia.py -q` (candidatos com nota 0,30-0,85 vao a IA; uma chamada por mensagem; verificador de fidelidade e reparo; modo `verbatim`; IA que inventa numero/preco cai no texto do item ou em "nao sei"; follow-up usa a memoria e soma os tokens da ultima pergunta na busca; erro/timeout/JSON invalido/sem chave -> reserva por palavras e alerta `brain_api_failure` deduplicado; orcamento >= 100% pula a IA; custo e tokens no rastro; latencia simulada de 2 s dentro de 8 s) |

## F5 - Painel (alteracoes minimas aprovadas)

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| CB-T19 `[x]` `[P]` | KPI "Avaliados como uteis" | 3.5 | T10 | `B/app/panel/services/metrics_read.py`, `B/app/panel/api/metrics.py`, `BT/integration/test_kpi_uteis.py` | CB-26 | `pytest tests/brain/integration/test_kpi_uteis.py tests/panel/integration/test_metrics_read.py -q` (`up/(up+down)` por periodo e marca via `ops.v_tickets`; cobertura = avaliadas / respostas com botoes; sem avaliacao continua "sem avaliacoes · meta >= 85%"; cache de 60 s existente; sem divisao por zero) |
| CB-T20 `[x]` `[P]` | Rotulos de alerta e do toque no Inbox | 1.5 | - | `B/app/panel/api/alerts.py`, `B/app/panel/services/inbox_read.py`, `BT/integration/test_panel_labels.py` | CB-12, CB-15, CB-14, CB-25 (apresentacao) | `pytest tests/brain/integration/test_panel_labels.py tests/panel/integration/test_inbox_read.py tests/panel/integration/test_inbox_chat.py -q` (rotulos PT-BR `brain_budget` e `brain_api_failure` aparecem e filtram; toque de botao aparece como "[avaliacao]" e nao como "[button]") |

## F6 - Gate de avaliacao, seguranca e integracao

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| CB-T24 `[x]` `[P]` | Conjunto de avaliacao (casos) | 3 | T09 | `BT/eval/cases/guia.jsonl`, `BT/eval/cases/piloto.jsonl`, `BT/eval/cases/preco.jsonl`, `BT/eval/cases/followup.jsonl`, `BT/eval/cases/ataques.jsonl`, `BT/eval/cases/nielsen_gmr_sinteticos.jsonl`, `BT/eval/README.md`, `BT/unit/test_eval_cases_schema.py` | CB-21 | `pytest tests/brain/unit/test_eval_cases_schema.py -q` (esquema: pergunta, cadastro simulado, resultado esperado `responde`/`nao_sei`/`pede_marca`/`acesso`/`recusa`, itens citados; casos de preco com e sem valor na fonte; follow-ups; ataques; itens sinteticos; **nenhum dado licenciado**: varredura rejeita marcadores de Nielsen/GMR reais) |
| CB-T26 `[x]` `[P]` | Testes de arquitetura do cerebro | 2 | T13 | `BT/security/test_architecture.py` | CB-22, CB-13, CB-27 | `pytest tests/brain/security/test_architecture.py tests/panel/unit/test_architecture.py -q` (AST: nada em `services/brain`, `ratings.py`, `question_tickets.py` importa `app.panel`; so `brain/llm.py` referencia o host do Gemini ou `httpx`; `import app.services.pipeline` nao carrega `app.panel`) |
| CB-T29 `[x]` | Executor do gate (`python -m app.services.brain.eval`) | 3.5 | T28, T24 | `B/app/services/brain/eval.py`, `BT/unit/test_eval_runner.py` | CB-21 | `pytest tests/brain/unit/test_eval_runner.py -q` (modo `palavras` determinístico sem rede; modo `ia` com chave e relatorio; falha com codigo != 0 se factual < 98% ou qualquer caso de preco falhar; "deve dizer nao sei" conta como acerto quando escala; cobertura por categoria; nunca imprime telefone nem texto fora do relatorio local) |
| CB-T30 `[x]` | Instalacao do provedor no `main` | 3 | T28, T12 | `B/app/main.py`, `BT/integration/test_brain_wiring.py` | CB-14, CB-16, CB-22 | `pytest tests/brain/integration/test_brain_wiring.py tests/integration/test_app_e2e.py tests/security/test_zero_cost.py -q` (`BRAIN_ENABLED=false` mantem `UnderConstructionProvider`; `true` com chave instala o `BrainProvider` com `FaqReader`, `FaqSource`, cliente e medidor; sem chave volta ao "em construcao"; `SpyAnswerProvider` ainda substitui o provedor nos testes) |
| CB-T31 `[x]` | Seguranca: payload ao Gemini, injection e custo zero | 3.5 | T28 | `BT/security/test_payload_privacy.py`, `BT/security/test_injection_corpus.py`, `BT/security/test_zero_cost_brain.py` | CB-12, CB-13, CB-22 | `pytest tests/brain/security -q` (intercepta o HTTP e varre o corpo: sem telefone, BSUID, nome, empresa, gestor nem id do vendedor, inclusive no historico; corpus de ataques em PT-BR nao altera escopo/perfil e gera `injection_attempt` sem o texto do atacante; numero desconhecido, revogado, sem aceite, pausado ou acima de 60/h nunca chama busca nem Gemini) |
| CB-T32 `[x]` | Teste de ponta a ponta (webhook -> resposta -> avaliacao) | 4 | T30, T25, T19 | `BT/integration/test_e2e_flow.py` | CB-1, CB-6, CB-7, CB-24, CB-25, CB-29, CB-26 | `pytest tests/brain/integration/test_e2e_flow.py -q` (`create_app` + `FakeChannel` + transporte Gemini falso: pergunta coberta -> varias partes na ordem, botoes so na ultima; toque 👍 grava; toque 👎 -> agradecimento + ticket em Escalados; toque repetido nao duplica; "nao sei" cria ticket; pedido de marca sem acesso; pausa cala resposta e agradecimento; marca desligada escala; KPI de uteis aparece nas Metricas) |
| CB-T33 `[x]` | Testes transversais: LGPD, retencao, latencia, rastro | 3 | T30 | `BT/integration/test_lgpd_retention.py`, `BT/integration/test_latency_trace.py` | CB-16, CB-20, CB-23 | `pytest tests/brain/integration/test_lgpd_retention.py tests/brain/integration/test_latency_trace.py -q` (`lgpd.delete_person` zera pergunta/resposta dos tickets e a janela de contexto fica vazia; `retention.purge` com `brain_runs`/`answer_ratings` nao falha; `brain_runs` sem texto; p95 simulado <= 8 s com Gemini de 2 s e caminho por palavras <= 1 s; campos do rastro preenchidos) |

## F7 - Fechamento

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao |
|---|---|---|---|---|---|---|
| CB-T34 `[x]` | Fechamento: suite completa, gate offline, documentos | 3 | T01..T33 | `docs/STATE.md`, `.claude/CLAUDE.md` (so a linha de verificacao do comando de avaliacao), `docs/tasks/cerebro.1/task.md` (estados), `docs/tasks/cerebro.1/spec/ac-test-map.md`, `docs/tasks/cerebro.1/spec/complexity.json` (horas reais) | CB-21 e todos (mapa) | `.venv\Scripts\python -m pytest` (suite inteira, 0 pulados no banco de teste), `-m mypy app`, `-m ruff check app tests`, `-m app.db.migrate --check`, `-m app.services.brain.eval --mode palavras` (gate offline passando); mapa AC -> teste completo; STATE.md com decisoes tecnicas e pendencias |
| CB-T35 `[x]` | **MANUAL / OPCIONAL**: gate com IA real e teste no numero de teste | 2 | T34, chave do Gemini no `.env` (Caio), D12 resolvida | `docs/tasks/cerebro.1/gate-ia-relatorio.md` | CB-21, CB-24 | `-m app.services.brain.eval --mode ia` (custo de poucos centavos), conferencia de emoji/botoes no celular de teste, calibragem de 0,85/0,30/janela. Resultado decide `grounded` x `verbatim` e liga `BRAIN_ENABLED` em producao. Executada pelo Caio ou a pedido dele |

---

## Parallel Execution Map

Regra: no maximo 5 tarefas por onda, arquivos disjuntos, nenhuma dependencia entre irmas da mesma onda. Nenhum arquivo compartilhado aparece em duas tarefas da mesma onda.

| Onda | Tarefas | Horas | Observacao |
|---|---|---|---|
| W1 | T01, T02, T03, T04, T05 | 13 | T02 abre o pacote de testes e a migration 201 |
| W2 | T06, T08, T09, T10, T11 | 17.5 | T10 (migration 202) fica **depois** de T02; T11 mexe em `outbox.py` |
| W3 | T07, T12, T13, T14, T15 | 16 | T12 e o unico dono de `answering.py` e do trecho de `main.py` |
| W4 | T16, T17, T18, T19, T20 | 13 | |
| W5 | T21, T22, T24, T26 | 13 | |
| W6 | T23, T25 | 8 | T23 e o unico dono de `provider.py` na onda; T25 de `pipeline.py` |
| W7 | T28 | 4 | `provider.py` de novo (nunca na mesma onda que T23) |
| W8 | T29, T30, T31 | 10 | T30 e o unico dono de `main.py` na onda |
| W9 | T32, T33 | 7 | |
| W10 | T34, T35 | 5 | T35 manual/opcional |

Totais: **35 tarefas, 10 ondas, 106.5 h** (104.5 h sem a T35 manual). `complexity.json` estima ~140 h no total; a diferenca (~25%) e reserva para revisao, retrabalho e calibragem do gate.

**Caminho critico (31.5 h):** T02 (migration 201 + infra) -> T10 (migration 202) -> T16 (ticket de pergunta) -> T22 (efeitos) -> T23 (provedor, palavras) -> T28 (provedor, IA) -> T30 (instalacao) -> T32 (ponta a ponta) -> T34 (fechamento).

### Verificacao das ondas (checagem feita na redacao)

- Dependencias dentro de cada onda: nenhuma. Cada `depends_on` aponta para onda anterior: T06->T05; T09->T05; T10->T02; T11->T02,T04; T12->T04,T11; T13->T01,T03; T14,T15->T10,T03; T16->T10; T17->T13,T07; T21->T16,T10,T04; T22->T16,T10,T06,T12; T23->T06,T09,T18,T22,T12; T25->T12,T21,T04; T28->T23,T13,T17,T14,T15,T08,T07; T29->T28,T24; T30->T28,T12; T31->T28; T32->T30,T25,T19; T33->T30; T34->todas.
- Arquivos compartilhados: `values.py` so em T04 (W1); `config.py` so em T03 (W1); `outbox.py` so em T11 (W2); `answering.py` so em T12 (W3); `pipeline.py` so em T25 (W6); `provider.py` em T23 (W6) e T28 (W7); `main.py` em T12 (W3) e T30 (W8). Migrations: 201 em T02 (W1) e 202 em T10 (W2), nunca na mesma onda.
- Cada tarefa traz os proprios testes; testes de outras tarefas so mudam quando listados na coluna Arquivos.
- Mudancas no painel.1 (T19, T20) sao exatamente as 4 aprovadas por Caio em 2026-10-28: KPI de uteis, dois rotulos de alerta, rotulo "[avaliacao]" no Inbox.

## Rastreabilidade AC -> tarefa

| AC | Tarefas |
|---|---|
| CB-1, CB-3 | T05, T09, T23 |
| CB-2 | T08, T13, T17, T28 |
| CB-4, CB-5 | T09, T16, T22, T23 |
| CB-6, CB-7 | T12, T16, T22, T23 |
| CB-8 | T06, T08, T23, T28 |
| CB-9, CB-10, CB-19 | T05, T06, T23 |
| CB-11 | T11, T12, T18 |
| CB-12 | T06, T14, T22, T31 |
| CB-13 | T07, T13, T17, T31 |
| CB-14, CB-15 | T13, T15, T28, T30 |
| CB-16 | T28, T33 |
| CB-17, CB-27, CB-28 | T09 |
| CB-18 | T11, T12, T23 |
| CB-20 | T10, T15, T22, T33 |
| CB-21 | T24, T29, T34, T35 |
| CB-22 | T25, T26, T31 |
| CB-23 | T14, T28, T33 |
| CB-24 | T01, T02, T04, T11, T12, T18 |
| CB-25, CB-29 | T21, T25, T32 |
| CB-26 | T19, T32 |

## Itens que precisam do Caio antes ou durante a Fase 4

1. **Aprovar esta Fase 3** (35 tarefas, 10 ondas).
2. **D12 e D7 continuam fora do codigo:** Gemini API direta x Vertex AI + LGPD, e licenca Nielsen. Bloqueiam apenas o go-live (e a T35), nao a Fase 4.
3. **Chave do Gemini no `backend/.env`** so e necessaria para a T35 (o agente nao le nem escreve `.env`). Ate la todos os testes usam transporte falso.
4. **Precos por token do modelo** (`BRAIN_PRICE_IN/OUT_PER_MTOK_USD`): vem da T01 com fonte e data; sem eles `BRAIN_ENABLED=true` e recusado pela configuracao.
