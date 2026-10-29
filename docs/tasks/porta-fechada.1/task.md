# Tasks — porta-fechada.1 (Receber mensagens da WhatsApp Cloud API e aplicar a lista positiva com opt-in)

> **Story:** [porta-fechada.1](../../stories/porta-fechada.1.md)
> **Spec:** [spec.md](./spec/spec.md) (aprovada: Fase 1 e Fase 2 por Caio em 2026-10-23)
> **Fase:** 3 (decomposição), rascunho de 2026-10-23, aguardando aprovação antes da Fase 4.
> **Task ID do épico:** PORTA-FECHADA-1. Prefixo das tarefas: `PF-nn`.

## Conventions

- `[ ]` pendente, `[~]` em andamento, `[x]` pronta. `[P]` = paralelizável com as irmãs da mesma onda (arquivos disjuntos, sem dependência entre si).
- Commit: `{type}(porta-fechada): PF-nn - {descrição}` (CLAUDE.md). Uma tarefa = um commit lógico, suíte verde.
- **Caminhos** são relativos à raiz do repositório (`AgenteWPP/`). O código fica em `backend/`; ao citar `app/...` entenda `backend/app/...`.
- **Whitelist:** a Fase 4 só pode tocar os arquivos listados em "Files" da tarefa (CLAUDE.md, "never"). Cada tarefa traz os próprios testes; testes de outra tarefa não são editados.
- **Comando de teste**, rodado a partir de `backend\`: `.venv\Scripts\python -m pytest`. Python de desenvolvimento: 3.14 via `py`; o código **deve continuar compatível com 3.12** (droplet): nada de sintaxe ou API exclusiva do 3.13/3.14.
- **Banco de desenvolvimento (fato de ambiente, 2026-10-23):** o PostgreSQL **18.6** já está instalado e rodando na máquina do Caio (serviço Windows `postgresql-x64-18`, porta 5432, `psql` em `C:\Program Files\PostgreSQL\18\bin\psql.exe`). Nenhum download é necessário. **O servidor de produção roda Postgres 16: nenhuma migration, SQL ou serviço pode usar recurso exclusivo do 17/18** (por exemplo `uuidv7()`, colunas virtuais geradas, `RETURNING OLD/NEW`, `WITHOUT OVERLAPS`, `MERGE ... RETURNING`). Toda migration deve ser conferida contra a documentação do PG 16. A aplicação **nunca** usa o superusuário `postgres`: usa os papéis dedicados `jarvis` (desenvolvimento) e `jarvis_test` (testes), criados em PF-01.
- **Credenciais:** só em `backend/.env` (que o Caio preenche; agentes não leem nem escrevem `.env*`). O modelo versionado é `backend/configuracao-modelo.txt` (criado em PF-02, sem valores), como no piloto.
- **Testes marcados `db`** (`@pytest.mark.db`) usam Postgres real via `TEST_DATABASE_URL` (banco `jarvis_test`) e **pulam sozinhos** se a variável não existir. Quando o Caio já tiver preenchido o `.env`, a verificação das tarefas `db` roda de verdade; se `TEST_DATABASE_URL` não estiver definida na sessão, o executor reporta quantos testes foram pulados. **Nunca** apontar `TEST_DATABASE_URL` para o banco do droplet nem para o banco `jarvis` de desenvolvimento (os testes fazem `TRUNCATE`).
- **Lint e tipos** (toda tarefa de código): `.venv\Scripts\python -m ruff check app tests` e `.venv\Scripts\python -m mypy app` limpos (configs criadas em PF-01).
- Sem dependência nova além das fixadas em spec 4 (fastapi, uvicorn, asyncpg, pydantic-settings, httpx; dev: pytest, pytest-asyncio, ruff, mypy). Adicionar qualquer outra exige emenda na spec.
- Os testes de uma tarefa seguem os nomes de 4.7, com uma exceção deliberada: `integration/test_alerts_deliveries.py` virou `test_alerts.py` (PF-16) + `test_deliveries.py` (PF-23), e `test_reply_policy_flags.py` virou `test_flags.py` (PF-11) + `test_reply_policy.py` (PF-17), para que cada arquivo tenha um único dono. Registrar isso na spec 4.7 na PF-41.

## Interfaces fundação que o painel.1 consome (e em que tarefa ficam prontas)

Dono único por arquivo: o painel só **chama**; qualquer mudança de contrato vem por emenda a esta lista.

| Fundação | Interface (spec 4.3.2) | Tarefa | Onda |
|---|---|---|---|
| Esqueleto, config, pool, runner de migrations | `app.config.Settings`, `app.db.connection`, `python -m app.db.migrate` | PF-02, PF-03, PF-04 | 2 |
| Migrations 001–005 (inclui `chat.tickets`, `ops.v_tickets`) | tabelas de 4.2 | PF-06, PF-07 | 3 |
| `users.validate_row` (pura) | `validate_row(row, now=)` | PF-12 | 4 |
| Auditoria e flags | `audit.write`, `Flags.get_bool/set_bool/team_group_enabled` (`paused`, `brand_enabled:*`) | PF-11 | 4 |
| `alerts.raise_alert(..., email=)` | R3 | PF-16 | 5 |
| `ReplyPolicy`, `FlagsReplyPolicy` | R4 | PF-17 | 5 |
| Tickets (`chat.tickets`) | `tickets.create_media_ticket`, número J-nnnn | PF-18 | 5 |
| Fila de saída (enfileirar) | `outbox.enqueue` | PF-19 | 5 |
| `attempts.dismiss` | 4.2.8 | PF-20 | 5 |
| `users.register`, revogar | R2 | PF-26 | 7 |
| `users.resend`, `swap_number` | 4.3.3 | PF-31 | 8 |

**Ponto de partida seguro do painel.1:** fim da onda 5 para tudo menos `users.register`/revogar; fim da onda 7 para a administração completa de pessoas. O painel não depende do pipeline, do webhook nem do CLI.

## Tasks

### PF-01 — Preparar o ambiente de desenvolvimento (venv, requirements, papéis e bancos `jarvis`/`jarvis_test`, marcador `db`)

Entrega o esqueleto de projeto e o ambiente. PostgreSQL 18.6 local já existe (ver Conventions): **nada a baixar nem instalar**.

**Files:**
- Create: `backend/requirements.txt`, `backend/requirements-dev.txt`, `backend/pytest.ini`, `backend/ruff.toml`, `backend/mypy.ini`, `backend/DEV_SETUP.md`, `backend/scripts/dev_db_setup.sql`, `backend/tests/__init__.py`, `backend/tests/conftest.py`, `backend/tests/unit/__init__.py`, `backend/tests/unit/test_env_smoke.py`
- Modify: `.gitignore` (acrescentar `backend/.venv/`, `.env*`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`; manter o que já existe)

**Depends on:** none

**Escopo:**
1. `backend/.venv` criado com `py -3 -m venv --system-site-packages backend\.venv` (Python 3.14 do Caio, reaproveitando as bibliotecas já instaladas). `requirements.txt` com as dependências da spec 4, nas versões instaladas (ver "Decisões do dono", item 2). Registrar em `DEV_SETUP.md` a combinação testada em 3.14 e a exigida em 3.12.
2. `pytest.ini`: `asyncio_mode = auto`, `testpaths = tests`, `pythonpath = .`, marcadores `db` e `security` registrados (`--strict-markers`).
3. `tests/conftest.py`: hook `pytest_collection_modifyitems` que **pula** todo teste `@pytest.mark.db` quando `TEST_DATABASE_URL` não estiver definida, com o motivo escrito ("defina TEST_DATABASE_URL; ver backend/DEV_SETUP.md"). Esse arquivo não é mais editado por outras tarefas (fixtures de banco ficam em PF-04). Pode carregar `backend/.env` só para ler `TEST_DATABASE_URL` se a variável não estiver no ambiente (parse simples de linhas `CHAVE=valor`, sem imprimir valores; arquivo ausente é normal).
4. `scripts/dev_db_setup.sql`: SQL para ser executado **pelo Caio** como superusuário, uma única vez: cria os papéis `jarvis` e `jarvis_test` (`LOGIN`, sem superuser, sem createrole, **senha como placeholder `__DEFINIR_SENHA__`**, nunca uma senha real no arquivo) e os bancos `jarvis` (dono `jarvis`) e `jarvis_test` (dono `jarvis_test`), `REVOKE ALL ... FROM PUBLIC` nos bancos, tudo idempotente (`DO $$ ... IF NOT EXISTS`; `CREATE DATABASE` fora de bloco, via `\gexec`). Sem recursos exclusivos do PG 17/18. Comentário no topo: "o superusuário `postgres` serve só para rodar este script; a aplicação nunca o usa".
5. `DEV_SETUP.md` (PT-BR, curto): criar o venv; instalar; rodar testes; passo a passo para o Caio rodar o script (`& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -f backend\scripts\dev_db_setup.sql`, depois definir as duas senhas com `\password jarvis` e `\password jarvis_test` no psql, para a senha não ficar em arquivo nem no histórico do shell); copiar `backend/configuracao-modelo.txt` para `backend/.env` e preencher `DATABASE_URL` (banco `jarvis`, papel `jarvis`) e `TEST_DATABASE_URL` (banco `jarvis_test`, papel `jarvis_test`); aviso de que o dev local é PG 18 e a produção é PG 16 (não usar recurso novo); como parar/remover os bancos de dev (`DROP DATABASE`) sem tocar no serviço. **O assistente não escreve `.env` e não tem acesso à senha do `postgres`**: se os papéis ainda não existirem quando a tarefa rodar, o executor para e pede ao Caio que execute o script (ver "Decisões do dono", item 1) e marca a verificação `db` como pendente.
6. `test_env_smoke.py`: um teste trivial que prova que o pytest roda no venv, mais um `@pytest.mark.db` que apenas conecta ao banco (`SELECT 1` e `SHOW server_version_num` com asyncpg) e pula sem `TEST_DATABASE_URL`; falha se o papel conectado for superusuário (`SELECT rolsuper FROM pg_roles WHERE rolname = current_user`), garantindo que o `postgres` nunca é usado.

**Acceptance:** sem AC (infra de ambiente; suporta todos). Critério próprio: `pytest` roda em Windows com Python 3.14; testes `db` pulam com mensagem clara sem `TEST_DATABASE_URL` e passam com ela; nenhum superusuário na configuração da aplicação; nenhuma senha em arquivo versionado; nada fora da whitelist tocado; nenhum download feito.

**Verification:**
- `cd backend; py -3 -m venv .venv; .venv\Scripts\python -m pip install -r requirements-dev.txt` conclui (ou a tarefa para e pergunta, ver item 1).
- `.venv\Scripts\python -m pytest tests/unit/test_env_smoke.py -q` passa; sem `TEST_DATABASE_URL` o teste `db` aparece como `skipped`; com ela (após o Caio rodar o script e preencher o `.env`) passa e confirma papel não superusuário.
- `.venv\Scripts\python -m ruff check tests; .venv\Scripts\python -m mypy --version` executam.
- Revisão: `Select-String -Path backend\scripts\dev_db_setup.sql -Pattern "PASSWORD '"` não encontra senha literal.

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-02 — Configuração (`Settings`) e modelo de variáveis `[P]`

**Files:**
- Create: `backend/app/__init__.py`, `backend/app/config.py`, `backend/configuracao-modelo.txt`, `backend/tests/unit/test_config.py`

**Depends on:** PF-01

**Escopo:** `Settings` (pydantic-settings) com **todas** as variáveis de 4.5 (nomes, padrões, `SecretStr` para segredos e para `ALERT_RECIPIENT_EMAIL`); validadores fail-fast (DATABASE_URL, segredos e `WHATSAPP_PHONE_NUMBER_ID` presentes; hosts de URL base em allow-list; `TEAM_GROUP_ENABLED=true` exige `WAHA_API_KEY` e `TEAM_GROUP_CHAT_ID`); e-mail ausente **não** derruba. Lista de backoff do e-mail parseada de `EMAIL_BACKOFF_SECONDS`. Textos `TEXT_*` (4.3.4) como campos com os padrões propostos. `configuracao-modelo.txt` é o **modelo que o Caio copia para `backend/.env`**: todas as chaves, sem valores, mais `TEST_DATABASE_URL` (comentário: "banco `jarvis_test`, papel `jarvis_test`, nunca o `postgres`"), com exemplo de forma `postgresql://jarvis:<senha>@localhost:5432/jarvis` apenas como texto de ajuda. `TEST_DATABASE_URL` não pertence ao `Settings` de produção (é lida só pelos testes). Importações absolutas (`from app.x import y`). **Nunca ler `.env*` do disco real nos testes** (usar `Settings(_env_file=None, **kwargs)`).

**Acceptance:** AC-13 (chave do grupo desligada por padrão; validador), AC-37 (segredos só em ambiente, destinatário `SecretStr` sem padrão), NFR de segredos (spec 5).

**Verification:**
- `.venv\Scripts\python -m pytest tests/unit/test_config.py -q` (padrões, validadores, `repr` mascara segredos, e-mail ausente não falha, host fora da allow-list falha).
- `ruff check app tests`; `mypy app`.

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-03 — Núcleo transversal: relógio, erros, log JSON, redação, segurança, valores `[P]`

**Files:**
- Create: `backend/app/core/__init__.py`, `backend/app/core/clock.py`, `backend/app/core/errors.py`, `backend/app/core/logging_json.py`, `backend/app/core/log_redaction.py`, `backend/app/core/security.py`, `backend/app/core/values.py`, `backend/tests/unit/test_core_clock_errors.py`, `backend/tests/unit/test_log_redaction.py`, `backend/tests/unit/test_security.py`, `backend/tests/unit/test_values.py`

**Depends on:** PF-01

**Escopo (adaptar o AUTO-1 onde houver):**
- `clock.py`: `Clock` (Protocol), `SystemClock`, `FakeClock` utilizável nos testes.
- `errors.py`: exceções tipadas (`DuplicatePhone`, `InvalidSignature`, `PayloadTooLarge`, `TransientDbError`, erros de validação com campo e motivo, etc.).
- `logging_json.py`: formatter JSON de uma linha (`ts`, `level`, `logger`, `event`, `request_id`, extras); contexto de `request_id`.
- `log_redaction.py`: reuso do AUTO-1 + padrões de telefone (E.164 e `55\d{10,11}`), e-mail e registro de todo `SecretStr`.
- `security.py`: HMAC SHA-256 sobre **bytes brutos**, `verify_hub_signature(raw: bytes, header: str|None, secret) -> bool` com `hmac.compare_digest` sobre bytes (header não ASCII devolve `False`, nunca exceção), comparação de verify token em tempo constante.
- `values.py` (**dono único; ninguém mais edita**): enums e listas (status de usuário, `access_decision`, `decision`, `reply_kind`, `kind` de saída, perfis (5), UFs, marcas (5), canais iniciais, motivos de revogação, `SEND_RESULT`), constantes de payload do botão (`OPTIN_ACCEPT:`/`OPTIN_DECLINE:`) e o dataclass `MessageOutcome(outbound_ids, delivery_ids, answer_request, transient_failure)` que o webhook e o pipeline compartilham (evita o webhook importar o pipeline).

**Acceptance:** AC-2 (comparação de bytes; header não ASCII = rejeitado), AC-32 (logs sem PII/segredos), base de AC-31.

**Verification:**
- `.venv\Scripts\python -m pytest tests/unit/test_core_clock_errors.py tests/unit/test_log_redaction.py tests/unit/test_security.py tests/unit/test_values.py -q` (inclui header com caractere não ASCII sem TypeError; telefone e e-mail mascarados; segredo nunca aparece em `repr`/log).
- `ruff check app tests`; `mypy app`.

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-04 — Pool asyncpg, runner de migrations e fixtures de banco de teste `[P]`

**Files:**
- Create: `backend/app/db/__init__.py`, `backend/app/db/connection.py`, `backend/app/db/migrate.py`, `backend/tests/db_support.py`, `backend/tests/integration/__init__.py`, `backend/tests/integration/conftest.py`, `backend/tests/security/__init__.py`, `backend/tests/security/conftest.py`, `backend/tests/unit/test_migrate_parse.py`, `backend/tests/integration/test_migrate_runner.py`

**Depends on:** PF-01

**Escopo:**
- `connection.py`: `create_pool(dsn, min_size=2, max_size=10)` (cópia do AUTO-1, sem importar `Settings`; o `main.py` passa o DSN), `transaction()` helper, tradução de erros transitórios (conexão, deadlock, pool esgotado) para `TransientDbError`.
- `migrate.py`: `python -m app.db.migrate [--status|--check]`; ordena pelo prefixo numérico, **recusa prefixo duplicado**, uma transação por arquivo, `pg_advisory_lock`, grava versão e SHA-256 em `ops.schema_migrations` (a tabela é criada pelo próprio runner na 1ª execução se não existir, ver 4.2.4), recusa checksum alterado; `--check` sai com código ≠ 0 se faltar migration; função `is_up_to_date(conn)` para o `/health`. Diretório de migrations configurável por parâmetro (os testes usam um diretório temporário). Usa só SQL compatível com PG 16.
- `tests/db_support.py`: fixtures reutilizáveis (`db_pool`, `db_conn`, aplicação das migrations reais uma vez por sessão, `TRUNCATE ... RESTART IDENTITY CASCADE` entre testes), lidas por `integration/conftest.py` e `security/conftest.py` (`from tests.db_support import *`). Recusa rodar se o nome do banco em `TEST_DATABASE_URL` não terminar em `_test` (proteção contra apontar para `jarvis`). **Esses três arquivos são do PF-04; outras tarefas só os importam.**

**Acceptance:** base de AC-7 (UNIQUE exige banco real), ADR de 2026-10-23 (runner), spec 4.2.6.

**Verification:**
- `.venv\Scripts\python -m pytest tests/unit/test_migrate_parse.py -q` (ordenação, prefixo duplicado, checksum; sem banco).
- `.venv\Scripts\python -m pytest tests/integration/test_migrate_runner.py -q` (db: aplica, é idempotente, recusa checksum diferente, `--check`, recusa banco que não termina em `_test`; **pula sem banco**).
- `ruff check app tests`; `mypy app`.

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-05 — Telefone: normalização, validação e chave de comparação `[P]`

**Files:**
- Create: `backend/app/services/__init__.py`, `backend/app/services/phone.py`, `backend/tests/unit/test_phone.py`

**Depends on:** PF-01

**Escopo (4.4.8, função pura):** entrada tolerante, exige DDI; Brasil: lista de DDDs válidos (conferir contra a Anatel; registrar a fonte em comentário), celular com 9 dígitos mantém, 8 dígitos começando em 6–9 ganha o 9, fixo (2–5) recusado com "Telefone fixo não recebe WhatsApp"; demais países só E.164 (8–15 dígitos). Saídas: `phone_e164` canônico (BR móvel com o 9) e `match_key` (sem `+` e, em celular BR de 13 dígitos, sem o 9º dígito). Função `mask_phone` (`***1234`).

**Acceptance:** AC-9, AC-15 (telefone inválido rejeitado com motivo), base de AC-8.

**Verification:**
- `.venv\Scripts\python -m pytest tests/unit/test_phone.py -q` (tabela: com/sem 9, parênteses/hífens, `+`, fixo, DDD inexistente, outros países, sem DDI).
- `ruff check app tests`; `mypy app`.

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-06 — Migrations 001–003 (schemas, `access`, `chat`) `[P]`

**Files:**
- Create: `backend/migrations/001_schemas_ops_base.sql`, `backend/migrations/002_access.sql`, `backend/migrations/003_chat.sql`, `backend/tests/integration/test_schema_access_chat.py`

**Depends on:** PF-04

**Escopo:** SQL exatamente como 4.2.2–4.2.4, `IF NOT EXISTS`, imutáveis depois de aplicadas, **somente recursos do PostgreSQL 16** (dev local é 18: conferir). 001: schemas `access`, `chat`, `ops`; `ops.system_state`; `ops.audit_log` + trigger que recusa UPDATE/DELETE. 002: `users` (todos os CHECKs), `optin_requests` (+ índice parcial `optin_one_open_per_user`), `access_attempts`, `unknown_first_messages`. 003: `message_trace` (UNIQUE `wa_message_id`, CHECK `has_sender`, índices), `outbound_messages`. **Não** criar roles/GRANTs (são do servidor, runbook). O papel `jarvis` de dev é dono do banco e roda as migrations.

**Acceptance:** AC-7 (UNIQUE de `wa_message_id`), AC-8 (UNIQUE `bsuid`), AC-9/15 (UNIQUE `phone_key`), AC-18 (CHECK `active_needs_acceptance`), AC-19 (um aviso aberto por usuário), AC-34 (CHECK `revoked_needs_author`), PN-55 (auditoria imutável).

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_schema_access_chat.py -q` (db): cada CHECK/UNIQUE rejeita o caso inválido; UPDATE/DELETE em `audit_log` falha; `ops.schema_migrations` registra 001–003.
- `.venv\Scripts\python -m app.db.migrate --check` com `DATABASE_URL` de teste sai 0 após aplicar.

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-07 — Migrations 004–005 (alertas, entregas, contadores, tickets) `[P]`

**Files:**
- Create: `backend/migrations/004_ops_alerts_deliveries.sql`, `backend/migrations/005_chat_tickets.sql`, `backend/tests/integration/test_schema_ops_tickets.py`

**Depends on:** PF-04

**Escopo:** 004: `ops.alerts` (+ índices), `ops.ticket_seq`, `ops.deliveries` (com `ticket_never_emails`), `ops.security_counters`. 005: `chat.tickets` (CHECKs, `escalated_needs_urgency`, índices) e `ops.v_tickets`. Somente recursos do PG 16. **Pré-requisito:** 003 existir (FK `trace_id`), por isso o teste aplica 001–005 em ordem. Numeração PF fica em 001–099; o painel usa 101–107 (não criar nada nessa faixa).

**Acceptance:** AC-13 (UNIQUE de entrega), AC-36 e R1 (ticket de mídia gravável com `kind='media'`, `classification='MIDIA'`, `escalated`, `urgency`), decisão 9 (CHECK impede e-mail de ticket), AC-37 (estrutura de entrega de e-mail).

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_schema_ops_tickets.py -q` (db): inserir ticket escalado sem urgência falha; entrega `ticket/email` falha; `ops.v_tickets` devolve as colunas; sequência gera `J-0001`.
- `.venv\Scripts\python -m app.db.migrate --status` lista 001–005.

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-08 — Parser do payload da Meta e fixtures `[P]`

**Files:**
- Create: `backend/app/services/inbound.py`, `backend/tests/fixtures/meta/*.json` (texto, botão de modelo, botão interativo, áudio, imagem, status sent/delivered/read/failed, payload múltiplo, outro `phone_number_id`, remetente só com BSUID), `backend/tests/unit/test_inbound.py`

**Depends on:** PF-03

**Escopo (função pura, sem I/O):** `parse_payload(obj) -> ParsedPayload` com mensagens e statuses normalizados; `extract_sender` **isolada em uma função** (wa_id + BSUID; premissa Q5); `ButtonReply(payload)` unificando `button` e `interactive.button_reply`; tipos de conteúdo (texto, mídia: áudio/imagem/vídeo/documento/figurinha/localização/contatos, botão, interativo, reação, system, unsupported); `mime_type` de mídia sem baixar nada; tamanho do texto em caracteres. Filtro por `phone_number_id` é função separada (`split_by_recipient`). Fixtures começam da documentação da Cloud API e **são substituídas por capturas reais do número de teste** depois (nota no cabeçalho de cada JSON).

**Acceptance:** AC-5, AC-6 (parte do parser), AC-14, base de AC-18/20/21 (botão), AC-10 (tipo e mime da 1ª mensagem).

**Verification:**
- `.venv\Scripts\python -m pytest tests/unit/test_inbound.py -q`
- `ruff check app tests`; `mypy app`.

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-09 — Interface de canal e cliente da Cloud API `[P]`

**Files:**
- Create: `backend/app/services/channel.py`, `backend/app/services/whatsapp.py`, `backend/tests/fakes.py`, `backend/tests/unit/test_whatsapp_channel.py`

**Depends on:** PF-02, PF-03

**Escopo:** `Channel` (Protocol), `SendResult`, `TemplateMessage`, `ReplyButton` (4.3.2); cliente `WhatsAppCloudChannel` em httpx (`send_text`, `send_template`, `send_reply_buttons`, `verify_signature` chamando `core.security`); `build_welcome_template` em **função única** (modelo com 1 parâmetro e 2 botões de resposta rápida, payloads `OPTIN_ACCEPT:<id>`/`OPTIN_DECLINE:<id>`); base da Graph validada contra allow-list; classificação do resultado: 429/503, `ConnectError`/`ConnectTimeout` = `transient`; `ReadTimeout` = `ambiguous`; 4xx = definitivo; código/subcódigo cru no `SendResult`, mapa de códigos numa tabela substituível. `tests/fakes.py`: `FakeChannel` (registra chamadas em ordem, falha por tipo de erro) usado por todos os testes seguintes. **Não existe nenhum cliente de download de mídia** (AC-31/36).

**Acceptance:** AC-17 (só modelo para quem ainda não escreveu), AC-33 (classificação do erro), AC-2 (assinatura por bytes via Channel), spec 4.3.2.

**Verification:**
- `.venv\Scripts\python -m pytest tests/unit/test_whatsapp_channel.py -q` (`httpx.MockTransport`: payload do modelo, botões, 429/503 transitório, ReadTimeout ambíguo, 4xx definitivo, host fora da allow-list recusado).
- `ruff check app tests`; `mypy app`.

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-10 — Clientes de e-mail (Resend) e WAHA `[P]`

**Files:**
- Create: `backend/app/services/notifications.py`, `backend/app/services/waha.py`, `backend/tests/unit/test_notifications_waha.py`

**Depends on:** PF-02, PF-03

**Escopo:** só a camada de rede, sem banco. `notifications.send_email(subject, body)` via Resend (padrão AUTO-1, httpx); destinatário vem de `Settings` (`SecretStr`, nunca em log); sem chave ou sem destinatário = resultado `skipped` com aviso único no log. `waha.send_group_text(text)` (padrão AUTO-1; chamado só se a camada acima mandar). Resultados tipados (`DeliveryResult`: ok/transitório/definitivo/skipped). Falha nunca levanta exceção para o chamador. Logs sem PII nem endereço.

**Acceptance:** AC-37 (e-mail sem bloquear, falha logada sem PII), AC-13 (caminho WAHA existe e é testado).

**Verification:**
- `.venv\Scripts\python -m pytest tests/unit/test_notifications_waha.py -q` (`MockTransport`: sucesso, 5xx, timeout, sem chave = skipped, o endereço do destinatário não aparece em nenhum registro de log capturado).
- `ruff check app tests`; `mypy app`.

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-11 — Auditoria e flags (`paused`, `brand_enabled:*`, grupo) `[P]`

**Files:**
- Create: `backend/app/services/audit.py`, `backend/app/services/flags.py`, `backend/tests/integration/test_audit.py`, `backend/tests/integration/test_flags.py`

**Depends on:** PF-03, PF-04, PF-06

**Escopo:** `audit.write(conn, actor_type, actor, action, target_type, target_id, old, new)` (sem PII; falha ao gravar aborta a ação). `Flags` (4.3.2, R4): `get_bool` com cache TTL 30 s por processo (relógio injetável), `set_bool(conn, key, value, actor)` grava em `ops.system_state`, audita na mesma transação e **invalida o cache**; chaves aceitas `team_group_enabled | paused | brand_enabled:<marca>` (marca ∈ `core/values.BRANDS`; outra chave = erro tipado), padrões `false | false | true`; `team_group_enabled()` combina o valor inicial de `Settings` com `system_state`. **Este é o arquivo do qual o painel depende** (dono único: PF).

**Acceptance:** R4, AC-13 (chave do grupo), AC-34 (auditoria), PN-55.

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_audit.py tests/integration/test_flags.py -q` (db): padrões; escrita audita e invalida cache; chave inválida recusada; TTL de 30 s com relógio falso; ação aborta se a auditoria falhar.
- `ruff check app tests`; `mypy app`.

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-12 — `users.validate_row` (validação pura do cadastro) `[P]`

**Files:**
- Create: `backend/app/services/users.py` (somente `validate_row`, `ValidatedUser`, `FieldError`; as demais funções entram em PF-26 e PF-31), `backend/tests/unit/test_validate_row.py`

**Depends on:** PF-03, PF-05

**Escopo (R2, PURA: sem banco, rede nem relógio global):** `validate_row(row, *, now) -> ValidatedUser | list[FieldError]`; os 8 campos obrigatórios (nome, telefone, perfil, empresa, UFs, canais, marcas, gestor) com mensagem indicando o campo e o motivo; listas separadas por `|` ou lista Python; perfil ∈ 5 perfis; UFs ∈ lista; marcas ∈ 5 marcas; canais ∈ lista inicial; telefone via `phone.py`; `valid_until` sempre `None`. Chaves do CSV em PT-BR (`nome,telefone,perfil,empresa,ufs,canais,marcas,gestor`) mapeadas para campos internos.

**Acceptance:** AC-15 (rejeita indicando o campo), AC-16 (mesma exigência do CSV), R2.

**Verification:**
- `.venv\Scripts\python -m pytest tests/unit/test_validate_row.py -q` (um caso por campo ausente/inválido; vários erros de uma vez; nenhuma chamada ao relógio global: roda com `now` fixo).
- `ruff check app tests`; `mypy app`.

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-13 — Decisão de acesso (`access.decide`) e textos fixos `[P]`

**Files:**
- Create: `backend/app/services/access.py`, `backend/app/services/replies.py`, `backend/tests/unit/test_access.py`

**Depends on:** PF-03, PF-08

**Escopo (puros):** `decide(sender, content, now) -> AccessDecision` implementando a tabela de 4.4.2 por completo (status × conteúdo × botão atual/antigo; `valid_until` ignorado; lembrete 1/24h por `optin_reminder_at`; `sair` normalizado; mídia = `escalated_media`; botão/reação/system/unsupported de ativo = `ignored_type`); `can_send_proactive(user)`; `replies.py`: textos (de `Settings.TEXT_*`), montagem do lembrete interativo e dos botões de aviso (payload com id do aviso). Nada de I/O.

**Acceptance:** AC-10, 11, 12, 14, 19, 21, 27, 28 (predicado), base de AC-30, AC-31, AC-36.

**Verification:**
- `.venv\Scripts\python -m pytest tests/unit/test_access.py -q` (tabela exaustiva; botão antigo de `active`/`declined`/`revoked` não muda nada; `declined`/`revoked` sempre silêncio sem alerta).
- `ruff check app tests`; `mypy app`.

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-14 — Resolução de identidade (telefone/BSUID) `[P]`

**Files:**
- Create: `backend/app/services/identity.py`, `backend/tests/integration/test_identity.py`

**Depends on:** PF-05, PF-06

**Escopo:** `resolve(conn, wa_id, bsuid)` com `SELECT … WHERE phone_key=$1 OR bsuid=$2 FOR UPDATE` (cast explícito de parâmetros repetidos, lição AUTO-1), associa o identificador faltante, devolve `unknown | user | conflict` (dois usuários, ou BSUID guardado diferente do recebido); BSUID já de outro usuário **não** é reassociado. Roda na transação do chamador.

**Acceptance:** AC-8, AC-9, AC-14.

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_identity.py -q` (db): com/sem 9º dígito, associação do BSUID, conflito sem reassociação, BSUID-only sem cadastro = desconhecido.
- `ruff check app tests`; `mypy app`.

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-15 — Contadores de segurança e limite HTTP por IP `[P]`

**Files:**
- Create: `backend/app/services/counters.py`, `backend/app/services/rate_limit.py`, `backend/tests/unit/test_rate_limit.py`, `backend/tests/integration/test_counters.py`

**Depends on:** PF-03, PF-07

**Escopo:** `counters.py`: incremento em memória por (`day`, `kind`) para `invalid_signature | oversize_payload | bad_verify_token | invalid_json | wrong_recipient` e `flush(conn)` que grava em lote em `ops.security_counters` (UPSERT somando); sem PII. `rate_limit.py`: adaptação do AUTO-1 (janela por `X-Real-IP`, nunca `X-Forwarded-For`; webhook `RL_WEBHOOK_MAX`/`RL_WEBHOOK_WINDOW`, 600/60 s), com relógio injetável.

**Acceptance:** AC-2/AC-4 (contadores de rejeição), 4.6 (gravação em lote a cada 30 s; o laço é do PF-34), 429 do webhook (4.3.1).

**Verification:**
- `.venv\Scripts\python -m pytest tests/unit/test_rate_limit.py tests/integration/test_counters.py -q`
- `ruff check app tests`; `mypy app`.

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-16 — Alertas (`raise_alert` com e-mail opcional) `[P]`

**Files:**
- Create: `backend/app/services/alerts.py`, `backend/tests/integration/test_alerts.py`

**Depends on:** PF-07, PF-11

**Escopo (R3, 4.4.6):** `raise_alert(conn, kind, severity, message, payload, *, user_id=None, dedup_key=None, dedup_window=None, email=True) -> int | None`: dedup sob `pg_advisory_xact_lock(hashtextextended(dedup_key, 0))`; ao criar, insere em `ops.deliveries` a linha `group` (`pending` se `flags.team_group_enabled()`, senão `panel_only`) e, **só se `email=True`**, a linha `email` (`pending`, ou `skipped` se destinatário/chave do provedor ausentes). Mensagem PT-BR sem PII e sem texto de mensagem; `payload` só com ids e contagens (validação defensiva que recusa chaves como telefone/nome/texto). Todos os chamadores de PF usam o padrão. **Dono único do arquivo (o painel só chama).**

**Acceptance:** R3, AC-37 (alerta novo gera entrega de e-mail), AC-8 (dedup 24 h do conflito), AC-29 (dedup 1/h do limite), AC-12 (a API não é chamada para tentativas de acesso: ver testes de PF-36).

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_alerts.py -q` (db): cria alerta + 2 entregas; `email=False` cria só a de grupo; chave do grupo desligada = `panel_only`; dedup devolve `None` sem nova entrega; destinatário vazio = e-mail `skipped`; payload com PII é recusado.
- `ruff check app tests`; `mypy app`.

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-17 — Política de resposta (`ReplyPolicy`, `FlagsReplyPolicy`) `[P]`

**Files:**
- Create: `backend/app/services/reply_policy.py`, `backend/tests/integration/test_reply_policy.py`

**Depends on:** PF-11

**Escopo (R4):** `PolicyDecision(ok, reason)`, `ReplyPolicy` (Protocol), `AllowAllPolicy` (padrão), `FlagsReplyPolicy(flags)`: `paused=true` nega toda resposta conversacional (`reason='paused'`); `brand_enabled:<m>=false` nega respostas de conteúdo daquela marca (`'brand_disabled'`); respostas sem marca ignoram o flag de marca; **isentas da pausa**: `welcome_template`, `optin_reminder`, `optin_confirmation`, `optin_declined`; `brand_allows(brand)`. Definidos aqui (e não no outbox) para evitar import circular; o outbox importa daqui.

**Acceptance:** R4, decisão de design "pausa × opt-in" (4.9).

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_reply_policy.py -q` (db): pausa nega `under_construction`, `media_ack`, `rate_limit_notice`, `opt_out_confirmation`; isenções passam; marca desligada nega só com marca; escrita de flag vale no mesmo processo imediatamente.
- `ruff check app tests`; `mypy app`.

**Estimate:** 2 h  **Status:** `[x]`

---

### PF-18 — Tickets escalados de mídia (`chat.tickets`) `[P]`

**Files:**
- Create: `backend/app/services/tickets.py`, `backend/tests/integration/test_tickets.py`

**Depends on:** PF-07, PF-11

**Escopo (R1, 4.4.6):** `create_media_ticket(conn, user_id, trace_id, msg_type)` insere em `chat.tickets` (`kind='media'`, `classification='MIDIA'`, `escalated=true`, `status='awaiting'`, `urgency='urgente'`, `brand` nulo, `message='[áudio]'` etc., nunca conteúdo), número `f"J-{n:04d}"` da `ops.ticket_seq` (**não usar `lpad`**, que trunca acima de 4 dígitos), e **uma** linha `group` em `ops.deliveries` (`source_type='ticket'`, `source_id = chat.tickets.id`; `pending` ou `panel_only` conforme a chave). **Nunca** cria alerta nem entrega `email`.

**Acceptance:** AC-36 (ticket imediato em tabela própria, sem alerta e sem e-mail), R1, AC-13, decisão 9.

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_tickets.py -q` (db): ticket com campos exatos; linha única de grupo; zero linhas em `ops.alerts` e zero entregas `email`; número acima de 9999 sai `J-10000`.
- `ruff check app tests`; `mypy app`.

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-19 — Fila de saída: enfileirar (`outbox.enqueue`) `[P]`

**Files:**
- Create: `backend/app/services/outbox.py` (somente enfileiramento e tipos; `dispatch` entra em PF-25, recuperação em PF-30), `backend/tests/integration/test_outbox_enqueue.py`

**Depends on:** PF-06

**Escopo:** `enqueue(conn, user, kind, *, body=None, template=None, inbound_trace_id=None, optin_request_id=None) -> int`: grava `chat.outbound_messages` com `to_phone = users.phone_e164`, `required_status` coerente com o `kind` (boas-vindas/lembrete: `pending_optin`; recusa: `declined` — a recusa muda o status na mesma transação, ver spec 4.4.3 e AC-20/23, corrigido na implementação; demais: `active`), `status='pending'`, `next_attempt_at`. Valida `kind` contra `core/values`. Roda na transação do chamador.

**Acceptance:** AC-17 (boas-vindas como modelo), AC-23 (base: `required_status`), AC-21/30 (linhas de saída).

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_outbox_enqueue.py -q` (db): destino é `phone_e164`; `required_status` por tipo; kind inválido recusado.
- `ruff check app tests`; `mypy app`.

**Estimate:** 2 h  **Status:** `[x]`

---

### PF-20 — Tentativas de acesso e limite de 60/h `[P]`

**Files:**
- Create: `backend/app/services/attempts.py`, `backend/app/services/limits.py`, `backend/tests/integration/test_attempts.py`, `backend/tests/integration/test_limits.py`

**Depends on:** PF-06

**Escopo:** `attempts.record(conn, ...)`: upsert em `access_attempts` por `phone_key`/`bsuid` (+1, `last_attempt_at`, reabre `dismissed_at`), 1ª vez guarda tipo/mime/tamanho e, **somente se `UNKNOWN_FIRST_TEXT_STORE` e a mensagem for texto**, o corpo em `unknown_first_messages` (nunca sobrescreve; nunca guarda mídia); `attempts.dismiss(conn, attempt_id, actor)` (painel). `limits.check_and_count(conn, user_id, now)`: com a linha do usuário já travada (`FOR UPDATE` feito na identidade), conta `counted_in_limit` nos últimos 60 min (janela deslizante, índice parcial, relógio por parâmetro); `rate_limit_notice_due(user, now)`; bloqueadas não prolongam a janela.

**Acceptance:** AC-10, 11, 12 (sem alerta: a camada não chama alertas), 27, AC-29 (limite exato).

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_attempts.py tests/integration/test_limits.py -q` (db): contador, 1ª mensagem guardada só com a chave ligada, nunca sobrescrita, mídia sem corpo; 60 passam e a 61ª é bloqueada; fora da janela libera; relógio falso.
- `ruff check app tests`; `mypy app`.

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-21 — Opt-in: emitir aviso, aceitar, recusar `[P]`

**Files:**
- Create: `backend/app/services/optin.py`, `backend/tests/integration/test_optin_service.py`

**Depends on:** PF-11, PF-13, PF-19

**Escopo (4.4.3, na transação do chamador):** `issue_notice(conn, user, issued_by)` (marca o aviso anterior `superseded`, abre outro com `NOTICE_VERSION`, enfileira o modelo de boas-vindas com payloads `OPTIN_ACCEPT:<id>`/`OPTIN_DECLINE:<id>`; **usado por `users.register`, `resend` e `swap_number`**); `accept(conn, user, notice_id, bsuid, now)` (só vale se `<id>` é o aviso `open` do usuário; `active`, `accepted_at`, versão, associa BSUID, enfileira confirmação); `decline(...)` (`declined`, `declined_at`, texto de recusa); `maybe_remind(...)` (lembrete 1/24h). Botão antigo não muda nada.

**Acceptance:** AC-17 (modelo, nunca texto livre), AC-18, AC-19, AC-20, AC-21, base de AC-35.

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_optin_service.py -q` (db): aceite grava data e versão; botão de aviso antigo/de outro usuário não vale; recusa; lembrete no máximo 1/24h; `active_needs_acceptance` nunca violado.
- `ruff check app tests`; `mypy app`.

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-22 — Rastro por mensagem (`trace`) `[P]`

**Files:**
- Create: `backend/app/services/trace.py`, `backend/tests/integration/test_trace.py`

**Depends on:** PF-06

**Escopo:** `begin(conn, wa_message_id, ...) -> int | None` (`INSERT … ON CONFLICT (wa_message_id) DO NOTHING RETURNING id`, decisão transitória `processing`; `None` = duplicata); `finalize(...)` (decisão, motivo, `counted_in_limit`, corpo **somente** se `allowed`, `reply_kind`, `ticket_number`); `mark_error(...)` (rastro `error` em transação separada); `record_send_result(...)` (`reply_wa_message_id`, `reply_sent_at`, `latency_ms`, `send_result`, `decision` = `send_failed`/`revoked_before_send`). Garantia de que `processing` nunca fica commitado.

**Acceptance:** AC-7 (duplicata sem efeito), AC-32 (exatamente 1 rastro, corpo só de autorizado), AC-23/AC-33 (campos de resultado).

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_trace.py -q` (db): duplicata devolve `None`; duplicata concorrente (duas conexões) cria um rastro; `body` recusado quando a decisão não é `allowed`; nenhum `processing` após `finalize`.
- `ruff check app tests`; `mypy app`.

**Estimate:** 2 h  **Status:** `[x]`

---

### PF-23 — Despacho de alertas e tickets (`deliveries`) `[P]`

**Files:**
- Create: `backend/app/services/deliveries.py`, `backend/tests/integration/test_deliveries.py`

**Depends on:** PF-10, PF-11, PF-16, PF-18

**Escopo (4.4.6):** `dispatch(delivery_id)`: claim atômico (`UPDATE … status='sending' WHERE id=$1 AND status='pending' RETURNING`), envio, e `sent` | `pending` com backoff (`EMAIL_BACKOFF_SECONDS`; `EMAIL_MAX_ATTEMPTS=6` → `failed`, **sem novo e-mail**) | `skipped`; `build_email` (assunto `[ALTA] Jarvis: <rótulo>`, corpo com severidade, tipo, texto do alerta, contagem e instante America/Sao_Paulo, "usuário #id", link do painel se houver; **nunca** telefone/nome/texto); grupo só se `flags.team_group_enabled()` for verdadeiro **na hora do envio** (desligado depois da criação = `panel_only`); texto curto do grupo sem PII; falha do WAHA mantém o registro e retenta sem afetar o fluxo. Falha nunca propaga ao webhook.

**Acceptance:** AC-13, AC-37, AC-36 (ticket nunca envia e-mail), AC-12 (nada para tentativas de acesso).

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_deliveries.py -q` (db, `MockTransport`): e-mail enviado/falho com backoff/desiste; independe da chave do grupo; chave desligada = zero chamadas ao WAHA; ligada envia; WAHA com falha retenta; o corpo do e-mail e os logs não contêm telefone, nome, texto nem o endereço do destinatário.
- `ruff check app tests`; `mypy app`.

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-24 — Borda HTTP do webhook (GET/POST) `[P]`

**Files:**
- Create: `backend/app/api/__init__.py`, `backend/app/api/webhook.py`, `backend/tests/integration/test_webhook_http.py`

**Depends on:** PF-08, PF-09, PF-15

**Escopo (4.3.1 e 4.4.1 passos 0–5 e 7–8):** só HTTP. Router com `GET /webhook/whatsapp` (challenge/403, token em tempo constante) e `POST` na ordem **limite por IP → tamanho (413 por `Content-Length` e em stream) → assinatura (403, sem parse) → JSON (400) → `phone_number_id` (descarta o `value` inteiro, contador `wrong_recipient`) → delega**. A lógica de domínio é injetada: `PayloadProcessor` (Protocol: `handle_message(msg) -> MessageOutcome`, `handle_status(st)`), obtido de `request.app.state`; o webhook **não importa o pipeline**. Cada mensagem em chamada independente; erro transitório (`TransientDbError`) em qualquer uma → as demais seguem e a resposta final é 503; `BackgroundTasks` recebe os ids de saída após o 200. Texto de log só com eventos de 4.6.

**Acceptance:** AC-1, AC-2, AC-3, AC-4, AC-6, AC-31 (ordem tamanho → assinatura), AC-5 (status não gera decisão).

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_webhook_http.py -q` com `PayloadProcessor` falso, **sem banco** (`TestClient`): GET certo/errado; POST sem assinatura, assinatura inválida e header não ASCII = 403 sem chamar o processador; corpo > 64 KB = 413 antes da assinatura; JSON inválido = 400; outro `phone_number_id` descartado; payload múltiplo chama o processador N vezes; 503 em erro transitório; statuses não chamam `handle_message`.
- `ruff check app tests`; `mypy app`.

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-25 — Despacho de saída (`outbox.dispatch`)

**Files:**
- Modify: `backend/app/services/outbox.py` (acrescenta `dispatch` e `set_reply_policy`; mantém `enqueue` de PF-19)
- Create: `backend/tests/integration/test_outbox_dispatch.py`

**Depends on:** PF-09, PF-17, PF-19, PF-22

**Escopo (4.4.7):** claim atômico; **relê o status do usuário no banco** e exige `status = required_status` (diferente → `discarded` com `revoked_before_send`/`status_changed`, rastro `decision='revoked_before_send'`); consulta `ReplyPolicy.allow` (negado → `discarded` `paused|brand_disabled`, `send_result='discarded'`, sem alerta); envia pelo `Channel`; atualiza `outbound_messages` e o rastro (`reply_wa_message_id`, `reply_sent_at`, `latency_ms`, `send_result`). Tentativas: **no máximo 2**, a segunda só se a requisição comprovadamente não saiu (`ConnectError`/`ConnectTimeout`) ou 429/503; `ReadTimeout` = `unknown` (não reenvia); 4xx = `failed` e `decision='send_failed'`. Política padrão `AllowAllPolicy`.

**Acceptance:** AC-23, AC-22, AC-30, AC-33 (erro no rastro, sem retentativa infinita), R4.

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_outbox_dispatch.py -q` (db, `FakeChannel`): revogação entre enfileirar e enviar descarta; pausa descarta; isenção de consentimento; timeout ambíguo não reenvia; 429 reenvia uma vez; claim duplicado não envia duas vezes; 4xx marca `send_failed`.
- `ruff check app tests`; `mypy app`.

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-26 — Administração de usuários: `register` e revogação `[P]`

**Files:**
- Modify: `backend/app/services/users.py` (acrescenta `register`, `revoke`, `UserRow`; mantém `validate_row` de PF-12)
- Create: `backend/tests/integration/test_users_admin.py`

**Depends on:** PF-11, PF-12, PF-19, PF-21

**Escopo (R2):** `register(conn, data, *, actor, actor_type="cli", enqueue_welcome=True) -> UserRow` na transação de quem chama: cria `pending_optin` (`valid_until` nulo), chama `optin.issue_notice`, audita; duplicata (`phone_key`) → `DuplicatePhone`; `revoke(conn, user_id, reason, actor)`: `revoked` com autor e data, **idempotente**, motivo da lista fixa, audita. **O painel chama estas funções diretamente** (assinaturas congeladas nesta tarefa).

**Acceptance:** AC-15, AC-16 (lote = uma transação), AC-17 (boas-vindas só como modelo), AC-34, R2.

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_users_admin.py -q` (db): cadastro cria usuário + aviso + linha de saída + auditoria numa transação; duplicata rejeitada sem efeitos; lote com erro na 2ª linha não grava nada; revogar duas vezes = uma auditoria de mudança; motivo inválido recusado.
- `ruff check app tests`; `mypy app`.

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-27 — Pipeline por mensagem, parte 1 (idempotência, identidade, acesso, silêncio) `[P]`

**Files:**
- Create: `backend/app/services/pipeline.py` (parte 1), `backend/tests/integration/test_pipeline_core.py`

**Depends on:** PF-13, PF-14, PF-16, PF-20, PF-22

**Escopo (4.4.1 passo 6.1–6.3, 6.6 e 5, 8):** implementa `PayloadProcessor`: `handle_message(msg)` em **uma transação por mensagem**: `trace.begin` (duplicata = fim sem efeito) → `identity.resolve` (trava a linha) → `access.decide` → tratadores de **`unknown`, `declined`, `revoked`, `identity_conflict`** (tentativa registrada, sem resposta, **sem alerta**; conflito cria o alerta `identity_conflict` com dedup 24 h) → `trace.finalize` → commit. `handle_status(st)`: ignora `sent/delivered/read`; `failed` de envio do Jarvis atualiza `outbound_messages` e o rastro de origem (o alerta de falha repetida é de PF-30). Erro não transitório em uma mensagem: log ERROR com `request_id`, rastro `error` em transação separada, 200 (Q22); transitório sobe como `TransientDbError`. Deixa ganchos claros (`NotImplemented` explícito coberto por teste) para `active`/`pending_optin`/mídia, concluídos em PF-32.

**Acceptance:** AC-5, AC-7, AC-8, AC-10, AC-11, AC-12, AC-14, AC-22, AC-27, AC-31, AC-32 (parte).

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_pipeline_core.py -q` (db): desconhecido = silêncio + tentativa + zero alerta/entrega/saída; revogado/recusado idem; reenvio do mesmo `message_id` sem efeito; BSUID de outro usuário = conflito + alerta; exatamente 1 rastro por mensagem; `FakeChannel` sem nenhuma chamada.
- `ruff check app tests`; `mypy app`.

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-28 — Aplicação FastAPI: esqueleto, `/health` e CI `[P]`

**Files:**
- Create: `backend/app/main.py`, `backend/tests/integration/test_health.py`, `.github/workflows/ci.yml`

**Depends on:** PF-02, PF-03, PF-04, PF-06

**Escopo:** `create_app()` com lifespan (pool, fecha clientes), `TrustedHost`, headers (`nosniff`, HSTS, `Cache-Control: no-store`), docs/redoc desligados, `request_id`, handlers de erro sem traceback, `GET /health` (`SELECT 1` + `migrate.is_up_to_date`; 200 `{"status":"ok"}` / 503 `{"status":"degraded"}`, sem versão). **Ainda sem webhook, pipeline, workers nem política** (o `main.py` é completado em PF-38; a tarefa dona atual é esta). `ci.yml`: **Postgres 16** como service container (a produção é 16, o dev local é 18), Python 3.12, os quatro comandos de verificação (pytest, ruff, mypy, `migrate --check`); é só um arquivo (sem remoto, não roda; ver Q25).

**Acceptance:** NFR de segurança/observabilidade (spec 5), `/health` (4.3.1).

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_health.py -q` (db para o caso com banco; o caso "banco indisponível = 503" roda sem banco).
- Revisão manual do YAML do CI (não instalar dependência só para validar).
- `ruff check app tests`; `mypy app`.

**Estimate:** 2 h  **Status:** `[x]`

---

### PF-29 — Ponto de extensão do cérebro (`answering`) `[P]`

**Files:**
- Create: `backend/app/services/answering.py`, `backend/tests/integration/test_answering.py`

**Depends on:** PF-19

**Escopo (4.3.2):** `AnswerContext`, `Answer`, `AnswerProvider` (Protocol), `UnderConstructionProvider` (devolve `Answer("under_construction", TEXT_UNDER_CONSTRUCTION)`), `answer_and_enqueue(...)`: **fora de transação** chama o provedor para um texto de usuário `active`, depois uma transação curta grava a linha de `outbound_messages` e atualiza `reply_kind` do rastro. Chamado só para texto `allowed`. Sem modelo nem busca.

**Acceptance:** AC-30, AC-31 (provedor só é chamado após `allowed`), spec 4.3.2 (regra do ponto de extensão).

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_answering.py -q` (db): provedor padrão enfileira o texto fixo; `SpyAnswerProvider` recebe o contexto completo (perfil, UFs, canais, marcas); falha do provedor não perde o rastro.
- `ruff check app tests`; `mypy app`.

**Estimate:** 2 h  **Status:** `[x]`

---

### PF-30 — Despacho de saída: recuperação e alerta de falha repetida `[P]`

**Files:**
- Modify: `backend/app/services/outbox.py` (acrescenta `recover_stuck`, `check_send_failures`; dono sequencial depois de PF-25)
- Create: `backend/tests/integration/test_outbox_recovery.py`

**Depends on:** PF-16, PF-25

**Escopo (4.4.6–4.4.7):** `recover_stuck(conn, now)`: `sending` há > 2 min → `unknown` (sem reenviar); rastro `reply_kind='pending'` há > 5 min → `send_result='abandoned'`; a recuperação de entregas `sending` > 5 min fica em PF-34. Em cada falha definitiva (exceto boas-vindas): conta falhas dos últimos `SEND_FAILURE_WINDOW_MINUTES` (30) e, com `≥ SEND_FAILURE_ALERT_THRESHOLD` (3), `raise_alert('send_failure', high, dedup global 1 h)`. Falha da boas-vindas: usuário permanece `pending_optin`, `failed`, alerta `welcome_failed` (high; dedup 1 h por usuário) com e-mail; sem fallback de texto livre.

**Acceptance:** AC-17 (falha mantém `pending_optin`, alerta com e-mail, permite reenvio), AC-33.

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_outbox_recovery.py -q` (db): 2 falhas não alertam, a 3ª sim (1 alerta/h); boas-vindas falha = alerta `welcome_failed` e usuário segue `pending_optin`; `sending` antigo vira `unknown` e **não** é reenviado.
- `ruff check app tests`; `mypy app`.

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-31 — Administração: reenvio/reativação e troca de número `[P]`

**Files:**
- Modify: `backend/app/services/users.py` (acrescenta `resend`, `swap_number`; dono sequencial depois de PF-26)
- Create: `backend/tests/integration/test_users_resend_swap.py`

**Depends on:** PF-26

**Escopo (4.3.3, 4.4.3 item 5):** `resend(conn, user_id, actor)`: `pending_optin`/`declined`/`revoked` → novo aviso (`issue_notice`), usuário em `pending_optin`; `revoked` zera `accepted_at`/`revoked_*` (valores antigos vão para `audit_log.old_value`); `active` é recusado com erro tipado e mensagem clara. `swap_number(conn, from_phone, to_phone, actor)`: **na mesma transação** revoga A (`number_change`), cria B `pending_optin` com os mesmos dados e novo aviso; falha em qualquer passo desfaz tudo.

**Acceptance:** AC-35, AC-26, AC-19.

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_users_resend_swap.py -q` (db): reativar revogado não dá acesso até novo Aceito; aviso anterior `superseded`; `active` recusa; troca atômica (B duplicado desfaz a revogação de A).
- `ruff check app tests`; `mypy app`.

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-32 — Pipeline por mensagem, parte 2 (usuário ativo, opt-in, limite, mídia)

**Files:**
- Modify: `backend/app/services/pipeline.py` (completa os tratadores; dono sequencial depois de PF-27)
- Create: `backend/tests/integration/test_pipeline_active.py`

**Depends on:** PF-18, PF-21, PF-27, PF-29, PF-31

**Escopo (4.4.1 passo 6.4–6.5, 4.4.2, 4.4.5):** `pending_optin` (Aceito/Não aceito do aviso aberto; texto/mídia/botão antigo = lembrete 1/24h ou silêncio, nada de conteúdo, nada baixado); `active`: limite (`limits`; 61ª = `rate_limited`, aviso único por janela, alerta `rate_limit` 1/h com e-mail); `sair` (`proactive_opt_out`, confirmação, corpo guardado); texto `allowed` (`counted_in_limit`, corpo guardado, `reply_kind='pending'`, `answer_request` no `MessageOutcome` para pós-commit); mídia → `tickets.create_media_ticket` + `media_ack` se `MEDIA_ACK_ENABLED`, **sem alerta, sem e-mail**; `ignored_type`. `MessageOutcome` devolve ids de saída/entrega para o despacho pós-commit.

**Acceptance:** AC-18, 19, 20, 21, 28, 29, 30, 36, AC-31 (nada que custa antes de `allowed`), AC-32.

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_pipeline_active.py -q` (db, `FakeChannel` + `SpyAnswerProvider`): aceite ativa; texto no lugar do botão não aceita; 61ª bloqueada com um aviso e um alerta; "sair" liga opt-out; áudio gera 1 ticket, 0 alertas, 0 e-mails, 1 `media_ack`, nada baixado; o provedor só é chamado para texto `allowed`.
- `ruff check app tests`; `mypy app`.

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-33 — CLI, parte 1: `user add|import|revoke|list` e `flag` `[P]`

**Files:**
- Create: `backend/app/cli.py` (parte 1), `backend/tests/integration/test_cli_users.py`

**Depends on:** PF-11, PF-26

**Escopo (4.3.3):** `python -m app.cli`, fino sobre a camada de serviço: `user add` (8 campos; rejeita indicando o campo; espera até 20 s o resultado do envio e imprime `enviado`/`falhou: <motivo>`/`na fila`), `user import --file x.csv [--dry-run]` (cabeçalho PT-BR, delimitador `,` ou `;`, UTF-8 com BOM, listas por `|`; **tudo ou nada**, erro lista linha, campo e motivo), `user revoke (--phone|--id) --reason`, `user list [--status]` (telefone mascarado), `flag get|set team-group on|off`, `flag set paused on|off`, `flag set brand <marca> on|off`. O CLI **não envia nada** (enfileira e consulta); `--by` assume o usuário do SO; saídas sem PII além do telefone mascarado. Lê `DATABASE_URL` de `backend/.env` via `Settings`.

**Acceptance:** AC-15, AC-16, AC-17 (mensagem de falha do envio), AC-34, AC-13 (chave do grupo), R4 (flags).

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_cli_users.py -q` (db): cadastro completo e com campo faltando; CSV válido, CSV com erro na linha 3 (nada gravado, mensagem com linha/campo/motivo), `--dry-run` não grava; revogar; `flag set paused on` grava e audita.
- `.venv\Scripts\python -m app.cli --help` lista os comandos.
- `ruff check app tests`; `mypy app`.

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-34 — Laços de fundo (`workers`) `[P]`

**Files:**
- Create: `backend/app/services/workers.py`, `backend/tests/integration/test_workers.py`

**Depends on:** PF-15, PF-23, PF-30

**Escopo:** laço de varredura (intervalo configurável, relógio injetável) que: despacha `outbound_messages` e `deliveries` vencidos; executa `outbox.recover_stuck` e a recuperação de entregas (`sending` > 5 min volta a `pending`); `counters.flush` a cada 30 s; recuperação executada também **na subida**; encerra limpo no shutdown. Expõe `start(app)`/`stop()` para o lifespan (a ligação é de PF-38). Exceção em um ciclo é logada e o laço continua.

**Acceptance:** AC-37 (retentativa de e-mail), AC-13 (retentativa do grupo), AC-33, 4.4.7 (recuperação), 4.6 (contadores em lote).

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_workers.py -q` (db, relógio falso): um ciclo despacha o vencido e ignora o não vencido; trava `sending` é recuperada; exceção não mata o laço; flush grava contadores.
- `ruff check app tests`; `mypy app`.

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-35 — CLI, parte 2: `user resend` e `user swap-number` `[P]`

**Files:**
- Modify: `backend/app/cli.py` (acrescenta os dois comandos; dono sequencial depois de PF-33)
- Create: `backend/tests/integration/test_cli_resend_swap.py`

**Depends on:** PF-31, PF-33

**Escopo:** `user resend (--phone|--id)` (usa `users.resend`; `active` recusa com mensagem clara e código de saída ≠ 0), `user swap-number --from A --to B` (usa `users.swap_number`; atômico), ambos imprimindo o resultado do envio como no `user add`.

**Acceptance:** AC-35, AC-26.

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_cli_resend_swap.py -q` (db).
- `ruff check app tests`; `mypy app`.

**Estimate:** 2 h  **Status:** `[x]`

---

### PF-36 — Testes de aceite via pipeline: acesso, idempotência, concorrência, limite `[P]`

Somente testes (a implementação já existe; falha encontrada vira correção **na tarefa dona do arquivo**, com emenda a esta lista).

**Files:**
- Create: `backend/tests/integration/test_pipeline_access.py`, `backend/tests/integration/test_idempotency.py`, `backend/tests/integration/test_rate_limit.py`

**Depends on:** PF-32

**Escopo:** cenários ponta a ponta contra o `PayloadProcessor` real e Postgres real (conexões reais do pool, sem mocks de banco): AC-10/11/12/14/27 (zero alerta, zero e-mail, zero entrega, zero resposta para desconhecido/revogado/recusado; 1ª mensagem guardada só com a chave ligada e nunca sobrescrita); AC-7 (duplicata sequencial e **duplicata simultânea**: um rastro, um efeito); AC-29 (60 passam; **61ª simultânea** não passa; aviso único por janela; alerta 1/h; bloqueadas não prolongam a janela; duplicatas e statuses não contam); AC-22 (a mensagem seguinte à revogação já é bloqueada).

**Acceptance:** AC-7, 10, 11, 12, 14, 22, 27, 29.

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_pipeline_access.py tests/integration/test_idempotency.py tests/integration/test_rate_limit.py -q` (db).

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-37 — Testes de aceite via pipeline: opt-in, revogação, resposta e mídia `[P]`

Somente testes.

**Files:**
- Create: `backend/tests/integration/test_optin.py`, `backend/tests/integration/test_revocation.py`, `backend/tests/integration/test_replies_media.py`

**Depends on:** PF-32, PF-30

**Escopo:** AC-17/18/19/20/21 (modelo, aceite, botão antigo, recusa, texto no lugar do botão, lembrete 1/24h); AC-22/23 (revogar com resposta em preparação: **hook que revoga entre o commit e o envio**, resposta descartada `revoked_before_send`); AC-26 e AC-35 (troca de número, reativação); AC-28 (`sair`); AC-30 (texto fixo, sem modelo, corpo guardado); AC-36 (áudio/imagem: ticket em `chat.tickets`, sem alerta, sem e-mail, `media_ack`, nada baixado, `escalated_media`; com `MEDIA_ACK_ENABLED=false`, `reply_kind='none'`).

**Acceptance:** AC-17–23, 26, 28, 30, 35, 36.

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_optin.py tests/integration/test_revocation.py tests/integration/test_replies_media.py -q` (db).

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-38 — Ligação da aplicação (`main.py`) e teste ponta a ponta do webhook

**Files:**
- Modify: `backend/app/main.py` (inclui o router do webhook, instancia `Pipeline` como `PayloadProcessor`, instala `FlagsReplyPolicy` via `outbox.set_reply_policy` no lifespan, inicia/encerra os `workers`, cria os canais e clientes httpx e os fecha)
- Create: `backend/tests/integration/test_app_e2e.py`

**Depends on:** PF-24, PF-28, PF-32, PF-34, PF-17

**Escopo:** montagem sem lógica nova. Teste ponta a ponta com `TestClient`, Postgres real, `FakeChannel` e `MockTransport`: POST assinado de texto de usuário `active` → 200 rápido → resposta "em construção" despachada depois do commit; POST de desconhecido → 200 e nenhuma chamada de saída; 503 em erro transitório; `/health` com migrations em dia; pausa ligada via `flags` interrompe a resposta; reinício (novo `create_app`) recupera pendências.

**Acceptance:** AC-3, AC-30, AC-31, AC-1..4 (integração com o app real), R4 (política instalada no lifespan), NFR "200 < 1 s".

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_app_e2e.py tests/integration/test_health.py tests/integration/test_webhook_http.py -q` (db).
- `.venv\Scripts\python -m app.db.migrate --check` com banco de teste; `ruff check app tests`; `mypy app`.
- **Gate de entrega ao painel:** painel.1 pode integrar `main.py` somente depois desta tarefa.

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-39 — Suíte de segurança e intrusão `[P]`

**Files:**
- Create: `backend/tests/security/test_signature.py`, `backend/tests/security/test_order.py`, `backend/tests/security/test_zero_cost.py`, `backend/tests/security/test_replay_intrusion.py`

**Depends on:** PF-38

**Escopo (4.7, "zero custo"):** assinatura inválida/ausente/mal formada/não ASCII sem parse, sem banco, sem rastro, sem resposta (AC-2); **ordem** tamanho → assinatura → idempotência → remetente → acesso → limite com `FakeChannel` e espiões registrando a sequência (AC-31); teste de arquitetura que varre o código-fonte e falha se algum módulo referenciar download de mídia da Graph API; `AnswerProvider` espião só chamado para texto `allowed`; replay (mesmo payload assinado duas vezes = um efeito); inundação de números desconhecidos não gera alerta, e-mail, grupo nem custo; payload de outro `phone_number_id`; verify token errado nunca ecoa o challenge. Marcador `security`.

**Acceptance:** AC-2, AC-4, AC-10, AC-12, AC-31, AC-36 (nada baixado), NFR de segurança (critério de pronto do épico).

**Verification:**
- `.venv\Scripts\python -m pytest tests/security -q -m security` (db).

**Estimate:** 4 h  **Status:** `[x]`

---

### PF-40 — Testes de rastro, logs sem PII e e-mail sem PII `[P]`

**Files:**
- Create: `backend/tests/integration/test_trace_logs.py`, `backend/tests/integration/test_email_privacy.py`

**Depends on:** PF-38

**Escopo:** AC-32 (exatamente 1 rastro por mensagem não duplicada, com ids, tipo, decisão e motivo, resposta ou `none`, latência, resultado do envio; corpo só de autorizado; logs capturados durante um fluxo completo não contêm telefone completo, BSUID, corpo, e-mail do destinatário nem segredo); AC-37 (e-mail novo alerta imediato, sem bloquear o webhook, sem texto de mensagem; tentativas de acesso e tickets não geram e-mail; falha de e-mail logada sem PII e retentada); AC-13 (grupo desligado = zero chamadas; ligado = envio; WAHA com falha mantém o registro).

**Acceptance:** AC-13, AC-32, AC-37.

**Verification:**
- `.venv\Scripts\python -m pytest tests/integration/test_trace_logs.py tests/integration/test_email_privacy.py -q` (db).

**Estimate:** 3 h  **Status:** `[x]`

---

### PF-41 — Fechamento: comandos de verificação, STATE, mapa AC → teste

**Files:**
- Modify: `.claude/CLAUDE.md` (somente a seção "Verification commands"), `docs/STATE.md` (entradas AD/L), `docs/tasks/porta-fechada.1/spec/spec.md` (somente 4.7: nomes finais dos testes; manter o frontmatter)
- Create: nenhum

**Depends on:** PF-36, PF-37, PF-39, PF-40, PF-35

**Escopo:** registrar os quatro comandos canônicos (`pytest`, `ruff`, `mypy`, `migrate --check`) no CLAUDE.md; rodar a suíte inteira; produzir a tabela AC → arquivo de teste definitiva (todo AC com ao menos um teste automatizado, exceto envio real do modelo e do e-mail) e atualizar 4.7 da spec; registrar em STATE.md o que ficou pendente do dono (Q8, Q12, Q17, DNS). **Não** modificar `docs/prd.md` nem `docs/architecture/` (correção do PRD §10 sobre mídia exige ADR/decisão do Caio, apenas listar).

**Acceptance:** Definition of Done do épico (comandos de verificação registrados; ACs cobertos).

**Verification:**
- `.venv\Scripts\python -m pytest tests -q`, `ruff check app tests`, `mypy app`, `python -m app.db.migrate --check` (com banco de teste) todos verdes; relatar quantos testes `db` foram pulados (deve ser zero com o `.env` preenchido).

**Estimate:** 2 h  **Status:** `[x]`

---

### PF-42 — Artefatos de deploy (somente arquivos): PM2 e nginx `[P]`

Onda final **opcional**. Entrega arquivos versionados; **nenhum comando é executado no servidor** (Caio adiou o deploy até funcionar).

**Files:**
- Create: `deploy/ecosystem.config.js`, `deploy/nginx/jarvis.conf.template`

**Depends on:** PF-41

**Escopo (4.10):** PM2 `jarvis` (uvicorn `--workers 1 --no-access-log` — o log de acesso do uvicorn grava a query string, e a verificação da Meta leva o `hub.verify_token` nela (visto no teste local de 2026-10-27), `127.0.0.1:<porta>`, `max_memory_restart: 300M`, `restart_delay: 3000`, logs em `/var/log/jarvis/`); porta proposta 8010 **marcada como "confirmar com `ss -ltnp`"** (Q24); template nginx (TLS pelo mesmo mecanismo do AUTO-1 a confirmar, `proxy_set_header X-Real-IP $remote_addr`, `client_max_body_size 64k`, `proxy_read_timeout 15s`, apenas `location = /webhook/whatsapp` e `= /health`, `location /` 404). Placeholders para domínio, porta e caminhos; nenhum segredo.

**Acceptance:** spec 4.10 (arquivos entregues; aplicação ao servidor fora do escopo).

**Verification:**
- `node -e "require('./deploy/ecosystem.config.js')"` carrega sem erro (se Node disponível; o piloto usa Node) ou revisão manual.
- Revisão: nenhum IP, senha ou token no arquivo além do placeholder.

**Estimate:** 2 h  **Status:** `[ ]`

---

### PF-43 — Runbook e checklist de go-live (somente documento) `[P]`

**Files:**
- Create: `deploy/RUNBOOK.md`

**Depends on:** PF-41

**Escopo:** passo a passo de 8.2 com **"confirmar com o Caio" antes de cada comando no servidor compartilhado** (banco e roles `jarvis_owner`/`jarvis_app` e GRANTs, **Postgres 16 no servidor**, `/opt/jarvis`, `.env` modo 600, `pm2 start`, `migrate --status` e `migrate`, `nginx -t` + `reload` nunca restart, callback e campo `messages` na Meta, roteiro de smoke de 7), reversão (seção 9), parada de emergência, backlog de reentregas, chaves que continuam desligadas (`UNKNOWN_FIRST_TEXT_STORE`, `TEAM_GROUP_ENABLED`). Sem executar nada.

**Acceptance:** spec 8.2 e 9.

**Verification:**
- Revisão cruzada com 4.10, 8.2 e 9 (cada item presente; nenhum comando destrutivo sem aviso).

**Estimate:** 2 h  **Status:** `[ ]`

---

### PF-44 — (ADIADA, fora das ondas) Go-live e smoke test manual

M9 da spec. **Não é implementada pela Fase 4** e não conta nas ondas: depende de DNS, banco e deploy no droplet (adiados pelo Caio), do modelo de boas-vindas aprovado e pago (Q8) e de Q12. Quando o Caio liberar, executa o `deploy/RUNBOOK.md` e o roteiro manual de 7 (cadastro, boas-vindas, Aceito, Não aceito, texto no lugar do botão, revogação e reativação, "sair", áudio, estouro do limite, número não cadastrado). Estimativa 4–6 h, executor humano com apoio.

**Status:** `[ ]` (bloqueada por decisão do dono)

---

## Parallel execution map

Regras aplicadas: no máximo 5 tarefas por onda; nenhuma dependência entre tarefas da mesma onda; nenhum arquivo em comum (incluindo testes). Os únicos arquivos escritos por mais de uma tarefa (`users.py`, `outbox.py`, `pipeline.py`, `cli.py`, `main.py`) são sempre sequenciais, em ondas diferentes, com dono declarado.

| Onda | Tarefas | Pré-requisito (ondas anteriores) | Horas (soma) |
|---|---|---|---|
| **1** | PF-01 | — | 4 |
| **2** | PF-02, PF-03, PF-04, PF-05 | PF-01 | 14 |
| **3** | PF-06, PF-07, PF-08, PF-09, PF-10 | PF-02..05 | 18 |
| **4** | PF-11, PF-12, PF-13, PF-14, PF-15 | PF-03..08 | 16 |
| **5** | PF-16, PF-17, PF-18, PF-19, PF-20 | PF-06, PF-07, PF-11 | 13 |
| **6** | PF-21, PF-22, PF-23, PF-24 | PF-08..PF-19 | 14 |
| **7** | PF-25, PF-26, PF-27, PF-28, PF-29 | PF-13..PF-22 | 16 |
| **8** | PF-30, PF-31, PF-32, PF-33 | PF-25..PF-29 | 13 |
| **9** | PF-34, PF-35, PF-36, PF-37 | PF-30..PF-33 | 13 |
| **10** | PF-38 | PF-24, PF-28, PF-32, PF-34 | 3 |
| **11** | PF-39, PF-40 | PF-38 | 7 |
| **12** | PF-41 | PF-35..PF-40 | 2 |
| **13 (opcional, só arquivos)** | PF-42, PF-43 | PF-41 | 4 |

**Total:** 43 tarefas em 13 ondas; **127 h** (123 h até a onda 12, mais 4 h do deploy opcional). PF-44 (smoke manual, 4–6 h) fica fora.

Verificação de arquivos disjuntos por onda (resumo):

- Onda 2: `config.py`+modelo / `core/*` / `db/*`+`tests/db_support.py`+conftests de integration e security / `phone.py`.
- Onda 3: migrations 001–003 / migrations 004–005 / `inbound.py`+fixtures / `channel.py`+`whatsapp.py`+`tests/fakes.py` / `notifications.py`+`waha.py`.
- Onda 4: `audit.py`+`flags.py` / `users.py` (apenas `validate_row`) / `access.py`+`replies.py` / `identity.py` / `counters.py`+`rate_limit.py`.
- Onda 5: `alerts.py` / `reply_policy.py` / `tickets.py` / `outbox.py` (enqueue) / `attempts.py`+`limits.py`.
- Onda 6: `optin.py` / `trace.py` / `deliveries.py` / `api/webhook.py`.
- Onda 7: `outbox.py` (dispatch) / `users.py` (register, revoke) / `pipeline.py` (parte 1) / `main.py` (esqueleto)+`ci.yml` / `answering.py`.
- Onda 8: `outbox.py` (recuperação) / `users.py` (resend, swap) / `pipeline.py` (parte 2) / `cli.py` (parte 1).
- Onda 9: `workers.py` / `cli.py` (parte 2) / 3 arquivos de teste / 3 arquivos de teste (todos distintos).
- Onda 10: `main.py` (ligação) + `test_app_e2e.py`.
- Onda 11: `tests/security/*` / `test_trace_logs.py`+`test_email_privacy.py`.
- Onda 12: `.claude/CLAUDE.md`, `docs/STATE.md`, seção 4.7 da spec.
- Onda 13: `deploy/ecosystem.config.js`+`deploy/nginx/...` / `deploy/RUNBOOK.md`.

Observação sobre sequências dentro do mesmo arquivo: `outbox.py` (PF-19 → PF-25 → PF-30), `users.py` (PF-12 → PF-26 → PF-31), `pipeline.py` (PF-27 → PF-32), `cli.py` (PF-33 → PF-35), `main.py` (PF-28 → PF-38). Cada uma só inicia depois da anterior estar mesclada (dependência declarada).

Com `sdd-waves` (11+ tarefas em muitos arquivos): rodar por ondas; ao fim de cada onda a suíte inteira (`pytest tests -q`, ruff, mypy) precisa estar verde antes da próxima. A onda 1 depende de uma ação do Caio (rodar o script de criação dos papéis e preencher `backend/.env`).

## Decisões do dono necessárias antes/durante a Fase 4

1. **PF-01 (ação sua, ~5 min):** rodar `backend/scripts/dev_db_setup.sql` com o superusuário `postgres` (o assistente não tem essa senha) e definir as senhas de `jarvis` e `jarvis_test`; depois copiar `backend/configuracao-modelo.txt` para `backend/.env` e preencher `DATABASE_URL` e `TEST_DATABASE_URL`. Sem isso as tarefas `db` ficam com testes pulados.
2. **Python 3.14 × versões fixadas — resolvido (2026-10-23):** as bibliotecas já estavam instaladas no Python 3.14 do Caio ("já tem tudo instalado"). O venv usa `--system-site-packages` (nada baixado) e os requirements fixam as versões instaladas: fastapi 0.115.6, uvicorn 0.34.0, asyncpg 0.31.0, pydantic-settings 2.7.1, httpx 0.28.1; dev: pytest 9.0.3, pytest-asyncio 1.3.0, ruff 0.15.15, mypy 2.3.1. O código segue compatível com 3.12 (ruff `py312`, mypy `3.12`).
3. **Remoto Git / CI (Q25):** `ci.yml` (PF-28) é apenas um arquivo; sem remoto GitHub ele não roda. Confirmar se haverá remoto.
4. **Capturas reais da Meta (Q5):** as fixtures de PF-08 nascem da documentação; trocar por payloads do número de teste exige que o Caio envie mensagens (texto, botão, áudio, imagem) e que alguém capture o webhook. Pode ocorrer a qualquer momento.
5. **Deploy (ondas 13 e PF-44):** continuam adiados, sem alteração no servidor; PF-42/43 só geram arquivos.

## Cobertura de ACs (resumo; tabela definitiva em PF-41)

| AC | Tarefas |
|---|---|
| 1–4 | PF-24, PF-38, PF-39 |
| 5, 6 | PF-08, PF-24, PF-27 |
| 7 | PF-06, PF-22, PF-36 |
| 8, 9 | PF-05, PF-14, PF-27 |
| 10–12, 14, 27 | PF-13, PF-20, PF-27, PF-36 |
| 13 | PF-02, PF-10, PF-11, PF-23, PF-40 |
| 15, 16 | PF-12, PF-26, PF-33 |
| 17 | PF-09, PF-21, PF-30, PF-37 |
| 18–21 | PF-13, PF-21, PF-32, PF-37 |
| 22, 23 | PF-25, PF-36, PF-37 |
| 26, 34, 35 | PF-26, PF-31, PF-35, PF-37 |
| 28 | PF-13, PF-32, PF-37 |
| 29 | PF-20, PF-32, PF-36 |
| 30, 36 | PF-18, PF-29, PF-32, PF-37 |
| 31 | PF-13, PF-24, PF-39 |
| 32 | PF-22, PF-27, PF-40 |
| 33 | PF-09, PF-25, PF-30 |
| 37 | PF-10, PF-16, PF-23, PF-40 |
| R1–R4 | PF-07/PF-18, PF-12/PF-26, PF-16, PF-11/PF-17 |

## Definition of Done

- Todas as tarefas PF-01..PF-41 marcadas `[x]` (PF-42/43 opcionais; PF-44 adiada por decisão do dono).
- `pytest tests -q`, `ruff check`, `mypy app` e `python -m app.db.migrate --check` verdes; testes `db` executados contra o Postgres local (banco `jarvis_test`) ou no CI (se ficarem pulados, o fechamento do épico não pode ser declarado).
- Todos os ACs ativos com ao menos um teste automatizado, exceto envio real do modelo e do e-mail (manuais, PF-44).
- Revisão de código (`code-reviewer`) e revisão de segurança (`security-reviewer`) concluídas, a segunda cobrindo webhook, HMAC, PII em logs, e-mail e CLI.
- Nenhuma migration ou SQL usa recurso exclusivo do PostgreSQL 17/18 (produção é 16).
- Spec atualizada em qualquer divergência (4.7 pelo menos); `docs/STATE.md` atualizado.
- Nenhuma alteração no servidor compartilhado; nenhum arquivo `.env*` lido pelos agentes nem versionado.

## Notas de implementação para as próximas tarefas (ondas 1–5, 2026-10-23)

Contratos que saíram mais precisos que o texto das tarefas; quem chama deve seguir o código:

- `identity.resolve(conn, sender: inbound.Sender) -> Identity` (`kind`, `state: access.SenderState`, `user_id`, `phone_key`, `bsuid`); `access.decide(identity.state, msg, now)`.
- `tickets.create_media_ticket(conn, user_id, trace_id, msg_type, *, flags, clock=None)`: `flags` é obrigatório.
- `alerts.raise_alert(..., *, flags=None, settings=None, clock=None)`: passar `flags` e sempre uma `dedup_window` quando houver `dedup_key`.
- `reply_policy.FlagsReplyPolicy.brand_allows` é `async`.
- `limits.rate_limit_notice_due(last_notice_at, now, *, window_minutes)` recebe o horário do último aviso, não o usuário.
- `limits.check_and_count(conn, user_id, now, *, limit, window_minutes)`: o chamador já segura o `FOR UPDATE` do usuário e grava `counted_in_limit`.
- `attempts.record(..., store_first_text=settings.unknown_first_text_store)`: o serviço não lê `Settings`.
- `Flags.set_bool(..., actor_type="panel")` no painel.
- Nomes iguais: `app.services.channel.SendResult` (dataclass do envio) × `app.core.values.SendResult` (enum do trace). Importar com alias quando usar os dois.
- Testes de banco: helpers locais em cada arquivo; `test_schema_ops_tickets.py` importa `add_user`/`add_trace` de `test_schema_access_chat.py` (mover para `tests/db_support.py` numa limpeza futura).

### Acrescentado após a onda 6

- `trace.begin` grava `access_decision='error'` provisório; o pipeline sempre chama `trace.finalize` (ou `trace.mark_error`) e `trace.ensure_finalized` antes do COMMIT.
- Webhook (PF-24) lê de `app.state`: `settings`, `counters`, `processor` (`PayloadProcessor`: `handle_message(msg) -> MessageOutcome`, `handle_status(st) -> MessageOutcome | None`), opcionais `webhook_limiter` e `outbound_dispatcher(outbound_ids, delivery_ids)`. **PF-38 precisa ligar `outbound_dispatcher`** a `outbox.dispatch` + `deliveries.dispatch`; sem ele nada é enviado.
- Eventos de log do webhook além de 4.6: `webhook_received`, `webhook_payload_too_large`, `webhook_signature_invalid`, `webhook_wrong_recipient`, `webhook_processing_error` (registrar na spec 4.6 em PF-41). Exceções do processador vão para `logger.exception`: o pipeline nunca pode colocar texto da mensagem em mensagens de exceção.
- `deliveries.dispatch(pool, delivery_id, *, flags, ...)` não filtra `next_attempt_at`: a varredura de reenvio filtra `next_attempt_at <= now`.
- Modelo de boas-vindas no outbox (PF-21): JSON `{name, language, body_params, quick_reply_payloads}`; PF-25 converte listas em `tuple` antes de montar `channel.TemplateMessage` e falha alto se `name` estiver vazio (`WELCOME_TEMPLATE_NAME`).
- `optin.issue_notice` não muda `users.status`: quem chama (`register`, `resend`, `swap_number`) põe `pending_optin` e zera `optin_reminder_at` na mesma transação.
- `optin.*` recebem `settings=` obrigatório (versão do aviso, textos, modelo).

### Acrescentado após a onda 7

- Assinaturas finais: `users.register(conn, data, *, actor, actor_type="cli", enqueue_welcome=True, settings=None, clock=None)` (com boas-vindas, `settings` é obrigatório) e `users.revoke(conn, user_id, reason, actor, *, actor_type="cli", clock=None)`. Spec do painel.1 corrigida.
- `Pipeline(pool, settings, flags, clock=None)`: decisões sem tratador (`pending_optin`, `optin_*`, `allowed`, `rate_limited`, `escalated_media`, `ignored_type`) levantam `NotImplementedError` até PF-32 registrar o tratador em `Pipeline._handlers`.
- `outbox.dispatch(pool, id, *, channel, policy=None, clock=None)` levanta `InvalidValue` em conteúdo inválido (linha já `failed`): o `outbound_dispatcher` de PF-38 captura. PF-30: boas-vindas que falham assim também geram `welcome_failed`.
- `answering.answer_and_enqueue(pool, AnswerRequest, provider)` só enfileira e devolve o id: PF-38 chama `outbox.dispatch` nele.
- `app.main.create_app()` (sem `app` no módulo): subir com `uvicorn app.main:create_app --factory`; TrustedHost aceita `*.positiveco.com.br`, `localhost`, `127.0.0.1` — o nginx repassa o `Host` (PF-42/43).

### Acrescentado após a onda 8

- PF-38 (`outbound_dispatcher`): depois de `outbox.dispatch` devolver `failed` ou levantar `InvalidValue`, chamar `outbox.check_send_failures(conn, id, settings=, flags=)`; sem isso nenhum alerta de falha de envio dispara. `outbox.recover_stuck(conn, now)` roda no laço de fundo e na subida.
- `users.resend(conn, user_id, actor, *, actor_type, settings, clock)` e `users.swap_number(conn, from_phone, to_phone, actor, *, actor_type, settings, clock)`; `UserAlreadyActive` (subclasse de `InvalidValue`) fica em `users.py`.
- Pipeline (PF-32): o limite de 60/h conta toda mensagem aceita de usuário ativo, inclusive reação e tipos ignorados (leitura de AC-29 "mensagens aceitas"); reação de quem ainda não aceitou pode gerar o lembrete (1 por 24 h) — decisão provisória, a confirmar com o Caio.
- CLI (PF-33): `python -m app.cli user|flag ...`; carrega o `Settings` completo, então o `backend/.env` precisa das chaves da WhatsApp (ou PF-35 cria um `Settings` reduzido para a CLI). Código de saída 1 para duplicata em `add` e 2 em `import`.

### Acrescentado após a onda 10

- `create_app(settings=None, *, channel=None, http_client=None)`: injeção só para testes; em produção o lifespan cria e fecha o canal da WhatsApp e o cliente httpx.
- A resposta do cérebro (`answer_request`) roda numa tarefa de fundo criada pelo `AppProcessor`, depois do commit do pipeline e sem atrasar o 200; o encerramento espera essas tarefas (`drain`).
- As entregas varridas pelo laço de fundo usam um cliente httpx novo por entrega (o `Workers` não recebe o cliente compartilhado); só o despacho disparado pelo webhook usa o cliente compartilhado.
- **Gate do painel liberado:** painel.1 pode integrar `main.py` a partir daqui.

### Acrescentado após a onda 11

- Suíte final: 963 testes (inclui 99 de segurança e 29 de privacidade), 0 pulados, ~4 min.
- Spec 4.6 × código: o evento `message_decision` registra `decision`, `user_id`, `msg_type`, `reply_kind` (não `reason`/`latency_ms`); `latency_ms` fica no rastro só quando a resposta é enviada. Reconciliar em PF-41.
- Testes que acessam o banco fora do loop do `TestClient` abrem conexão própria (o pool do app pertence ao loop do `TestClient`).
