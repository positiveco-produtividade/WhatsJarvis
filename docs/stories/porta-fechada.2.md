# História porta-fechada.2 — Papel da aplicação sem DDL no banco

> **Épico:** [porta-fechada](../epics/porta-fechada.md) · **Status:** Implementada em 2026-11-18 (fases 1 a 3 aprovadas por Caio em 2026-11-18; suíte inteira com o app como `jarvis_app`, perf e eval palavras verdes; aplicar no droplet junto com o deploy) · **Owner:** Caio · **Criado:** 2026-11-18

Fonte de verdade: `docs/tasks/porta-fechada.2/spec/spec.md` e `requirements.json`.
Em caso de divergência, vale o spec.

## Como / quero / para

**Como** dono do Jarvis,
**quero** que o app rode em produção com um papel que só lê e grava dados,
**para** que uma falha no app não consiga mudar a estrutura do banco nem a auditoria, e
migrations novas não dependam de GRANT manual.

## Escopo (resumo)

- Papéis `jarvis_owner` (dono, roda as migrations) e `jarvis_app` (o app) em produção.
- Runner com `MIGRATION_DATABASE_URL`; permissões reaplicadas ao fim de cada migrate.
- Auditoria só INSERT/SELECT para o app; app sem nenhum DDL.
- App recusa subir em produção com o papel dono.
- Fluxos principais testados como `jarvis_app`; runbook, CI e `dev_db_setup.sql` atualizados.

Origem: spec de porta-fechada.1, 4.2.6 e 4.10 (previsto e não implementado);
`docs/tasks/cerebro.1/spec/verificacao-externa.md`, item 5.
