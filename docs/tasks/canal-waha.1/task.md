---
task_id: CANAL-WAHA-1
phase: 4-implementation
status: approved
tasks_approved_by: Caio
tasks_approved_at: 2026-11-13
spec: spec/spec.md
total_tasks: 25
total_waves: 8
estimated_hours: 74.5
generated: 2026-11-13
---

# Task breakdown - CANAL-WAHA-1 (Vendedores pelo WAHA, Meta como segunda opcao)

Fonte: `spec/spec.md` (Fase 1 aprovada por Caio em 2026-11-13; Fase 2 aprovada por Caio em 2026-11-13, com WA-8 e WA-15 revistos e WA-21 a WA-25 acrescentados), `spec/requirements.json` (WA-1..WA-25), `spec/complexity.json`, `spec/research.json`. Fase 3 aprovada por Caio em 2026-11-13; Fase 4 (implementacao) em andamento.

## Convencoes

- **IDs:** `WA-T01..WA-T25` (prefixo `WA-T` para nao colidir com os criterios `WA-n`). Duracao 1-4 h, incluindo os testes da propria tarefa. Commit: `type(canal): CANAL-WAHA-1.Txx - descricao`. Checkbox: `[ ]` pendente, `[~]` em andamento, `[x]` pronta; `[P]` = paralela na onda.
- **Caminhos** relativos a raiz do repo. `B/` = `backend/`, `BT/` = `backend/tests/`, `F/` = `frontend/`. Todo comando roda a partir de `backend\` (CLAUDE.md, Verificacao).
- **Whitelist:** a Fase 4 so toca os arquivos da coluna "Arquivos" da tarefa. Testes de outra tarefa so mudam quando listados ("se necessario" = apenas se o teste existente quebrar por causa da mudanca, sem mudar o comportamento que ele afirma).
- **Verificacao padrao** de toda tarefa com codigo: o `pytest` indicado + `.venv\Scripts\python -m ruff check app tests` + `.venv\Scripts\python -m mypy app`. Tarefas de migration somam `migrate --check` contra o banco de teste. A suite inteira roda no fim de cada onda e em T23. Tarefas que tocam a busca do cerebro somam `.venv\Scripts\python -m app.services.brain.eval --mode palavras` (licao L-004).
- **Compatibilidade:** codigo 3.12 / PostgreSQL 16. Imports absolutos. Unica dependencia nova: `tzdata` (dev), em T01.
- **Meta intacta (WA-3):** todo desvio de comportamento depende de `channel.capabilities` ou de `CHANNEL_PROVIDER`; nenhuma assercao de teste existente da Meta muda.
- **Privacidade:** fixtures sem telefone real (numeros `5500...`); nenhum `.env` lido ou escrito pelo agente; nenhum log com telefone, `@lid`, texto, chave ou HMAC.
- **Arquivos compartilhados** (`config.py`, `main.py`, `values.py`, `optin.py`, `outbox.py`, `workers.py`, `answering.py`, `provider.py`, `webhook_waha.py`, `channel.py`): nunca em duas tarefas da mesma onda.

## Desvios do design (escopos disjuntos; mesmo comportamento)

1. **KPI "Avaliados como uteis":** ja mostra "sem dados" sem avaliacoes (`metrics_read.useful_label`, CB-26). WA-14 no painel vira so teste de regressao (T13), sem codigo de painel.
2. **`optin.py` em duas tarefas** (T12 opt-in sem modelos; T19 enquete depois do aceite) e **`workers.py` em duas** (T15 ordem das partes; T16 laco da sessao), em ondas diferentes.
3. **`webhook_waha.py` em duas tarefas** (T10 rota e mensagens; T16 evento `session.status`), em ondas diferentes.
4. **Painel em duas tarefas** (T17 link `wa.me`, "Copiar link" e faixa de canal; T18 "Numero oculto"), por arquivos disjuntos.

---

## W0 - Preparacao (higiene, configuracao, dados, fixtures)

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| WA-T01 `[ ]` | Testes sem `.env` do desenvolvedor; `tzdata`; teste com data fixa | 3 | - | `B/requirements-dev.txt`, `BT/conftest.py`, `BT/panel/integration/test_assistant_service.py`, `BT/unit/test_env_smoke.py` (se necessario) | WA-3 (base) | Suite inteira com um `.env` falso que tem `PRODUCTION=false` e sem `WHATSAPP_*`: as 72 falhas de ambiente de 2026-11-13 somem; `test_resposta_registrada_com_ferramentas` passa em qualquer data; `ZoneInfo("America/Sao_Paulo")` funciona no Windows |
| WA-T02 `[ ]` | Configuracao do canal | 3 | T01 | `B/app/config.py`, `B/configuracao-modelo.txt`, `BT/unit/test_config.py` (se necessario), `BT/unit/test_config_channel.py` | WA-1, WA-20 (base) | `pytest tests/unit/test_config_channel.py tests/unit/test_config.py -q` (`CHANNEL_PROVIDER` padrao `waha`; com `waha` exige `JARVIS_WAHA_API_KEY`, `JARVIS_WAHA_HMAC_KEY`, `JARVIS_WHATSAPP_NUMBER` e nao exige `WHATSAPP_*`; com `meta` o contrario; `JARVIS_WAHA_BASE_URL` so localhost; numero em E.164; padroes da spec 9.5; `BRAND_SESSION_MINUTES >= 0`; segredos como `SecretStr`) |
| WA-T03 `[ ]` `[P]` | Migration 301 (marca da conversa e `brand_poll`) | 2.5 | - | `B/migrations/301_brand_session.sql`, `BT/integration/test_migration_301.py` | WA-15, WA-21 (base) | `pytest tests/integration/test_migration_301.py -q` (colunas `active_brand` com CHECK nas 5 marcas e `active_brand_at`; kind `brand_poll` aceito e kind invalido recusado; CHECK achado pelo catalogo como na 201; idempotente; `migrate --check`) |
| WA-T04 `[ ]` `[P]` | Fixtures dos eventos do WAHA | 1.5 | - | `BT/fixtures/waha/` (novos `.json`: texto, midia, grupo, `fromMe`, `status@broadcast`, `@lid`, reacao, ack -1/1/2/3, `poll.vote`, `session.status`), `BT/fixtures/waha/README.md` | WA-6, WA-7 (base) | Sem pytest proprio; o README cita a fonte (documentacao do WAHA, 2026-11-13) e marca as fixtures a trocar pela captura real (T25) |

## W1 - Borda: contratos e modulos puros

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| WA-T05 `[ ]` | Capacidades do canal e `send_poll`; Meta com botoes ou texto | 3 | T02 | `B/app/services/channel.py`, `B/app/services/whatsapp.py`, `BT/fakes.py`, `BT/unit/test_whatsapp_poll.py` | WA-25, WA-3 | `pytest tests/unit/test_whatsapp_poll.py tests/integration/test_outbox_dispatch.py -q` (`ChannelCapabilities` Meta = botoes e modelos; `FakeChannel` configuravel; `send_poll` na Meta: ate 3 opcoes = botoes de resposta com payload `BRAND:<slug>`, 4+ = texto com a lista; nada mais muda na Meta) |
| WA-T06 `[ ]` `[P]` | HMAC SHA-512 do WAHA | 1.5 | - | `B/app/core/security.py`, `BT/unit/test_security.py` | WA-4 | `pytest tests/unit/test_security.py -q` (assinatura correta aceita; corpo alterado, chave errada, cabecalho ausente, hex invalido e algoritmo diferente de `sha512` recusados; comparacao em tempo constante) |
| WA-T07 `[ ]` `[P]` | Parser dos eventos do WAHA | 4 | T04 | `B/app/services/waha_inbound.py`, `BT/unit/test_waha_inbound.py` | WA-5, WA-6, WA-7, WA-18 (ack), WA-21 (voto) | `pytest tests/unit/test_waha_inbound.py -q` (sessao errada = `Ignored(wrong_recipient)`; `fromMe`, grupo, `status@broadcast`, canal e newsletter = `Ignored`; texto e midia sem download; reacao; ack -1/1/2/3/4 para `InboundStatus`, 0 ignorado; `poll.vote` vira texto com o nome da opcao e id do voto; `session.status`; payload malformado nunca levanta; puro, sem I/O) |
| WA-T08 `[ ]` | Cliente `WahaChannel` | 4 | T05 | `B/app/services/waha_channel.py`, `BT/unit/test_waha_channel.py` | WA-17, WA-18, WA-20 | `pytest tests/unit/test_waha_channel.py -q` (com `httpx.MockTransport`: `sendSeen` → `startTyping` → espera `clamp(len/60, 1, WAHA_TYPING_MAX_SECONDS)` com relogio falso → `stopTyping` → `sendText`; falha em visto/digitando nao impede o envio; `sendPoll` escolha unica; tabela de classificacao da spec 9.3, incluindo 463/475; extrator de id tolerante; `send_template` = `unsupported`; `send_reply_buttons` envia so o corpo; chave no cabecalho; logs sem numero nem texto) |
| WA-T09 `[ ]` `[P]` | Resolucao de `@lid` | 2 | - | `B/app/services/waha_lid.py`, `BT/unit/test_waha_lid.py` | WA-8 | `pytest tests/unit/test_waha_lid.py -q` (resolve para E.164; cache com teto e validade; erro ou 404 = nao resolvido, sem levantar; log sem o lid) |

## W2 - Webhook e montagem do app

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| WA-T10 `[ ]` | Rota `POST /webhook/waha` | 4 | T06, T07, T09 | `B/app/api/webhook_waha.py`, `BT/integration/test_webhook_waha.py`, `BT/security/test_waha_order.py` | WA-4, WA-5, WA-8, WA-9 | `pytest tests/integration/test_webhook_waha.py tests/security/test_waha_order.py -q` (ordem IP → tamanho → HMAC → JSON → sessao; espiao no pool: zero `acquire` antes da assinatura; 429/413/403/400 com contadores; `@lid` resolvido vira telefone, nao resolvido vira `Sender(bsuid=lid)`; reentrega sem efeito repetido; `TransientDbError` = 503; despacho depois da resposta) |
| WA-T11 `[ ]` | Fabrica de canal e montagem no `main.py` | 3 | T08, T10 | `B/app/services/channel_factory.py`, `B/app/main.py`, `BT/integration/test_channel_selection.py`, `BT/integration/test_health.py` (se necessario), `BT/integration/test_app_e2e.py` (se necessario) | WA-1, WA-2 | `pytest tests/integration/test_channel_selection.py tests/integration/test_health.py tests/integration/test_app_e2e.py -q` (com `waha`: `/webhook/waha` ativo e `/webhook/whatsapp` 404; com `meta`: o contrario; canal certo no despacho e nos `Workers`; clientes fechados no lifespan) |

## W3 - Dominio: opt-in, respostas, saida

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| WA-T12 `[ ]` | Opt-in sem modelos nem botoes | 4 | T05, T11 | `B/app/services/access.py`, `B/app/services/optin.py`, `BT/unit/test_access.py`, `BT/integration/test_optin_waha.py`, `BT/integration/test_optin_service.py` (se necessario) | WA-11 (servico), WA-12, WA-13 | `pytest tests/unit/test_access.py tests/integration/test_optin_waha.py tests/integration/test_optin.py tests/integration/test_optin_service.py -q` (sem modelos: cadastro abre aviso e nao enfileira nada; 1a mensagem recebe `TEXT_OPTIN_NOTICE`; "ACEITO"/"aceito"/"sim"/"1" e "nao"/"NÃO"/"recuso"/"2" so valem com aviso ja mostrado; "sim" na 1a mensagem = aviso, nao aceite; lembrete 1 por intervalo; com Meta, so botao vale, como hoje) |
| WA-T13 `[ ]` `[P]` | Respostas sem avaliacao no WAHA | 2 | T05 | `B/app/services/answering.py`, `BT/integration/test_answering_waha.py`, `BT/brain/integration/test_kpi_uteis.py` (se necessario) | WA-14 | `pytest tests/integration/test_answering_waha.py tests/integration/test_answering.py tests/brain/integration/test_answering_effects.py -q` (sem botoes: todas as partes `brain_answer`, nenhum `reply_buttons`; reacao de ativo = so rastro, fora do limite; KPI mostra "sem dados"; com Meta, ultima parte segue `brain_answer_rate`) |
| WA-T14 `[ ]` `[P]` | Outbox: so responde, conexao solta no envio, `brand_poll` | 4 | T03, T05 | `B/app/services/outbox.py`, `B/app/core/values.py`, `BT/integration/test_outbox_waha.py`, `BT/integration/test_outbox_dispatch.py` (se necessario), `BT/integration/test_outbox_recovery.py` (se necessario) | WA-16, WA-17 (conexao), WA-15 (envio da enquete) | `pytest tests/integration/test_outbox_waha.py tests/integration/test_outbox_dispatch.py tests/integration/test_outbox_recovery.py tests/integration/test_pool_concurrency.py -q` (`OUTBOUND_REPLY_ONLY`: sem mensagem do usuario em 24 h = `discarded`/`no_inbound_window` sem chamar o canal; conexao devolvida durante o envio (espiao conta conexoes em uso); claim condicional mantido; `brand_poll` valida opcoes e chama `send_poll`; `OutboundKind.BRAND_POLL`) |
| WA-T15 `[ ]` `[P]` | Varredura em ordem das partes | 2.5 | T03 | `B/app/services/workers.py`, `BT/integration/test_outbox_order.py`, `BT/integration/test_workers.py` (se necessario) | WA-17 (ordem) | `pytest tests/integration/test_outbox_order.py tests/integration/test_workers.py -q` (parte 2 nao e pega com a parte 1 `pending`/`sending`; depois que a 1 sai, a 2 sai; partes de respostas diferentes nao se bloqueiam; enquete depois da confirmacao sai em ordem) |

## W4 - Operacao e painel

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| WA-T16 `[ ]` | Monitor da sessao e alertas de canal | 4 | T10, T11, T15 | `B/app/services/session_monitor.py`, `B/app/services/workers.py`, `B/app/api/webhook_waha.py`, `BT/integration/test_session_monitor.py` | WA-18 (alerta 463/475), WA-19 | `pytest tests/integration/test_session_monitor.py tests/integration/test_webhook_waha.py tests/integration/test_workers.py -q` (`session.status` diferente de `WORKING` e checagem de 60 s abrem `channel_down` uma vez (dedup em transacao); volta a `WORKING` limpa o estado; 463/475 abrem `channel_restricted`; `/health` informa `channel`; e-mail pelo fluxo de alertas existente) |
| WA-T17 `[ ]` `[P]` | Painel: link `wa.me`, "Copiar link" e faixa de canal | 3.5 | T11, T12 | `B/app/panel/api/pessoas_ficha.py`, `F/templates/_pessoa_ficha.html`, `F/static/js/pessoa_ficha.js`, `B/app/panel/api/system.py`, `F/templates/_banner_estado.html`, `B/app/cli.py`, `BT/panel/integration/test_waha_panel.py`, `BT/integration/test_cli_resend_swap.py` (se necessario), `BT/panel/integration/test_pessoas_ficha.py` (se necessario) | WA-11 (painel), WA-19 (faixa) | `pytest tests/panel/integration/test_waha_panel.py tests/panel/integration/test_pessoas_ficha.py tests/integration/test_cli_resend_swap.py -q` (com `waha`: ficha mostra `https://wa.me/5585994102873?text=...` e "Copiar link" no lugar de "Reenviar boas-vindas"; CLI de reenvio imprime o link; faixa "Jarvis desconectado do WhatsApp" quando `channel_down`; com `meta`, tela igual a de hoje; CSP sem inline) |
| WA-T18 `[ ]` `[P]` | Painel: "Numero oculto" em Pedindo acesso | 3 | T10 | `B/app/panel/services/people_read.py`, `F/templates/_pessoas_pedidos.html`, `B/app/panel/api/pessoas_cadastro.py`, `F/templates/_pessoa_form.html`, `BT/panel/integration/test_numero_oculto.py` | WA-8 (painel) | `pytest tests/panel/integration/test_numero_oculto.py tests/panel/integration/test_pessoas_pedidos.py tests/panel/integration/test_pessoas_cadastro.py -q` (tentativa so com identificador aparece como "Numero oculto" com data e contagem; formulario de autorizacao mostra o aviso "confirme com a pessoa"; salvar grava o `@lid` em `access.users.bsuid` na mesma transacao e liga a tentativa; mensagem seguinte com o mesmo `@lid` e reconhecida como o vendedor; `@lid` ja usado por outro cadastro = erro claro) |

## W5 - Marca da conversa

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| WA-T19 `[ ]` | Servico da marca da conversa | 3 | T03, T14 | `B/app/services/brand_session.py`, `BT/integration/test_brand_session.py` | WA-21, WA-23 (base) | `pytest tests/integration/test_brand_session.py -q` (gravar, ler valida, renovar a cada mensagem, vencer depois de `BRAND_SESSION_MINUTES`; `0` desliga; marca fora do cadastro recusada; reconhece "so o nome de uma marca" e "trocar marca" (normalizado); voto so vale para a ultima `brand_poll`) |
| WA-T20 `[ ]` | Enquete depois do aceite | 2 | T12, T19 | `B/app/services/optin.py`, `BT/integration/test_optin_brand_poll.py` | WA-15 | `pytest tests/integration/test_optin_brand_poll.py tests/integration/test_optin_waha.py tests/integration/test_optin.py -q` (aceite com 2+ marcas enfileira confirmacao e `brand_poll` na mesma transacao, com as marcas do cadastro; 1 marca fixa a marca sem enquete; recusa nao manda enquete) |

## W6 - Cerebro com marca da conversa

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| WA-T21 `[ ]` | Provedor: busca na marca, troca, enquete e pergunta guardada | 4 | T13, T19 | `B/app/services/brain/provider.py`, `B/app/services/answering.py`, `BT/brain/integration/test_provider_brand_session.py`, `BT/brain/integration/test_provider_palavras.py` (se necessario) | WA-21, WA-22, WA-23 | `pytest tests/brain/integration/test_provider_brand_session.py tests/brain/integration/test_provider_palavras.py tests/brain/integration/test_memory.py -q` + `eval --mode palavras` (escolha sem pendencia = `TEXT_BRAND_SELECTED`; com pendencia responde a pergunta guardada; pergunta sem marca busca so na marca + "todas"; marca do cadastro citada troca; fora do cadastro = pedido de acesso; vencida = `brand_poll` no lugar de `TEXT_BRAIN_ASK_BRAND`; "trocar marca" = enquete; pausa continua sem custo) |
| WA-T22 `[ ]` | Gate: casos de marca da conversa | 3 | T21 | `B/app/services/brain/eval.py`, `BT/brain/eval/cases/marca_conversa.jsonl`, `BT/brain/unit/test_eval_cases_schema.py` (se necessario), `BT/brain/unit/test_eval_runner.py` (se necessario) | WA-24 | `eval --mode palavras` APROVADO com o arquivo novo (marca valida, troca, vencida, voto respondendo a pergunta guardada, marca unica); `pytest tests/brain/unit -q` |

## W7 - Fechamento

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| WA-T23 `[ ]` | Regressao e privacidade | 3 | T16, T17, T18, T20, T22 | `BT/integration/test_pipeline_waha.py`, `BT/security/test_waha_privacy.py` | WA-3, WA-10, WA-20 | `pytest tests/integration/test_pipeline_waha.py tests/security/test_waha_privacy.py -q` (pipeline de porta-fechada com mensagens de origem WAHA: lista positiva, 60/h, pausa, rastro, tickets, midia, "sair"; varredura dos logs da suite atras de numeros, `@lid` e segredos) + **suite inteira** + ruff + mypy + `migrate --check` + `eval --mode palavras` |
| WA-T24 `[ ]` | Documentacao | 2 | T23 | `docs/STATE.md`, `B/DEV_SETUP.md`, `docs/tasks/canal-waha.1/go-live.md`, `docs/tasks/canal-waha.1/spec/spec.md` (status), `docs/stories/canal-waha.1.md` (status) | - | Revisao: STATE com a decisao e o andamento; DEV_SETUP com Python 3.12, PostgreSQL 16 em ZIP sem admin e o WAHA local; `go-live.md` com o checklist da spec 9.10 |
| WA-T25 `[ ]` | Go-live assistido (manual, depende do Caio) | 3 | T24 | `BT/fixtures/waha/` (troca pelas capturas reais, se diferentes), `docs/tasks/canal-waha.1/go-live.md` (registro) | WA-17 (p95), WA-19, todos (manual) | Checklist 9.10 com confirmacao do Caio a cada passo no servidor; payloads reais conferidos contra as fixtures (R1, R2, R3, R5); `eval --mode ia` (com a chave) >= 98% factual e 100% preco; teste no celular: link, aviso, ACEITO, enquete, pergunta, troca de marca, midia, "sair"; aprovacao juridica do aviso (D6) antes do link ir aos vendedores |

---

## Verificacao das ondas (arquivos compartilhados)

| Onda | Tarefas | Arquivos compartilhados tocados (um dono por onda) |
|---|---|---|
| W0 | T01, T02, T03, T04 | `config.py` (T02), `conftest.py` (T01) |
| W1 | T05, T06, T07, T08, T09 | `channel.py` (T05) |
| W2 | T10, T11 | `webhook_waha.py` (T10), `main.py` (T11) |
| W3 | T12, T13, T14, T15 | `optin.py` (T12), `answering.py` (T13), `outbox.py` e `values.py` (T14), `workers.py` (T15) |
| W4 | T16, T17, T18 | `workers.py` e `webhook_waha.py` (T16) |
| W5 | T19, T20 | `optin.py` (T20) |
| W6 | T21, T22 | `provider.py` e `answering.py` (T21) |
| W7 | T23, T24, T25 | - |

T02 depende de T01 na mesma onda (serializada). T08 depende de T05 na W1 (serializada).

## Cobertura dos criterios

| AC | Tarefas |
|---|---|
| WA-1 | T02, T11 |
| WA-2 | T11 |
| WA-3 | T01, T05, T23 (suite inteira com Meta) |
| WA-4 | T06, T10 |
| WA-5 | T07, T10 |
| WA-6, WA-7 | T07 |
| WA-8 | T09, T10, T18 |
| WA-9 | T10 |
| WA-10 | T23 |
| WA-11 | T12, T17 |
| WA-12, WA-13 | T12 |
| WA-14 | T13 |
| WA-15 | T14, T20 |
| WA-16 | T14 |
| WA-17 | T08, T14, T15, T25 |
| WA-18 | T07, T08, T16 |
| WA-19 | T16, T17, T25 |
| WA-20 | T02, T08, T23 |
| WA-21 | T07, T19, T21 |
| WA-22 | T21 |
| WA-23 | T19, T21 |
| WA-24 | T22, T25 |
| WA-25 | T05 |
