---
task_id: PORTA-FECHADA-2
status: implemented
phase: 4-implementation
requirements_approved_by: Caio
requirements_approved_at: 2026-11-18
design_approved_by: Caio
design_approved_at: 2026-11-18
tasks_approved_by: Caio
tasks_approved_at: 2026-11-18
---

# Spec — Papel da aplicação sem DDL no banco (PORTA-FECHADA-2)

> **Story:** [porta-fechada.2](../../../stories/porta-fechada.2.md) · **Epic:** [porta-fechada](../../../epics/porta-fechada.md)
> **Status:** Fase 1 (Requisitos, PA-1 a PA-14; decisões D1–D4 tomadas pelo Caio em 2026-11-18) **aprovada por Caio em 2026-11-18**. Fase 2 (design: seções 7 a 9, `research.json`, `complexity.json`, ADR em `overview.md`) **aprovada por Caio em 2026-11-18**. Fase 3 (tarefas: [`../task.md`](../task.md)) **aprovada por Caio em 2026-11-18**; Fase 4 concluída em 2026-11-18 (seção 10).
> Critérios completos e testáveis: `requirements.json`.

## 1. Objetivo

A spec de porta-fechada.1 (4.2.6 e 4.10) previu dois papéis no banco de produção:
`jarvis_owner` (dono, cria e altera tabelas, roda as migrations) e `jarvis_app` (o app,
só lê e grava dados, sem UPDATE/DELETE na auditoria). Isso **nunca foi implementado**:
hoje o app roda com o papel dono do banco, no PC e no plano de deploy (`deploy/RUNBOOK.md`
passo 2: "hoje o app roda com o papel dono").

Consequências de deixar como está:

- uma falha no app (ou uma injeção de SQL que escape das consultas parametrizadas) pode
  apagar ou alterar tabelas, desligar o gatilho que protege `ops.audit_log` e reescrever a
  auditoria;
- os GRANTs pendentes no `STATE.md` (cérebro: `chat.brain_runs`, `chat.answer_ratings`;
  retorno humano: `content.faq_suggestions` e colunas novas) seriam um passo manual a cada
  migration nova, fácil de esquecer: o primeiro sintoma seria um `permission denied` em
  produção.

Esta história faz a separação **antes** do primeiro deploy (o banco do droplet ainda não
existe), para que o go-live já saia com o app sem poder mudar a estrutura do banco e sem
GRANT feito à mão.

## 2. Escopo

**Dentro:**
- Os dois papéis em produção: dono (DDL) e aplicação (só dados), com o banco pertencendo ao dono.
- O runner de migrations conectando como dono, separado do `DATABASE_URL` do app.
- Permissões do papel da aplicação aplicadas de forma automática e repetível (inclusive para tabelas de migrations futuras).
- Auditoria (`ops.audit_log`) só com INSERT e SELECT para o app.
- Prova automática de que o app funciona inteiro com o papel restrito (fluxos principais) e de que ele não consegue mudar a estrutura.
- Runbook, `.env` modelo, `dev_db_setup.sql` e CI atualizados.

**Fora:** ver seção 5.

## 3. Critérios de aceite (EARS)

Convenções: "papel dono" é `jarvis_owner` em produção (no PC continua `jarvis`/`jarvis_test`).
"Papel da aplicação" é `jarvis_app`. "Schemas do Jarvis" são `access`, `chat`, `ops` e
`content`. "Estrutura" é qualquer DDL: CREATE, ALTER, DROP, TRUNCATE, gatilhos, GRANT.

### Papéis e conexões

- **PA-1 Dois papéis.** GIVEN o runbook de produção, WHEN o banco é criado, THEN existem `jarvis_owner` (LOGIN, sem superusuário, dono do banco `jarvis`) e `jarvis_app` (LOGIN, sem superusuário, sem CREATEDB/CREATEROLE, não dono de nada); `PUBLIC` não tem acesso ao banco.
- **PA-2 Runner como dono.** GIVEN `MIGRATION_DATABASE_URL` preenchida, WHEN `python -m app.db.migrate` roda (aplicar, `--status` ou `--check`), THEN conecta com ela; GIVEN vazia, THEN usa `DATABASE_URL` (comportamento de hoje, PC e CI).
- **PA-3 App com o papel restrito.** GIVEN produção, WHEN o app sobe, THEN `DATABASE_URL` aponta para `jarvis_app`; o painel, os laços de fundo e a CLI (`app.cli`) usam essa mesma conexão.

### Permissões

- **PA-4 Dados.** GIVEN o papel da aplicação, WHEN as permissões são aplicadas, THEN ele tem `USAGE` nos schemas do Jarvis, `SELECT, INSERT, UPDATE, DELETE` em todas as tabelas e visões desses schemas e `USAGE, SELECT` em todas as sequências, com as exceções de PA-5 e PA-6.
- **PA-5 Auditoria imutável.** GIVEN o papel da aplicação, WHEN tenta `UPDATE`, `DELETE` ou `TRUNCATE` em `ops.audit_log`, THEN o banco recusa por falta de permissão (além do gatilho que já existe); `INSERT` e `SELECT` funcionam.
- **PA-6 Controle de migrations.** GIVEN o papel da aplicação, WHEN acessa `ops.schema_migrations`, THEN só `SELECT` (o `/health` usa `migrate.is_up_to_date`).
- **PA-7 Sem estrutura.** GIVEN o papel da aplicação, WHEN tenta criar tabela ou função em qualquer schema (inclusive `public`), alterar, apagar ou truncar tabela, mexer em gatilho ou dar GRANT, THEN o banco recusa.
- **PA-8 Sem o assistente.** GIVEN o papel da aplicação, WHEN as permissões são aplicadas, THEN ele não ganha nada no schema `assistant` nem pode executar as funções de GRANT; `jarvis_assistant` continua só com as visões de `assistant` (comportamento da 105 inalterado).

### Aplicação automática

- **PA-9 Repetível.** GIVEN o papel da aplicação existe, WHEN as permissões são aplicadas duas vezes, THEN o resultado é o mesmo e não há erro.
- **PA-10 Migrations futuras** *(D1)*. GIVEN uma migration nova que cria tabela ou sequência, WHEN é aplicada pelo runner, THEN o papel da aplicação passa a ter as permissões de PA-4 nela sem nenhum passo manual.
- **PA-11 Sem o papel.** GIVEN o papel `jarvis_app` não existe (PC, CI antigo), WHEN as migrations rodam, THEN tudo funciona como hoje e um aviso (NOTICE) diz que as permissões do app não foram aplicadas.

### Proteção e prova

- **PA-12 Subida com papel errado** *(D2)*. GIVEN `PRODUCTION=true`, WHEN o app sobe conectado com um papel que é dono de tabelas do Jarvis ou superusuário, THEN a subida é recusada com mensagem que cita o nome do papel e o motivo (dono de tabela ou superusuário), nunca a URL. Fora de produção, nada é checado (no PC o app usa o papel dono, D4).
- **PA-13 Funciona com o papel restrito** *(D3)*. GIVEN o banco de teste com as permissões aplicadas, WHEN os fluxos principais rodam como papel da aplicação, THEN passam: mensagem do vendedor até a resposta e a fila de saída; ticket escalado e aviso; painel (concluir, responder, FAQ, sugestões, controles, cadastro); LGPD (excluir pessoa); retenção (expurgo); CLI de carga da FAQ; `/health`.
- **PA-14 Documentação.** GIVEN o runbook, WHEN alguém sobe o Jarvis num servidor novo, THEN os passos 2, 5 e 6 criam os dois papéis, preenchem `DATABASE_URL` (app) e `MIGRATION_DATABASE_URL` (dono) e aplicam as migrations como dono; o `STATE.md` deixa de listar os GRANTs manuais como pendência.

## 4. Requisitos não funcionais

- SQL compatível com PostgreSQL 16; nenhuma dependência nova.
- Senhas só no `.env` do servidor; nunca em arquivo versionado, log ou mensagem de erro (a mensagem de PA-12 cita o nome do papel, nunca a URL).
- Nada muda para quem desenvolve no PC sem criar o papel novo (PA-11).
- A suíte atual continua passando sem alteração de comportamento.

## 5. Fora do escopo

- Banco do droplet: ainda não existe; os comandos ficam no runbook e são executados no go-live, com o Caio, passo a passo.
- Segurança por linha (RLS), troca periódica de senha, pgbouncer.
- Separar mais papéis (por exemplo, um para a CLI ou para os laços de fundo).
- Mudar `jarvis_assistant` (já é restrito desde a 105).

## 6. Decisões (Caio, 2026-11-18; todas na opção recomendada)

| # | Pergunta | Decisão |
|---|---|---|
| **D1** | Como as tabelas de migrations futuras ganham permissão? | **O runner reaplica as permissões ao fim de cada `migrate`** (função idempotente na migration nova, como `assistant.apply_grants()`). Alternativa: `ALTER DEFAULT PRIVILEGES` do dono (invisível, só vale para objetos criados pelo próprio dono). |
| **D2** | App em produção conectado como dono: recusar a subida ou só alertar? | **Recusar a subida** (como já acontece com `DEV_AUTH_EMAIL` em produção). |
| **D3** | Como provar PA-13? | **Testes dos fluxos principais rodando como `jarvis_app` (SET ROLE)**, além da varredura do catálogo. Pede **um passo seu, uma vez**: rodar o `dev_db_setup.sql` atualizado como `postgres` no PC (cria `jarvis_app` e deixa `jarvis_test` assumi-lo). Alternativa: só a varredura do catálogo, sem passo manual, mas sem provar que nenhuma consulta precisa de algo a mais. |
| **D4** | No PC, o app passa a usar `jarvis_app`? | **Não**: no PC continua o papel dono (simples); a separação vale em produção e é provada pelos testes. |

## 7. Design

### 7.1 Migration `402_app_role_grants.sql`

Cria `ops.apply_app_grants()` (plpgsql, `SECURITY INVOKER`: só funciona quando quem chama é o dono das tabelas) e a chama no fim, como a 105 faz com `assistant.apply_grants()`. Corpo, para o papel `jarvis_app`:

1. Se o papel não existe: `RAISE NOTICE` e sai (PA-11).
2. `GRANT CONNECT ON DATABASE <atual>`; `REVOKE ALL ON SCHEMA public` (PA-7; no PG 16 `PUBLIC` já não cria em `public`).
3. Para cada schema `access`, `chat`, `ops`, `content`: `GRANT USAGE ON SCHEMA`, `REVOKE CREATE ON SCHEMA`, `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES` (inclui visões, como `ops.v_tickets`), `REVOKE TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES`, `GRANT USAGE, SELECT ON ALL SEQUENCES` (PA-4, PA-7).
4. `REVOKE UPDATE, DELETE ON ops.audit_log` (PA-5) e `REVOKE INSERT, UPDATE, DELETE ON ops.schema_migrations` (PA-6).
5. `REVOKE ALL ON SCHEMA assistant` e `REVOKE ALL ON ALL TABLES IN SCHEMA assistant` (PA-8).

Depois da função: `REVOKE ALL ON FUNCTION ops.apply_app_grants() FROM PUBLIC` (a de `assistant` já é revogada na 105). GRANT e REVOKE são idempotentes (PA-9). Sem `ALTER DEFAULT PRIVILEGES` (D1). Schema novo no futuro entra por uma migration que recria a função (`CREATE OR REPLACE`).

### 7.2 Runner (`app/db/migrate.py`)

- **URL (PA-2):** `main()` lê `MIGRATION_DATABASE_URL` e, vazia, `DATABASE_URL`. A mensagem de erro cita as duas variáveis, nunca o valor.
- **Reaplicar (PA-10, D1):** `apply_pending` termina chamando `refresh_grants(conn)`: para cada função existente entre `ops.apply_app_grants()` e `assistant.apply_grants()`, um `SELECT` dela, sob o mesmo lock consultivo. Roda a cada `migrate` (mesmo sem pendentes), então criar o papel e rodar `migrate` de novo basta. `--status` e `--check` não mudam nada (o `/health` continua só lendo).
- O fixture de testes (`tests/db_support._apply_real_migrations`) já chama `apply_pending`, então os testes recebem as permissões sem código a mais.

### 7.3 Subida em produção (PA-12, D2)

Novo `app/db/roles.py` com `async def role_problems(conn) -> list[str]`, uma consulta ao catálogo para `current_user`: superusuário; dono do banco; dono de alguma tabela, visão, sequência ou função dos schemas do Jarvis; `CREATE` em algum desses schemas; membro do papel dono do banco. O lifespan (`main.py`), logo depois de criar o pool, **só com `PRODUCTION=true`**: se houver problema, fecha o pool e levanta `RuntimeError` com o nome do papel e os motivos (ex.: "papel do banco 'jarvis_owner' nao pode ser usado pelo app em producao: dono do banco, dono de tabelas"), nunca a URL. Fora de produção, nada (D4).

### 7.4 Testes como `jarvis_app` (PA-13, D3)

- **Papel no PC e no CI:** `scripts/dev_db_setup.sql` e o passo "Preparar papéis" do CI criam `jarvis_app` **NOLOGIN** (no PC e no CI ninguém entra direto com ele) e `GRANT jarvis_app TO jarvis_test`, como já é feito com `jarvis_assistant`.
- **Toda a suíte vira prova:** o fixture `migrated_database` (usado pelo `db_pool`, pelos `Settings` dos testes e pelo `create_app` dos testes ponta a ponta) passa a devolver o DSN de teste com o papel trocado na conexão (parâmetro de partida `role=jarvis_app`, que o asyncpg manda como `server_settings`). O fixture `database_url` continua o do dono e é o que migra e faz o TRUNCATE. Resultado: webhook, cérebro, fila de saída, laços, painel, LGPD, retenção, CLI e `/health` rodam como `jarvis_app` em todos os testes que já existem.
- **Papel ausente:** o `migrated_database` falha com a instrução "rode backend/scripts/dev_db_setup.sql como postgres" (não pula: pular esconderia o problema).
- **Testes que precisam do dono** (migrations, gatilhos, TRUNCATE próprio, `SET ROLE jarvis_assistant`) passam a usar `database_url`; ajuste listado na Fase 3.
- **Varredura do catálogo** (`tests/security/test_app_role.py`): para cada tabela, visão e sequência dos schemas do Jarvis, `has_table_privilege` / `has_sequence_privilege` conforme PA-4 a PA-6; e tentativas reais como `jarvis_app` de CREATE TABLE (em cada schema e em `public`), ALTER, DROP, TRUNCATE, DROP TRIGGER, GRANT, UPDATE/DELETE em `ops.audit_log` e leitura de `assistant.people`, todas com `InsufficientPrivilegeError` (PA-5, PA-7, PA-8). Mais: repetir `apply_app_grants()` não muda nada (PA-9); migration nova de teste ganha permissão sozinha (PA-10); `role_problems` acusa o dono e aprova o `jarvis_app` (PA-12).

### 7.5 Documentação e configuração (PA-14)

- `deploy/RUNBOOK.md`: passo 2 cria `jarvis_owner` e `jarvis_app` (LOGIN, senhas com `\password`) e `CREATE DATABASE jarvis OWNER jarvis_owner`; passo 5 com `DATABASE_URL` (app) e `MIGRATION_DATABASE_URL` (dono); passo 6 exporta a do dono para o `migrate`; "Atualizar" idem; seção 11 (`pg_restore --no-owner` como dono, depois `migrate` para reaplicar as permissões).
- `backend/configuracao-modelo.txt`: `MIGRATION_DATABASE_URL` comentada, com a explicação.
- `STATE.md`: tira os GRANTs manuais das pendências (cérebro e retorno humano).

## 8. Arquivos

| Ação | Arquivo |
|---|---|
| Create | `backend/migrations/402_app_role_grants.sql`, `backend/app/db/roles.py`, `backend/tests/security/test_app_role.py`, `backend/tests/integration/test_migration_402.py`, `backend/tests/integration/test_startup_role.py` |
| Modify | `backend/app/db/migrate.py`, `backend/app/main.py`, `backend/tests/db_support.py`, `backend/scripts/dev_db_setup.sql`, `.github/workflows/ci.yml`, `deploy/RUNBOOK.md`, `backend/configuracao-modelo.txt`, `backend/DEV_SETUP.md`, `backend/tests/integration/test_migrate_runner.py`, testes que precisam do dono (lista na Fase 3) |
| Docs | este spec, `task.md`, `docs/stories/porta-fechada.2.md`, `docs/STATE.md`, ADR em `docs/architecture/overview.md` |

## 9. Riscos

- **Testes que dependem de ser dono** só aparecem ao rodar a suíte com o papel trocado (ex.: TRUNCATE próprio no `conftest` do painel; testes do assistente que fazem `SET ROLE jarvis_assistant`, que o `jarvis_app` não pode assumir). Mitigação: a primeira tarefa roda a suíte inteira com o papel trocado e lista o que falhou antes de mudar código do app.
- **Consulta do app que precisa de mais permissão** (`pg_advisory_*` é livre; `nextval` precisa de USAGE na sequência, já dado). Se aparecer, é exatamente o que a história quer descobrir antes do deploy.
- **Parâmetro `role` na partida da conexão:** é um parâmetro de sessão comum do PostgreSQL, repassado pelo asyncpg; a primeira tarefa confirma no PG 18 local e o CI no PG 16. Plano B: `init` do pool com `SET ROLE` (cobre só o `db_pool`, não o `create_app`).
- **Passo manual:** sem o `dev_db_setup.sql` atualizado, a suíte local falha com a instrução. É o combinado em D3.

## 10. Divergências da Fase 4 (2026-11-18)

Detalhes em `../task.md` ("Desvios do design"). Resumo:

1. A troca de papel nos testes vale para a própria `TEST_DATABASE_URL` (conftest raiz), não só para o fixture: os testes ponta a ponta que leem a variável também sobem o app como `jarvis_app`. A suíte inteira (3771 testes) passa assim.
2. Nenhuma permissão faltou ao app; a migration 402 ficou como no design. Os ajustes foram em testes que faziam o que só o dono faz (TRUNCATE, `setval`, reexecutar migration, mexer em gatilho, SET ROLE do assistente).
3. Nos testes, as ferramentas do assistente passam a rodar como `jarvis_assistant` (antes usavam o pool do app).

