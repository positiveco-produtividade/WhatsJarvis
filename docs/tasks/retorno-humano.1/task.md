---
task_id: RETORNO-HUMANO-1
phase: 4-implementation
status: approved
tasks_approved_by: Caio
tasks_approved_at: 2026-11-17
spec: spec/spec.md
total_tasks: 14
total_waves: 6
estimated_hours: 50
generated: 2026-11-17
---

# Task breakdown - RETORNO-HUMANO-1 (A equipe responde os escalados pelo Inbox e alimenta a FAQ)

Fonte: `spec/spec.md` (Fase 1 e Fase 2 aprovadas por Caio em 2026-11-17), `spec/requirements.json` (RH-1..RH-21), `spec/complexity.json`, `spec/research.json`, ADR de 2026-11-17 em `docs/architecture/overview.md`. Fase 3 aprovada por Caio em 2026-11-17; Fase 4 (implementação) em andamento.

## Convencoes

- **IDs:** `RH-T01..RH-T14` (prefixo `RH-T` para nao colidir com os criterios `RH-n`). Duracao 1-7 h, incluindo os testes da propria tarefa. Commit: `type(retorno): RETORNO-HUMANO-1.Txx - descricao`, **so com OK explicito do Caio**. Checkbox: `[ ]` pendente, `[~]` em andamento, `[x]` pronta; `[P]` = paralela na onda.
- **Caminhos** relativos a raiz do repo. `B/` = `backend/`, `BT/` = `backend/tests/`, `F/` = `frontend/`. Todo comando roda a partir de `backend\` (CLAUDE.md, Verificacao).
- **Whitelist:** a Fase 4 so toca os arquivos da coluna "Arquivos" da tarefa. "(se necessario)" = apenas se um teste existente quebrar por causa da mudanca, sem mudar o comportamento que ele afirma.
- **Verificacao padrao** de toda tarefa com codigo: o `pytest` indicado + `.venv\Scripts\python -m ruff check app tests` + `.venv\Scripts\python -m mypy app`. Migration soma `migrate --check` contra o banco de teste. Tarefa que toca `brain/` soma `.venv\Scripts\python -m app.services.brain.eval --mode palavras` (deve imprimir APROVADO). Tarefa que muda template soma o build do Tailwind (CLAUDE.md) e `tests/panel/unit/test_css_in_sync.py`. A suite inteira roda no fim de cada onda e em T14.
- **Compatibilidade:** codigo 3.12 / PostgreSQL 16. Imports absolutos. **Nenhuma dependencia nova.**
- **Privacidade:** fixtures sem telefone real (`5500...`); nenhum `.env` lido ou escrito pelo agente; nenhum log com telefone, nome, texto de mensagem ou de resposta.
- **Arquivos compartilhados** (`config.py`, `values.py`, `outbox.py`, `workers.py`, `pipeline.py`, `deliveries.py`, `F/static/css/tailwind.css`): nunca em duas tarefas da mesma onda.
- **AUTO-1 intocado:** nenhuma tarefa le, altera ou depende de arquivo, configuracao ou sessao do `agente-instagram-direct-n8n` (RH-21).

## Desvios do design (escopos disjuntos; mesmo comportamento)

1. **Rotas de sugestoes** ficam no proprio `relatorios.py` (o painel descobre routers sozinho, `app/panel/routes.py`); nenhuma tarefa toca `routes.py`.
2. **Ponto unico de envio** (`tickets_reply.py`) nasce em T09 com o drawer; o Inbox (T12) so reaproveita a rota.
3. **Intervalo do grupo** (RH-3) fica em `deliveries.py` (T07), nao em `workers.py`, para nao dividir `workers.py` entre T04 e T07.
4. **Descoberto na Fase 4 (T02, 2026-11-17):** remover `WAHA_BASE_URL/WAHA_API_KEY/WAHA_SESSION` quebraria `waha.py` entre as ondas. T02 passa a incluir: `B/app/services/waha.py` (envio ao grupo pelo `JARVIS_WAHA_*`), `BT/unit/test_notifications_waha.py`, a linha `HUMAN_REPLY` de `REQUIRED_STATUS` em `B/app/services/outbox.py` (o teste exige o mapa completo) e o ajuste mecanico `waha_api_key` -> `jarvis_waha_api_key` em `BT/integration/test_app_e2e.py`, `test_email_privacy.py`, `test_replies_media.py`, `test_deliveries.py`, `test_outbox_enqueue.py`, `BT/security/test_zero_cost.py`, `BT/panel/integration/test_email_alerts.py`, `test_team_group_flag.py` e `BT/unit/test_access.py` (`HUMAN_REPLY` sem texto fixo). Nenhuma assercao muda de sentido.
5. **`PANEL_BASE_URL` nao e obrigatorio** (T02): o link do aviso usa `Settings.panel_link_base` = `PANEL_BASE_URL` ou `https://PANEL_PUBLIC_HOST` (ja configurado). Evita nova variavel obrigatoria e quebra de testes que ligam o grupo.

---

## W0 - Dados e configuracao

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| RH-T01 `[x]` `[P]` | Migration 401 (resposta humana, acks, sugestoes, origem `sugestao`) | 3 | - | `B/migrations/401_retorno_humano.sql`, `BT/integration/test_migration_401.py`, `BT/integration/test_migration_301.py` e `BT/brain/integration/test_migration_201.py` (se necessario: reaplicar migrations posteriores ao recriar o CHECK de `kind`) | RH-7, RH-11, RH-15, RH-16, RH-18 (base) | `pytest tests/integration/test_migration_401.py -q` (kind `human_reply` aceito e invalido recusado; colunas `ticket_id` (FK `ON DELETE SET NULL`), `author`, `author_email`, `client_token` UNIQUE, `delivered_at`, `read_at`; `content.faq_suggestions` com CHECK de status e `ticket_number` UNIQUE; `faq_items.origin` aceita `sugestao`; CHECKs achados pelo catalogo como na 201/301; idempotente; apagar ticket nao quebra FK; `migrate --check`) |
| RH-T02 `[x]` `[P]` | Valores e configuracao | 3 | - | `B/app/core/values.py`, `B/app/config.py`, `B/configuracao-modelo.txt`, `BT/unit/test_values.py`, `BT/unit/test_config.py`, `BT/unit/test_config_channel.py` (se necessario) | RH-9, RH-21 (base) | `pytest tests/unit/test_values.py tests/unit/test_config.py tests/unit/test_config_channel.py -q` (`OutboundKind`/`ReplyKind.HUMAN_REPLY`; `TEXT_HUMAN_REPLY_HEADER(_NO_NAME)`, `TEXT_TEAM_GROUP_TICKET`, `TEAM_GROUP_MIN_INTERVAL_SECONDS=3` (>= 0), `HUMAN_REPLY_MAX_CHARS=4000`, `HUMAN_REPLY_IGNORE_WINDOW=true`; grupo ligado exige `TEAM_GROUP_CHAT_ID` `@g.us`, `JARVIS_WAHA_API_KEY` e `PANEL_BASE_URL` https; `WAHA_BASE_URL/WAHA_API_KEY/WAHA_SESSION` removidos e ignorados se presentes) |

## W1 - Saida, acks e aviso no grupo

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| RH-T03 `[ ]` | Outbox e politica para `human_reply` | 3 | T01, T02 | `B/app/services/outbox.py`, `B/app/services/reply_policy.py`, `BT/integration/test_outbox_enqueue.py`, `BT/integration/test_outbox_dispatch.py`, `BT/integration/test_reply_policy.py` | RH-10, RH-12, RH-13 | `pytest tests/integration/test_outbox_enqueue.py tests/integration/test_outbox_dispatch.py tests/integration/test_reply_policy.py -q` (`enqueue` grava `ticket_id`, `author`, `author_email`, `client_token`; `REQUIRED_STATUS[HUMAN_REPLY]=ACTIVE`; com `reply_only`, `human_reply` sai sem mensagem nas ultimas 24 h e os outros kinds continuam descartados (`no_inbound_window`); `HUMAN_REPLY_IGNORE_WINDOW=false` volta a descartar; pausa e marca desligada nao bloqueiam `human_reply`; vendedor revogado = descartado como hoje) |
| RH-T04 `[ ]` `[P]` | Ordem das partes por ticket | 2 | T01 | `B/app/services/workers.py`, `BT/integration/test_workers.py` | RH-9 | `pytest tests/integration/test_workers.py -q` (partes `human_reply` do mesmo `ticket_id` saem em ordem de `id`, cada uma so depois da anterior sair; **regressao:** ordem por `inbound_trace_id` das respostas do cerebro inalterada; saidas sem trace nem ticket nao esperam nada) |
| RH-T05 `[ ]` `[P]` | Acks entregue e lida | 2 | T01 | `B/app/services/pipeline.py`, `BT/integration/test_outbound_acks.py`, `BT/integration/test_pipeline_core.py` (se necessario) | RH-15 | `pytest tests/integration/test_outbound_acks.py tests/integration/test_waha_flow.py -q` (ack 2 grava `delivered_at`, 3/4 grava `read_at`, so se `NULL`; ack fora de ordem nao apaga; -1 continua `failed` como hoje; ack de id desconhecido sem efeito; nenhum log com id do WhatsApp completo) |
| RH-T06 `[ ]` `[P]` | Empacotador de partes publico | 1 | - | `B/app/services/brain/assembly.py`, `BT/brain/unit/test_assembly.py` | RH-9 (base) | `pytest tests/brain/unit/test_assembly.py -q` + `eval --mode palavras` APROVADO (`pack_parts(text, limit=700)` exposto sem mudar o resultado de `assemble`) |
| RH-T07 `[ ]` `[P]` | Aviso no grupo: formato, link, intervalo, WAHA do Jarvis | 5 | T02 | `B/app/services/deliveries.py`, `B/app/services/waha.py`, `BT/integration/test_deliveries.py`, `BT/unit/test_notifications_waha.py`, `BT/unit/test_group_ticket_text.py`, `BT/integration/test_group_notice.py` | RH-1, RH-2, RH-3, RH-4, RH-21 | `pytest tests/unit/test_group_ticket_text.py tests/unit/test_notifications_waha.py tests/integration/test_deliveries.py tests/integration/test_group_notice.py -q` (texto RH-2 para pergunta e midia: J-n, marca, motivo traduzido, primeiro nome + UF, pergunta entre aspas cortada em 1000, `[audio]`/`[foto]`, link `PANEL_BASE_URL/dashboard/inbox/ticket/J-n`, **sem telefone**; todos os motivos de escalado; `send_group_text` usa `JARVIS_WAHA_*` e so `TEAM_GROUP_CHAT_ID`; dois avisos seguidos respeitam o intervalo; grupo desligado = `panel_only`; mensagem de grupo recebida continua descartada) |

## W2 - Dominio

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| RH-T08 `[ ]` | `human_reply.send_reply` e sugestao | 5 | T03, T04, T06 | `B/app/services/human_reply.py`, `BT/unit/test_human_reply_header.py`, `BT/integration/test_human_reply.py` | RH-7, RH-9, RH-11, RH-12, RH-13, RH-16, RH-20 (logs) | `pytest tests/unit/test_human_reply_header.py tests/integration/test_human_reply.py -q` (cabecalho com e sem nome; texto vazio ou > 4000 = `invalid`; ticket inexistente = `not_found`; vendedor nao `active` = `blocked` sem gravar nada; envio grava partes, conclui o ticket `awaiting` com autor e data e audita sem o texto, tudo numa transacao (falha no meio desfaz); mesmo `client_token` = `duplicate`; segunda resposta = complemento sem mudar autor/data; sugestao criada so para `question` com mensagem, complemento concatena, midia nao gera; nao importa `app.panel`) |

## W3 - Painel: drawer, sugestoes, LGPD

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| RH-T09 `[ ]` `[P]` | Rota de envio e caixa no drawer | 4 | T08 | `B/app/panel/api/tickets_reply.py`, `B/app/panel/api/ticket_drawer.py`, `F/templates/_ticket_drawer.html`, `BT/panel/integration/test_ticket_drawer_reply.py` | RH-6, RH-7, RH-8, RH-12 | `pytest tests/panel/integration/test_ticket_drawer_reply.py tests/panel/integration -k drawer -q` (`POST /dashboard/tickets/J-n/responder` exige admin e CSRF; envia e devolve o drawer com toast e `ticketChanged`; bloqueado mostra o motivo e caixa desabilitada; `token` repetido nao reenvia; Concluir sem texto inalterado) |
| RH-T10 `[ ]` `[P]` | Sugestoes para a FAQ em Relatorios | 7 | T01, T08 | `B/app/panel/services/faq_suggestions.py`, `B/app/panel/api/relatorios.py`, `F/templates/relatorios.html`, `B/app/panel/api/faq_form.py`, `B/app/panel/services/faq_admin.py`, `F/templates/_faq_form.html`, `F/static/css/tailwind.css`, `BT/panel/integration/test_faq_suggestions.py`, `BT/panel/integration/test_faq_form.py`, `BT/panel/integration/test_faq_admin.py`, `BT/panel/integration/test_relatorios_page.py` | RH-17, RH-18, RH-19 | `pytest tests/panel/integration/test_faq_suggestions.py tests/panel/integration/test_faq_form.py tests/panel/integration/test_faq_admin.py tests/panel/integration/test_relatorios_page.py -q` (card com pendentes mais recentes, total, filtro Pendentes/Todas, marca em foco; `?sugestao=id` pre-preenche pergunta, resposta, marca e categoria; salvar cria item `origin='sugestao'`, `origin_ref=J-n` e aprova a sugestao na mesma transacao com auditoria; sugestao ja decidida = aviso, sem criar; cancelar nao muda; descartar com autor e auditoria; `?ticket=` antigo continua igual) + build do Tailwind |
| RH-T11 `[ ]` `[P]` | LGPD e retencao das sugestoes | 3 | T01 | `B/app/panel/services/lgpd.py`, `B/app/panel/services/retention.py`, `BT/panel/integration/test_lgpd.py`, `BT/panel/integration/test_retention.py` | RH-20 | `pytest tests/panel/integration/test_lgpd.py tests/panel/integration/test_retention.py -q` (exclusao apaga sugestoes `pendente`/`descartada` do vendedor e limpa `question`/`answer` das aprovadas, mantendo o item da FAQ; `author_email` das saidas humanas preservado so como auditoria; retencao inclui `content.faq_suggestions` na ordem de FK) |

## W4 - Inbox

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| RH-T12 `[ ]` | Inbox: link do aviso, caixa de resposta, linha do tempo | 7 | T05, T09 | `B/app/panel/api/inbox.py`, `B/app/panel/api/inbox_chat.py`, `B/app/panel/services/inbox_read.py`, `F/templates/inbox.html`, `F/templates/_inbox_chat.html`, `F/static/js/inbox_chat.js`, `F/static/css/tailwind.css`, `BT/panel/integration/test_inbox_deeplink.py`, `BT/panel/integration/test_inbox_reply.py`, `BT/panel/integration/test_inbox_read.py`, `BT/panel/integration/test_inbox_chat.py` (se necessario) | RH-5, RH-6, RH-10, RH-14, RH-15 | `pytest tests/panel/integration/test_inbox_deeplink.py tests/panel/integration/test_inbox_reply.py tests/panel/integration/test_inbox_read.py tests/panel/integration/test_inbox_chat.py -q` (`GET /dashboard/inbox/ticket/J-n` = 302 para `/dashboard/inbox?u=&t=`; sem sessao vai ao IAM com esse `next`; 404 se nao existe; fragmento marca `data-ticket` e caixa ligada ao ticket; "vendedor escreveu ha X"; `human_reply` aparece como "Equipe · autor" com situacao na fila/enviada/entregue/lida/falhou e sai da parte "Bot"; Concluir sem texto mantem a nota antiga; layout de celular sem rolagem lateral) + build do Tailwind |

## W5 - Fechamento

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| RH-T13 `[ ]` | Seguranca, logs e mapa de criterios | 2 | T09, T10, T12 | `BT/panel/security/test_route_sweep.py`, `BT/panel/security/test_pii_in_logs.py`, `BT/panel/AC_MAP.md`, `BT/panel/test_ac_coverage.py` (se necessario) | RH-20, todos (mapa) | `pytest tests/panel/security -q` (rotas novas exigem admin e CSRF no POST; eventos `human_reply_*` e `faq_suggestion_*` sem texto, nome nem telefone; mapa RH-n -> teste completo) |
| RH-T14 `[ ]` | Verificacao final e documentacao | 3 | todas | `docs/tasks/retorno-humano.1/task.md`, `docs/tasks/retorno-humano.1/spec/spec.md`, `docs/stories/retorno-humano.1.md`, `docs/STATE.md`, `B/configuracao-modelo.txt` (se necessario) | todos | Suite inteira (`pytest`), `mypy app`, `ruff check app tests`, `migrate --check`, `eval --mode palavras` APROVADO, `pytest tests/panel -m perf -q`; `STATE.md` com o estado, o checklist 9.10 e o que depende do deploy (link no celular) |

## Verificacao das ondas (arquivos compartilhados)

| Onda | Tarefas | Compartilhados tocados (uma tarefa cada) |
|---|---|---|
| W0 | T01, T02 | `values.py`, `config.py` (T02) |
| W1 | T03, T04, T05, T06, T07 | `outbox.py` (T03), `workers.py` (T04), `pipeline.py` (T05), `deliveries.py` (T07) |
| W2 | T08 | - |
| W3 | T09, T10, T11 | `tailwind.css` (T10) |
| W4 | T12 | `tailwind.css` |
| W5 | T13, T14 | - |

## Cobertura dos criterios

| AC | Tarefas |
|---|---|
| RH-1 | T07 |
| RH-2 | T07 |
| RH-3 | T07 |
| RH-4 | T07 |
| RH-5 | T12 |
| RH-6 | T09, T12 |
| RH-7 | T01, T08, T09 |
| RH-8 | T09 |
| RH-9 | T02, T04, T06, T08 |
| RH-10 | T03, T12 |
| RH-11 | T01, T08 |
| RH-12 | T03, T08, T09 |
| RH-13 | T03, T08 |
| RH-14 | T12 |
| RH-15 | T01, T05, T12 |
| RH-16 | T01, T08 |
| RH-17 | T10 |
| RH-18 | T01, T10 |
| RH-19 | T10 |
| RH-20 | T08, T11, T13 |
| RH-21 | T02, T07 |
