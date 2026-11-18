---
task_id: PORTA-FECHADA-2
phase: 4-implementation
status: approved
tasks_approved_by: Caio
tasks_approved_at: 2026-11-18
spec: spec/spec.md
total_tasks: 8
total_waves: 4
estimated_hours: 14
generated: 2026-11-18
---

# Task breakdown - PORTA-FECHADA-2 (Papel da aplicacao sem DDL no banco)

Fonte: `spec/spec.md` (Fase 1 e Fase 2 aprovadas por Caio em 2026-11-18), `spec/requirements.json` (PA-1..PA-14), `spec/complexity.json`, `spec/research.json`, ADR de 2026-11-18 em `docs/architecture/overview.md`. Fase 3 aprovada por Caio em 2026-11-18; Fase 4 concluida em 2026-11-18.

## Convencoes

- **IDs:** `PA-T01..PA-T08` (prefixo `PA-T` para nao colidir com os criterios `PA-n`). Commit: `type(banco): PORTA-FECHADA-2.Txx - descricao`, **feito pelo Caio**. Checkbox: `[ ]` pendente, `[~]` em andamento, `[x]` pronta; `[P]` = paralela na onda.
- **Caminhos** relativos a raiz do repo. `B/` = `backend/`, `BT/` = `backend/tests/`, `D/` = `deploy/`. Comandos a partir de `backend\`.
- **Whitelist:** so os arquivos da coluna "Arquivos". "(se necessario)" = apenas se um teste existente quebrar por causa da mudanca, sem mudar o que ele afirma.
- **Verificacao padrao** de toda tarefa com codigo: o `pytest` indicado + `ruff check app tests` + `mypy app`. Migration soma `migrate --check` contra o banco de teste.
- **Compatibilidade:** Python 3.12 / PostgreSQL 16. Imports absolutos. Nenhuma dependencia nova.
- **Seguranca:** nenhum `.env` lido ou escrito pelo agente; nenhuma senha ou URL em arquivo, log ou mensagem. Nada roda no droplet (o banco de la nao existe; os comandos ficam no runbook).
- **Passo manual do Caio (D3), antes da W1:** rodar o `dev_db_setup.sql` atualizado como `postgres` (o comando vem na T03). Sem ele a suite da W1 em diante falha com a instrucao.

## W0 - Banco e runner

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| PA-T01 `[x]` `[P]` | Migration 402 com `ops.apply_app_grants()` | 2 | - | `B/migrations/402_app_role_grants.sql`, `BT/integration/test_migration_402.py`, `BT/integration/test_migration_*.py` (se necessario: testes que contam funcoes ou migrations) | PA-4..PA-9, PA-11 | `pytest tests/integration/test_migration_402.py -q`: sem o papel, NOTICE e nada quebra; com o papel (pulado ate a T03 rodar), privilegios conforme PA-4..PA-6 e repetir nao muda nada; `migrate --check` |
| PA-T02 `[x]` `[P]` | Runner: `MIGRATION_DATABASE_URL` e `refresh_grants` | 1.5 | - | `B/app/db/migrate.py`, `BT/integration/test_migrate_runner.py`, `BT/unit/test_migrate_*.py` (se houver) | PA-2, PA-10 | `pytest tests/integration/test_migrate_runner.py -q`: URL de migration tem prioridade e vazia cai no `DATABASE_URL`; erro sem a URL no texto; `apply_pending` chama as funcoes de GRANT que existirem, inclusive sem pendentes; `--status`/`--check` nao chamam |
| PA-T03 `[x]` `[P]` | Papel `jarvis_app` no PC e no CI | 1 | - | `B/scripts/dev_db_setup.sql`, `.github/workflows/ci.yml`, `B/DEV_SETUP.md` | PA-1 (dev/CI), D3 | Script idempotente (rodar 2x sem erro, pelo Caio); CI cria `jarvis_app NOLOGIN` e `GRANT jarvis_app TO jarvis_test`; DEV_SETUP explica o passo |

## W1 - App como `jarvis_app`

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| PA-T04 `[x]` | Testes rodam o app como `jarvis_app` | 4 | T01, T02, T03 | `BT/db_support.py`, `BT/conftest.py`, `BT/panel/conftest.py`, `BT/panel/factories.py`, testes que precisam do dono (lista registrada em "Desvios" ao fim da tarefa), `B/migrations/402_app_role_grants.sql` (so se faltar permissao legitima ao app; ainda nao aplicada fora do PC) | PA-3, PA-13 | Primeiro: confirmar que o parametro `role` na conexao vira `current_user = jarvis_app` (senao, plano B). Depois `migrated_database` devolve o DSN com o papel; `database_url` segue dono (migra e TRUNCATE); papel ausente falha com a instrucao. **Suite inteira verde** com o app como `jarvis_app` |
| PA-T05 `[x]` `[P]` | Recusar subida em producao com papel dono | 2 | T01 | `B/app/db/roles.py`, `B/app/main.py`, `BT/integration/test_startup_role.py`, `BT/integration/test_health.py` (se necessario) | PA-12 | `pytest tests/integration/test_startup_role.py -q`: com `PRODUCTION=true`, dono do banco recusa (mensagem com o nome do papel e motivos, sem URL); `jarvis_app` sobe; fora de producao o dono sobe; `role_problems` unitario por motivo |

## W2 - Prova de seguranca

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| PA-T06 `[x]` | Varredura do catalogo e tentativas como `jarvis_app` | 2 | T04 | `BT/security/test_app_role.py` | PA-4..PA-10 | `pytest tests/security/test_app_role.py -q`: toda tabela/visao/sequencia dos 4 schemas com os privilegios certos; CREATE (cada schema e `public`), ALTER, DROP, TRUNCATE, DROP TRIGGER, GRANT, UPDATE/DELETE em `ops.audit_log`, leitura de `assistant.*` e EXECUTE de `ops.apply_app_grants()` recusados com `InsufficientPrivilegeError`; INSERT/SELECT em `audit_log` funcionam; migration de teste nova ganha permissao sozinha |

## W3 - Fechamento

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| PA-T07 `[x]` `[P]` | Runbook e modelo de configuracao | 1 | T02, T05 | `D/RUNBOOK.md`, `B/configuracao-modelo.txt` | PA-1, PA-3, PA-14 | Leitura: passos 2, 5, 6, "Atualizar" e secao 11 com os dois papeis e `MIGRATION_DATABASE_URL`; nenhuma senha no texto |
| PA-T08 `[x]` | Verificacao final e documentacao | 1.5 | todas | `docs/tasks/porta-fechada.2/task.md`, `docs/tasks/porta-fechada.2/spec/spec.md`, `docs/tasks/porta-fechada.2/spec/requirements.json`, `docs/stories/porta-fechada.2.md`, `docs/STATE.md`, `docs/architecture/overview.md` (ADR: proposto -> aceito) | todos | Suite inteira, `mypy app`, `ruff check app tests`, `migrate --check`, `pytest tests/panel -m perf -q`, `eval --mode palavras` APROVADO; STATE sem GRANT manual pendente |

## Desvios do design

1. **`TEST_DATABASE_URL` carrega o papel** (T04): alem do fixture `migrated_database`, o `tests/conftest.py` raiz troca o papel na propria variavel. Muitos testes ponta a ponta leem `os.environ["TEST_DATABASE_URL"]` direto (Settings, `create_app`, CLI); sem isso continuariam subindo o app como dono. O dono so vem por `database_url`, `owner_conn` e `owner_execute` (`tests/db_support.py`), que tiram o papel do DSN.
2. **Nenhuma permissao faltou ao app.** Todas as falhas da primeira rodada (22 falhas, 547 erros) eram do proprio teste fazendo o que so o dono faz. Ajustes, sem mudar o que cada teste afirma: `TRUNCATE` de limpeza (`_truncate_all`, `factories.truncate_content`, `test_monitor.py`) e `setval` em `ops.ticket_seq` (`test_tickets.py`, `test_deliveries.py`, `test_schema_ops_tickets.py`) passam por `owner_execute`; reexecucao de migration (`test_migration_201/202/301/401.py`), gatilho da auditoria (`test_schema_access_chat.py`), tabela renomeada para simular falha (`test_tickets_admin.py`), visoes e `apply_grants()` do assistente (`test_assistant_role.py`, `test_assistant_service.py`, `test_assistant_no_write.py`) passam por `owner_conn`. A migration 402 nao precisou mudar.
3. **Ferramentas do assistente nos testes rodam como `jarvis_assistant`** (fixture `assistant_pool`, `assistant_dsn`), como em producao (`ASSISTANT_DATABASE_URL`). Antes usavam o pool do app, que era o dono e via tudo. Arquivos: `test_assistant_service.py`, `test_assistant_no_write.py`, `test_pii_in_logs.py`.
4. **A guarda da T05 pegou testes** que subiam o app em producao com o DSN do dono (`test_no_alert_counts.py`, via `test_app_e2e.start_env`): resolvido pelo desvio 1, sem tocar nesses testes.
5. **Testes do PC e do mesmo banco nao rodam em paralelo:** duas sessoes de pytest no `jarvis_test` ao mesmo tempo travam (TRUNCATE x GRANT). Ja era verdade (TRUNCATE por teste); registrado porque aconteceu nesta tarefa.
6. **`DEV_SETUP.md`** apontava o `psql` para `C:\Program Files\PostgreSQL\18`; corrigido para `C:\Users\caiofranco\pgsql\bin` (T07).

## Verificacao das ondas (arquivos compartilhados)

| Onda | Tarefas | Compartilhados tocados (uma tarefa cada) |
|---|---|---|
| W0 | T01, T02, T03 | - (`402` so em T01; `migrate.py` so em T02) |
| W1 | T04, T05 | `main.py` (T05); `402` (T04, so se necessario) |
| W2 | T06 | - |
| W3 | T07, T08 | - |

## Cobertura dos criterios

| AC | Tarefas |
|---|---|
| PA-1 | T03, T07 |
| PA-2 | T02 |
| PA-3 | T04, T07 |
| PA-4 | T01, T06 |
| PA-5 | T01, T06 |
| PA-6 | T01, T06 |
| PA-7 | T01, T06 |
| PA-8 | T01, T06 |
| PA-9 | T01, T06 |
| PA-10 | T02, T06 |
| PA-11 | T01 |
| PA-12 | T05 |
| PA-13 | T04 |
| PA-14 | T07, T08 |
