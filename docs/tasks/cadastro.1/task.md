---
task_id: CADASTRO-1
phase: 4-implementation
status: approved
tasks_approved_by: Caio
tasks_approved_at: 2026-11-18
spec: spec/spec.md
total_tasks: 7
total_waves: 4
estimated_hours: 20
generated: 2026-11-18
---

# Task breakdown - CADASTRO-1 (Editar o cadastro e trocar o numero pelo painel)

Fonte: `spec/spec.md` (Fase 1 e Fase 2 aprovadas por Caio em 2026-11-18), `spec/requirements.json` (CA-1..CA-14), `spec/complexity.json`, `spec/research.json`, extensao da ADR de 2026-11-17 em `docs/architecture/overview.md`. Fase 3 aprovada por Caio em 2026-11-18; Fase 4 concluida em 2026-11-19 (CA-T06 opcional nao feita).

## Convencoes

- **IDs:** `CA-T01..CA-T07` (prefixo `CA-T` para nao colidir com os criterios `CA-n`). Commit: `type(cadastro): CADASTRO-1.Txx - descricao`, **feito pelo Caio**. Checkbox: `[ ]` pendente, `[~]` em andamento, `[x]` pronta; `[P]` = paralela na onda.
- **Caminhos** relativos a raiz do repo. `B/` = `backend/`, `BT/` = `backend/tests/`, `F/` = `frontend/`. Comandos a partir de `backend\`.
- **Whitelist:** so os arquivos da coluna "Arquivos". "(se necessario)" = apenas se um teste existente quebrar por causa da mudanca, sem mudar o que ele afirma.
- **Verificacao padrao:** o `pytest` indicado + `ruff check app tests` + `mypy app`. Migration soma `migrate --check` (coberto pelo teste do runner). Template so com classes que ja existem no CSS (sem o binario do Tailwind neste PC).
- **Banco:** os testes rodam o app como `jarvis_app` (porta-fechada.2); o app continua sem DDL. Nunca dois pytest no banco de teste ao mesmo tempo.
- **Privacidade:** nenhum log com nome, telefone, empresa ou gestor; fixtures com telefone `+5500...`/`+55119...` ficticio.

## W0 - Base

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| CA-T01 `[x]` `[P]` | Migration 403 e kind `brand_access_granted` | 2.5 | - | `B/migrations/403_brand_access_granted.sql`, `B/app/core/values.py`, `B/app/services/outbox.py` (REQUIRED_STATUS e isencao da janela), `B/app/config.py` (`TEXT_BRAND_ACCESS_GRANTED`), `BT/integration/test_migration_403.py`, `BT/integration/test_outbox_dispatch.py`, `BT/unit/test_values.py` e `BT/unit/test_config.py` (se necessario) | CA-11 (D4) | `pytest tests/integration/test_migration_403.py tests/integration/test_outbox_dispatch.py -q`: CHECK aceita o kind novo e so um CHECK de kind; sai fora da janela de 24 h; a pausa segura; `HUMAN_REPLY_IGNORE_WINDOW=false` volta a respeitar a janela |

## W1 - Servico

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| CA-T02 `[x]` | `users.update` | 5 | T01 | `B/app/services/users.py`, `BT/integration/test_users_update.py` | CA-4, CA-5, CA-6, CA-7, CA-8, CA-9, CA-10, CA-11 | `pytest tests/integration/test_users_update.py -q`: ok/noop/conflict/not_editable/not_found; status, aceite e telefone intactos; marca da conversa zerada so quando a marca sai; pedido concluido (autor e auditoria) e uma saida por marca so para `active`; sem pedido nada e enfileirado; auditoria sem nome/empresa/gestor |

## W2 - Painel

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| CA-T03 `[x]` | Rotas e modal de edicao | 4.5 | T02 | `B/app/panel/api/pessoas_editar.py`, `F/templates/_pessoa_form.html` (modo edicao), `F/templates/_pessoa_ficha.html` (botoes), `BT/panel/integration/test_pessoas_editar.py`, `BT/panel/integration/test_pessoas_cadastro.py` e `BT/panel/integration/test_pessoas_ficha.py` (se necessario) | CA-1, CA-2, CA-3, CA-4, CA-5, CA-6 | `pytest tests/panel/integration/test_pessoas_editar.py tests/panel/integration/test_pessoas_cadastro.py tests/panel/integration/test_pessoas_ficha.py -q`: formulario preenchido com telefone so leitura; salvar com toast; erro de campo; conflito com dados atuais; revogada sem botao e POST recusado; despacho das saidas depois do COMMIT |
| CA-T04 `[x]` | Trocar numero pelo painel | 2.5 | T02 | `B/app/panel/api/pessoas_editar.py` (rotas `/trocar-numero`), `F/templates/_pessoa_trocar_numero.html`, `BT/panel/integration/test_pessoas_editar.py` | CA-12, CA-13 | Mesmo arquivo de teste: troca cria a ficha nova `pending_optin` com aviso na fila e `HX-Redirect`; antiga `revoked` com `number_change`; duplicado (inclusive pelo 9o digito) e invalido mostram erro sem mudar nada. **Serializar com T03** (mesmos arquivos). |

## W3 - Fechamento

| ID | Titulo | h | depends_on | Arquivos (whitelist) | AC | Verificacao (testes) |
|---|---|---|---|---|---|---|
| CA-T05 `[x]` `[P]` | Seguranca e mapa de criterios | 2.5 | T03, T04 | `BT/panel/security/test_route_sweep.py`, `BT/panel/security/test_pii_in_logs.py`, `BT/panel/AC_MAP.md` (secao "Cadastro"), `BT/panel/test_ac_coverage.py` | CA-14, todos (mapa) | `pytest tests/panel/security tests/panel/test_ac_coverage.py -q`: rotas novas exigem admin e CSRF no POST; logs da edicao e da troca sem PII; todo CA-n com teste que existe |
| CA-T06 `[-]` `[P]` | E2E da edicao (opcional) | 1.5 | T03, PN-T65 | `BT/e2e/test_cadastro.py` | CA-1, CA-2, CA-12 | `pytest tests/e2e -m e2e -q` (pula sem Playwright) |
| CA-T07 `[x]` | Verificacao final e documentacao | 1.5 | todas | `docs/tasks/cadastro.1/task.md`, `docs/tasks/cadastro.1/spec/spec.md`, `docs/tasks/cadastro.1/spec/requirements.json`, `docs/stories/cadastro.1.md`, `docs/STATE.md` (B-001: lacuna resolvida), `docs/architecture/overview.md` (extensao da ADR: proposta -> aceita) | todos | Suite inteira, `mypy app`, `ruff check app tests`, `pytest tests/panel -m perf -q`, `eval --mode palavras` APROVADO |

## Desvios do design

1. **Orquestracao no painel** (T02): `app/services` nao importa `app.panel` (teste de arquitetura), e concluir o pedido usa `tickets_admin.conclude`. Entao `users.update` cuida so de `access.users` e da auditoria, e o novo `B/app/panel/services/people_edit.py` faz, na mesma transacao, a conclusao dos pedidos e os avisos `brand_access_granted`.
2. **Versao que sempre avanca** (T02/T03): o `UPDATE` grava `updated_at = GREATEST(agora, updated_at + 1 microssegundo)`; com relogio parado ou repetido, a edicao concorrente (CA-5) passava despercebida.
3. **Troca de numero pelo id** (T04): novo `users.swap_number_by_id` (mesma regra do `swap_number`, que passou a usar o helper comum `_swap_locked`); o admin nunca digita o numero antigo e o painel nao depende de achar a pessoa pelo telefone. A resposta devolve a ficha nova no modal, sem `HX-Redirect`.
4. **Formulario compartilhado** (T03): `pessoas_cadastro._render_form` virou `render_person_form` publico (com `action`, `editing`, `version`) e `FIELD_LABELS` publico.
5. **Teste da 401** (T01): reexecutar a 401 recria o CHECK de `kind` sem o kind novo; o teste passou a reaplicar as migrations posteriores, como os da 201 e da 301.
6. **Log de toda edicao** (T05): `panel_person_updated` saia so quando havia marca liberada; o teste de PII nos logs pegou e agora toda edicao gravada loga (so nomes de campos e contagens).
7. **T06 (E2E da edicao) nao feita:** opcional; as telas estao cobertas pelos testes de integracao (`test_pessoas_editar.py`), e o Caio pediu para poupar tokens em 2026-11-19.
9. **Listas de controle dos testes** (T07): `BT/integration/test_outbox_enqueue.py` (mapa kind -> status) e `BT/unit/test_access.py` (kinds sem texto fixo) passaram a incluir `brand_access_granted`.
8. **Bug do drawer (fora desta historia, achado pelos E2E do painel.1):** corrigido no mesmo dia em `_kanban_cartao.html` e `_alert_card.html` (`hx-select="unset"`).

## Verificacao das ondas (arquivos compartilhados)

| Onda | Tarefas | Compartilhados tocados (uma tarefa cada) |
|---|---|---|
| W0 | T01 | `values.py`, `outbox.py`, `config.py` |
| W1 | T02 | `users.py` |
| W2 | T03, T04 (em sequencia) | `pessoas_editar.py`, `test_pessoas_editar.py` |
| W3 | T05, T06, T07 | - |

## Cobertura dos criterios

| AC | Tarefas |
|---|---|
| CA-1 | T03, T06 |
| CA-2 | T03, T06 |
| CA-3 | T03 |
| CA-4 | T02, T03 |
| CA-5 | T02, T03 |
| CA-6 | T02, T03 |
| CA-7 | T02 |
| CA-8 | T02 |
| CA-9 | T02 |
| CA-10 | T02 |
| CA-11 | T01, T02 |
| CA-12 | T04, T06 |
| CA-13 | T04 |
| CA-14 | T05 |
