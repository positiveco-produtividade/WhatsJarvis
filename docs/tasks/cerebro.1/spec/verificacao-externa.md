# Verificação externa das premissas (CB-T01)

**Tarefa:** CB-T01 (só documentação, sem código). **Data de acesso de todas as fontes:** 2026-10-28.
**Entrada:** `research.json` → `unverified_assumptions` (6 itens) e spec 4.4.1, 4.6, 4.8, 4.11, 4.12.
**Alimenta:** CB-T03 (configuração), CB-T11/CB-T13 (cliente Gemini e medidor), CB-T35 (go-live).

Legenda: **verificado** (com fonte) · **não verificável** (por quê e o que o código faz por defesa) · **divergência** (a fonte contradiz a spec; precisa de ajuste).

## Resumo

| # | Premissa | Veredito | Impacto |
|---|---|---|---|
| 1 | Gemini REST: JSON mode, `temperature`, `usageMetadata`, cabeçalho, erros | **verificado, com 3 divergências** | `temperature` **não** deve ser enviada; pensamento (`thinking`) ligado por padrão afeta latência e custo; tokens de pensamento são cobrados como saída |
| 2 | Modelo `gemini-3.8-flash` e preço | **verificado** | US$ 0,75 / US$ 3,75 por 1M (entrada/saída) até 31/12/2026; **US$ 1,50 / US$ 7,50 a partir de 01/01/2027** |
| 3 | Botões de resposta da Meta (3, 20, 256, 1024) e texto (4096) | **verificado**; emoji **não verificável** na doc | teste manual no número de teste (T35) |
| 4 | Nome da constraint de CHECK de `kind` | **não verificável sem banco**; já neutralizado pela migration 201 | confirmar no teste da T02 |
| 5 | GRANTs das tabelas novas para `jarvis_app` | **verificado no repositório**; **não verificável no droplet** (banco ainda não existe) | runbook do go-live precisa de `ALTER DEFAULT PRIVILEGES` ou GRANT após 202 |
| 6 | Laço de `ops.deliveries` despacha o e-mail do alerta do cérebro | **verificado pelo código** | e-mail sai no próximo ciclo do laço (≤ 15 s após o commit); nada sai se a transação desfizer |

---

## 1. Gemini REST (`generativelanguage.googleapis.com/v1beta/...:generateContent`)

**Veredito: verificado, com divergências em relação à spec 4.4.1 ("temperatura baixa").**

Fontes:
- Referência da API: https://ai.google.dev/api/generate-content (acesso 2026-10-28; também via context7 `/websites/ai_google_dev_api`).
- Guia do Gemini 3.8 Flash: https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.8 ("Last updated 2026-10-13 UTC"; acesso 2026-10-28).
- Guia Gemini 3 (generateContent): https://ai.google.dev/gemini-api/docs/generate-content/gemini-3 (marcado como obsoleto, aponta para o guia do 3.8; acesso 2026-10-28).
- Pensamento: https://ai.google.dev/gemini-api/docs/thinking (acesso 2026-10-28).
- Saída estruturada: https://ai.google.dev/gemini-api/docs/structured-output (acesso 2026-10-28).
- Erros: https://ai.google.dev/gemini-api/docs/troubleshooting e https://ai.google.dev/gemini-api/docs/api-errors (acesso 2026-10-28).

### 1.1 Endpoint e chave — verificado
- `POST https://generativelanguage.googleapis.com/v1beta/{model=models/*}:generateContent` (referência da API).
- A chave vai no cabeçalho `x-goog-api-key` (exemplos REST da doc de saída estruturada). Mesmo padrão do painel (`backend/app/panel/assistant/gemini.py`, allow-list do host `generativelanguage.googleapis.com`).

### 1.2 Saída JSON — verificado
- `generationConfig.responseMimeType`: valores aceitos `text/plain` (padrão), `application/json`, `text/x.enum` (referência da API).
- `generationConfig.responseSchema` está **marcado como obsoleto** ("Deprecated. Use `responseFormat` instead"). Os exemplos atuais usam `responseJsonSchema` (SDK) ou `responseFormat` (REST).
- Limitações documentadas: nem todo recurso de JSON Schema é aceito; esquema grande ou muito aninhado pode voltar `400`; `minLength`/`maxLength` (e outros) **não são aplicados** e são descartados em silêncio; `minItems`/`maxItems` são aplicados.
- **Para o código (T11):** enviar `responseMimeType: "application/json"` (campo não obsoleto). **Não** depender de esquema no servidor: o parse e a validação de `intent`/`item_refs`/`answer_text` continuam no cliente (o verificador `fidelity.py` já faz isso). JSON inválido ou campo faltando = falha da IA → reserva por palavras (plano B da spec continua valendo como defesa).

### 1.3 `temperature` — **divergência**
- A referência genérica aceita `temperature` em [0,0; 2,0].
- **Mas** o guia do Gemini 3.8 Flash chama `temperature`, `top_p` e `top_k` de **parâmetros obsoletos** e manda removê-los ("Remove deprecated sampling parameters"). O guia Gemini 3 diz: "For all Gemini 3 models, we strongly recommend keeping the temperature parameter at its default value of `1.0`" e que valores abaixo de 1,0 podem causar repetição em laço ou queda de qualidade. A página não diz se o valor é ignorado ou se dá erro.
- **Para o código (T11) e a spec:** **não enviar `temperature`** (nem `topP`/`topK`/`candidateCount`, este último "unsupported in Gemini 3 and later"). A previsibilidade vem do prompt fechado, do JSON e do verificador determinístico, não da temperatura. A frase "Temperatura baixa" da spec 4.4.1 deve ser atualizada na implementação (regra do CLAUDE.md: spec acompanha a realidade).

### 1.4 Pensamento (`thinking`) — **divergência / novo requisito**
- `gemini-3.8-flash` tem pensamento **ligado por padrão em `medium`**; níveis aceitos `low`, `medium`, `high`. `minimal` **não é aceito e retorna erro** neste modelo (guia 3.8). Não há como desligar.
- Formato REST: `generationConfig.thinkingConfig.thinkingLevel: "low"` (guia Gemini 3, generateContent).
- `low` é descrito como o de menor latência.
- **Para o código (T11):** enviar `thinkingLevel: "low"`, porque o teto é de 5 s (`BRAIN_GEMINI_TIMEOUT_SECONDS`) e o pensamento consome tokens de saída pagos. Nunca enviar `minimal`. Se a chamada passar de 5 s com frequência, o gate (T35) mede a latência; a reserva por palavras continua cobrindo o timeout.
- Assinaturas de pensamento (thought signatures): chamada de turno único e sem ferramentas não as exige ("Validation is not strictly enforced"). O cérebro faz uma chamada sem estado por mensagem (o contexto vai como texto delimitado), então não precisa devolvê-las.

### 1.5 `usageMetadata` — verificado, com ajuste no medidor
Campos (referência da API): `promptTokenCount`, `cachedContentTokenCount`, `candidatesTokenCount`, `toolUsePromptTokenCount`, `thoughtsTokenCount` ("Number of tokens of thoughts for thinking models") e `totalTokenCount` = "prompt + tool-use prompt + thoughts + response candidates".
- A página de preços diz "Output price (including thinking tokens)" e a de pensamento diz "response pricing is the sum of output tokens and thinking tokens".
- **Para o código (T13, `brain/meter.py`):** `tokens_out = candidatesTokenCount + thoughtsTokenCount`. Contar só `candidatesTokenCount` (padrão do painel, `gemini.py:100-102`) subestima o custo. `tokens_in = promptTokenCount`. Campo ausente = 0. Sem nenhum `usageMetadata` numa resposta 200 (não documentado): gravar 0, marcar em `brain_runs.flags` (sugestão de nome: `usage_missing`) e não falhar a resposta.

### 1.6 Erros e timeout — verificado em parte
- Respostas bloqueadas: `promptFeedback.blockReason` (`SAFETY`, `OTHER`, `BLOCKLIST`, `PROHIBITED_CONTENT`, `IMAGE_SAFETY`) e ausência de `candidates`; `candidates[].finishReason` pode ser `STOP`, `MAX_TOKENS`, `SAFETY`, `RECITATION`, `OTHER`, `MALFORMED_RESPONSE`, entre outros.
- HTTP: 400 (pedido inválido, chave ruim), 402 (créditos pré-pagos esgotados), 403 `PERMISSION_DENIED`, 404 (modelo não encontrado), 408, 429 `RESOURCE_EXHAUSTED`, 500, 503 `UNAVAILABLE`, 504. A doc recomenda retentativa com backoff para 408/429/5xx; o cérebro **não** retenta (spec 4.4.1: uma chamada, depois reserva por palavras).
- **Não verificável:** o formato exato do corpo de erro do `generateContent` clássico. A página `api-errors` mostra `{"error": {"code": "<snake_case>", "message": ...}}` (da API Interactions); o formato histórico do generateContent usa `code` numérico e `status`. **Para o código:** decidir só pelo **código HTTP** e pela presença de `candidates[0].content.parts[*].text`; nunca analisar nem logar o corpo do erro (padrão do painel). Qualquer coisa fora de 200 com texto JSON válido = falha → reserva + alerta `brain_api_failure` (com dedup).
- Timeout: nenhum valor oficial para generateContent sem streaming. O de 5 s é escolha do projeto (`httpx.Timeout(5)`), não da Google.
- Limites de token do modelo (`inputTokenLimit` 1048576, `outputTokenLimit` 65536): verificados pelo chamador em 2026-10-28 via `GET v1beta/models/gemini-3.8-flash` → 200.

## 2. Modelo `gemini-3.8-flash` e preço

**Veredito: verificado.**

- **Existe:** `GET models/gemini-3.8-flash` → 200 em 2026-10-28 (verificação do chamador); também aparece com esse ID na página de preços e no guia 3.8 ("most intelligent Flash model"). A página não diz se é GA ou preview.
- **Fonte de preço:** https://ai.google.dev/gemini-api/docs/pricing (acesso 2026-10-28), confirmada pelo guia https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.8.

Nível pago, **Standard**, USD por 1 milhão de tokens:

| Item | Até 31/12/2026 (preço de lançamento) | A partir de 01/01/2027 (padrão) |
|---|---|---|
| Entrada (texto) | **0,75** | **1,50** |
| Saída (inclui tokens de pensamento) | **3,75** | **7,50** |
| Cache de contexto | 0,075 | 0,15 |
| Armazenamento do cache | 0,50 / 1M tokens / hora | 1,00 |

Sem divisão por tamanho de prompt (≤ 200 mil / > 200 mil) nos modelos Flash. Batch/Flex custam metade; Priority custa 1,35 / 6,75 (depois 2,70 / 13,50); o cérebro usa Standard. O nível gratuito ("Free of charge") usa os dados para melhorar produtos da Google ("Used to improve our products: Yes"); no pago, "No". Para dados de vendedores, **usar a chave do nível pago** (tem relação com a privacidade da spec 4.9 / CB-13).

**Para a configuração (T03) e o go-live (T35):**
- `BRAIN_PRICE_IN_PER_MTOK_USD` e `BRAIN_PRICE_OUT_PER_MTOK_USD` continuam obrigatórios e sem padrão no código.
- Recomendação: preencher já com o preço padrão (**1,50 / 7,50**), mais conservador, para o teto de US$ 100 não ser estourado sem aviso na virada para 2027. Se o Caio preferir o preço de lançamento (0,75 / 3,75), registrar no runbook a troca obrigatória em 01/01/2027. A decisão é do dono.
- Ordem de grandeza (só para referência, não é requisito): uma chamada de ~3.000 tokens de entrada, ~500 de resposta e de 0 a ~500 de pensamento custa ≈ US$ 0,008–0,012 no preço padrão (0,0045 de entrada + 0,0038–0,0075 de saída). O teto do mês cobre perto de 8 mil chamadas.

## 3. WhatsApp Cloud API: botões de resposta e texto

**Veredito: limites verificados; emoji não verificável na documentação.**

Fontes:
- https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-reply-buttons-messages (= https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/interactive-reply-buttons-messages), acesso 2026-10-28.
- https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/text-messages, acesso 2026-10-28.

| Limite | Valor oficial | Código atual |
|---|---|---|
| Botões por mensagem | até **3** | `MAX_REPLY_BUTTONS = 3` (`whatsapp.py:24`) |
| Título do botão | máx. **20** caracteres; **único** quando há mais de um botão | `BUTTON_TITLE_MAX = 20` (`whatsapp.py:25`) |
| `id` do botão | máx. **256** caracteres | não validado hoje (payloads curtos, `RATE_UP:<n>` cabe) |
| Corpo da mensagem interativa | máx. **1024** caracteres | **`body[:TEXT_MAX_CHARS]` = 4096** (`whatsapp.py:172`) |
| Rodapé (opcional) | máx. 60 caracteres | não usado |
| Texto simples (`text.body`) | máx. **4096** caracteres | `TEXT_MAX_CHARS = 4096` |

Consequências:
- **Corpo interativo:** a doc limita a 1024, mas `send_reply_buttons` corta em 4096. A montagem do cérebro (spec 4.4, passo 6) faz partes de ≤ 700 caracteres + assinatura, então a parte com 👍/👎 fica abaixo de 1024. Defesa no cérebro (T-montagem): garantir que a **última parte**, a que leva os botões, tenha ≤ 1024 caracteres **contando a assinatura**; se não couber, quebrar mais uma vez (nunca cortar, D9). O `whatsapp.py` é de porta-fechada.1 e não está na whitelist; registrar como observação, sem mudança.
- **`id` ≤ 256:** `RATE_UP:<id>`/`RATE_DOWN:<id>` tem no máximo ~30 caracteres. Basta um teste unitário do tamanho.
- **Títulos únicos:** "👍" e "👎" são diferentes. OK.
- **Emoji no título — não verificável:** a doc oficial não fala de emoji no título nem no corpo. Fontes de terceiros dizem que emoji pode contar como mais de um caractere (sequências de vários pontos de código, p.ex. tom de pele ou bandeira). **Defesa:** usar títulos curtos com emoji de **um ponto de código só** e texto (p.ex. "👍 Útil" / "👎 Não ajudou", bem abaixo de 20 em qualquer contagem), sem modificador de tom de pele nem seletor de variação; validar `len(title.encode("utf-16-le")) // 2 <= 20` no teste como margem (UTF-16 é a contagem mais pessimista entre as prováveis). **Confirmação obrigatória no número de teste em T35** (enviar a mensagem real e ver os botões no aparelho). Se a Meta recusar, o fallback é título só com texto ("Útil" / "Não ajudou"), sem mudar o payload.

## 4. Nome da constraint de CHECK de `kind` em `chat.outbound_messages`

**Veredito: não verificável sem banco nesta tarefa (não li `.env` nem conectei ao banco); risco já neutralizado pelo desenho.**

- `backend/migrations/003_chat.sql:33` declara o CHECK **na própria coluna** (`kind text NOT NULL CHECK (kind IN (...))`), sem nome. A convenção do PostgreSQL para esse caso é `<tabela>_<coluna>_check`, ou seja, `outbound_messages_kind_check`, mas a documentação oficial não garante esse nome.
- A migration 201 da spec 4.11 já procura a constraint pelo catálogo (`pg_constraint` + `pg_get_constraintdef LIKE '%welcome_template%'`), remove todas que encontrar e recria com nome explícito. Funciona com qualquer nome.
- **Para a T02:** o teste `test_migration_201.py` confere que, depois da 201, existe **exatamente um** CHECK de `kind` (nome `outbound_messages_kind_check`), que aceita os 3 kinds novos e recusa um inválido, e que rodar a 201 duas vezes não dá erro.

## 5. GRANTs das tabelas novas (`chat.brain_runs`, `chat.answer_ratings`) para o papel da aplicação

**Veredito: verificado no repositório; não verificável no droplet (o banco `jarvis` ainda não foi criado lá).**

O que o repositório mostra:
- Nenhuma migration de PF (001–005) nem do painel (101–104, 107) tem `GRANT` para o papel da aplicação. A única com GRANT é a `105_roles_assistant.sql`, e só para `jarvis_assistant`, por uma função idempotente `assistant.apply_grants()` que **revoga tudo** nos schemas `public/access/chat/ops/content` e concede só `SELECT` nas visões do schema `assistant`.
- Regra do projeto (task de PF, migrations 001–003): "**Não** criar roles/GRANTs (são do servidor, runbook)". `backend/scripts/dev_db_setup.sql` cria só os papéis `jarvis`, `jarvis_test`, `jarvis_assistant`.
- **Dev/teste:** o papel `jarvis` (ou `jarvis_test`) é **dono do banco**, roda as migrations e a aplicação com o mesmo papel. Por isso as tabelas são dele e **não precisam de GRANT**. É por isso que a suíte funciona sem GRANT.
- **Produção (planejado, spec PF 4.2.6 e 8.2):** dois papéis, `jarvis_owner` (DDL, roda o runner) e `jarvis_app` (DML, usado pelo `DATABASE_URL`, sem UPDATE/DELETE em `ops.audit_log`). Os GRANTs ficam no runbook `deploy/RUNBOOK.md` (PF-43, ainda `[ ]`). STATE.md: "No droplet: criar banco `jarvis`..." ainda está pendente. Então hoje **não há GRANT nenhum no droplet para conferir**.

Consequência e o que fazer:
- Em produção, tabela criada por `jarvis_owner` **não** fica acessível ao `jarvis_app` sem GRANT, e isso vale também para as **sequências** dos `bigserial` (`brain_runs_id_seq`, `answer_ratings_id_seq`): sem `USAGE` na sequência, o `INSERT` falha.
- **Recomendação para o runbook (T35 / PF-43), sem mudar migration:** logo depois de criar os papéis, como `jarvis_owner`:
  ```sql
  ALTER DEFAULT PRIVILEGES FOR ROLE jarvis_owner IN SCHEMA access, chat, ops, content
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO jarvis_app;
  ALTER DEFAULT PRIVILEGES FOR ROLE jarvis_owner IN SCHEMA access, chat, ops, content
    GRANT USAGE, SELECT ON SEQUENCES TO jarvis_app;
  ```
  (mais `GRANT USAGE ON SCHEMA ...` e a revogação de UPDATE/DELETE em `ops.audit_log` que PF já pede). Assim as tabelas 201–209 herdam as permissões. Alternativa: depois de cada `migrate` no servidor, `GRANT ... ON ALL TABLES/SEQUENCES IN SCHEMA chat TO jarvis_app`.
- **O cérebro precisa de:** `chat.brain_runs` (INSERT, SELECT para o gasto do mês), `chat.answer_ratings` (INSERT, SELECT), e as que já usa (`chat.tickets`, `chat.outbound_messages`, `chat.message_trace`, `ops.alerts`, `ops.deliveries`, `content.faq_items`).
- `jarvis_assistant` **não** deve ver as tabelas novas (não estão nas visões de `assistant`); `apply_grants()` já garante isso. Nada a fazer.
- **Defesa no código:** nenhuma específica. Um `InsufficientPrivilegeError` na transação do cérebro desfaz a resposta (o rastro fica `pending` e a recuperação marca `abandoned`), e o smoke test da T35 pega o problema no primeiro uso.

## 6. Laço de `ops.deliveries` e o e-mail do alerta criado dentro da transação do cérebro

**Veredito: verificado pela leitura do código** (`backend/app/services/alerts.py`, `deliveries.py`, `workers.py`, `main.py`); coberto em parte pelos testes existentes.

Como funciona:
1. `raise_alert(conn, ...)` (`alerts.py:128-165`) grava em `ops.alerts` e, **na mesma conexão/transação do chamador**, insere em `ops.deliveries` (`_insert_deliveries`, `alerts.py:108-125`): a linha `group` (`pending` se o grupo estiver ligado, senão `panel_only`) e, se `email=True`, a linha `email` com `status='pending'` (ou `skipped` se Resend/destinatário não estiverem configurados, `email_configured`, `alerts.py:93-98`), com `next_attempt_at = now`.
2. O laço `Workers` (`workers.py`) roda `run_cycle` a cada `SWEEP_INTERVAL_SECONDS = 15` s; ele é iniciado no lifespan (`main.py:297-298`). Cada ciclo seleciona `ops.deliveries WHERE status='pending' AND next_attempt_at <= agora` (até 50) e chama `deliveries.dispatch`, que faz o claim atômico, monta o e-mail sem PII (`build_email`) e envia, com backoff em falha transitória. Entregas travadas em `sending` há mais de 5 min voltam a `pending`.
3. Logo: um alerta criado dentro da transação 2 do cérebro **fica invisível até o commit**; depois do commit, sai no **próximo ciclo** (até ~15 s, mais o tempo do ciclo). Se a transação **desfizer**, alerta e entregas somem juntos: **nenhum e-mail sai** (correto: não existe alerta).
4. Despacho imediato: `AppProcessor._answer` (`main.py:213-219`) só despacha `outbound_id`; os `delivery_ids` do cérebro **não** são despachados na hora. Basta o laço (≤ 15 s é aceitável para alertas). Se a T-efeitos quiser envio imediato, pode devolver os ids e chamar o despachante com `((outbound_ids), (delivery_ids))`, que já aceita os dois (`main.py:144-148`). Opcional.

Testes que já cobrem: `tests/integration/test_workers.py::test_ciclo_despacha_vencido_e_ignora_nao_vencido` (varredura de entrega vencida vs. futura), `test_recuperacao_de_travados`; `tests/integration/test_deliveries.py` (envio de e-mail, backoff, `skipped`). **Falta** um teste do caminho completo "alerta do cérebro dentro da transação → commit → ciclo → e-mail". Sugestão para a tarefa de alertas do cérebro: um teste `db` que roda `raise_alert` na transação do cérebro, faz commit, roda `Workers.run_cycle()` com e-mail falso e confere `status='sent'`; e outro com rollback que confere zero linhas.

Observações (sem mudança nesta história):
- `build_email` usa o assunto fixo `"[ALTA] Jarvis: <kind>"` **para qualquer severidade** (`deliveries.py:83`). `brain_budget` é `warn` e vai chegar com "[ALTA]" no assunto. Se isso incomodar, decidir com o Caio se `brain_budget` vai com `email=False` (só painel/grupo) ou se é aceito; `deliveries.py` é de PF e não está na whitelist do cérebro.
- O e-mail só sai com `RESEND_API_KEY`, `RESEND_FROM_EMAIL` e `ALERT_RECIPIENT_EMAIL` preenchidos; senão a linha fica `skipped` e o alerta aparece só no painel.

## O que mudar na spec / tarefas (para o orquestrador)

1. Spec 4.4.1: trocar "Temperatura baixa" por "sem `temperature` (obsoleta no Gemini 3+); `thinkingLevel: low`"; manter `responseMimeType: application/json` + validação no cliente.
2. Spec 4.8 / T13: `tokens_out = candidatesTokenCount + thoughtsTokenCount`.
3. T03 / T35: preços 0,75/3,75 até 31/12/2026 e **1,50/7,50 a partir de 01/01/2027** (recomendado já usar 1,50/7,50); chave do nível pago.
4. Montagem / botões: última parte ≤ 1024 caracteres com a assinatura; títulos com emoji simples; teste real no número de teste em T35.
5. Runbook (T35/PF-43): `ALTER DEFAULT PRIVILEGES` (tabelas e sequências) de `jarvis_owner` para `jarvis_app`.
