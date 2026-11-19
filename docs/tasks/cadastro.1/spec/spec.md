---
task_id: CADASTRO-1
status: implemented
phase: 4-implementation
requirements_approved_by: Caio
requirements_approved_at: 2026-11-18
design_approved_by: Caio
design_approved_at: 2026-11-18
tasks_approved_by: Caio
tasks_approved_at: 2026-11-18
---

# Spec — Editar o cadastro e trocar o número pelo painel (CADASTRO-1)

> **Story:** [cadastro.1](../../../stories/cadastro.1.md) · **Epic:** [painel](../../../epics/painel.md)
> **Status:** Fase 1 (Requisitos, CA-1 a CA-14; decisões D1–D6 tomadas pelo Caio em 2026-11-18) **aprovada por Caio em 2026-11-18**. Fase 2 (design: seções 7 a 10, `research.json`, `complexity.json`, extensão da ADR de 2026-11-17) **aprovada por Caio em 2026-11-18**. Fase 3 (tarefas: [`../task.md`](../task.md)) **aprovada por Caio em 2026-11-18**; Fase 4 concluída em 2026-11-19 (seção 11).
> Critérios completos e testáveis: `requirements.json`.

## 1. Objetivo

Hoje o painel cadastra, retira, reativa, reenvia o aviso e apaga (LGPD) uma pessoa, mas
**não edita**: não há como mudar perfil, empresa, UFs, canais, marcas, gestor ou validade de
quem já está cadastrado, e trocar o número só pela linha de comando (`python -m app.cli user
swap-number`). Em 2026-10-27 foi preciso mudar um perfil direto no banco (B-001). Com 100
vendedores, liberar uma marca ou corrigir uma UF vira rotina.

Esta história dá ao admin:
- **Editar** os dados de uma pessoa na ficha, com as mesmas regras do cadastro;
- **Trocar o número** pela ficha, com a mesma regra da linha de comando (D1);
- ao **liberar uma marca** que a pessoa pediu, concluir o pedido e avisar o vendedor (D2).

## 2. Escopo

**Dentro:** botão "Editar" e "Trocar número" na ficha da pessoa; validação igual ao cadastro;
auditoria; efeito imediato das marcas nas respostas; conclusão dos pedidos de acesso à marca
com aviso ao vendedor; logs sem PII.

**Fora:** ver seção 5.

## 3. Critérios de aceite (EARS)

Convenções: "ficha" é a página da pessoa no painel (`/dashboard/pessoas/{id}`). "Pedido de
acesso" é o ticket escalado com motivo `brand_access_request`. Campos editáveis: nome, perfil,
empresa, UFs, canais, marcas, gestor e validade (só parceiro externo).

### Editar

- **CA-1 Abrir a edição.** GIVEN um admin na ficha de uma pessoa `pending_optin`, `active` ou `declined`, WHEN clica "Editar", THEN abre o formulário do cadastro já preenchido com os dados atuais; o telefone aparece só para leitura (troca é por CA-9).
- **CA-2 Salvar.** GIVEN o formulário de edição, WHEN o admin salva dados válidos, THEN a pessoa é atualizada, a ficha mostra os dados novos e um aviso "Cadastro atualizado"; a validação é a mesma do cadastro (campos obrigatórios, listas fechadas de perfil, UFs, canais e marcas, validade só para parceiro externo).
- **CA-3 Dados inválidos.** GIVEN dados inválidos, WHEN o admin salva, THEN o formulário volta com o erro no campo e nada é gravado.
- **CA-4 Sem mudança.** GIVEN nenhum campo alterado, WHEN o admin salva, THEN nada é gravado nem auditado e a ficha diz "Nada foi alterado".
- **CA-5 Edição concorrente.** GIVEN dois admins editando a mesma pessoa, WHEN o segundo salva depois que o primeiro já gravou, THEN o segundo recebe "Outra pessoa alterou este cadastro; confira e salve de novo" com os dados atuais, e nada dele é gravado (mesmo padrão da FAQ, PN-17).
- **CA-6 Quem não edita.** GIVEN uma pessoa `revoked` ou excluída pela LGPD, WHEN o admin abre a ficha, THEN não há "Editar" (reativar antes); um POST direto é recusado sem gravar.
- **CA-7 Status e aceite intactos.** GIVEN uma edição, WHEN é gravada, THEN status, aceite, versão do aviso, histórico de opt-in e telefone não mudam.
- **CA-8 Auditoria.** GIVEN uma edição gravada, WHEN se olha a auditoria, THEN há uma linha `user.update` com o admin, a pessoa e **os nomes** dos campos alterados; perfil, UFs, canais, marcas e validade com valor antigo e novo; nome, empresa e gestor só como "alterado" (sem o texto, que é dado pessoal).

### Marcas

- **CA-9 Efeito imediato.** GIVEN marcas alteradas, WHEN o vendedor manda a próxima mensagem, THEN o Jarvis já responde com as marcas novas (acesso, enquete de marca e "qual marca?").
- **CA-10 Marca da conversa retirada.** GIVEN o vendedor conversando sobre uma marca (marca da conversa ativa), WHEN o admin retira essa marca dele, THEN a marca da conversa é apagada na mesma gravação, e a próxima pergunta segue a regra de quem não escolheu marca.
- **CA-11 Pedido de acesso atendido** *(D2)*. GIVEN pedidos de acesso abertos (aguardando) da pessoa para uma marca, WHEN o admin dá essa marca a ela, THEN na mesma gravação esses pedidos são concluídos com o admin como autor, e o vendedor recebe pelo WhatsApp "Seu acesso a {marca} foi liberado. Pode perguntar!" (uma mensagem por marca liberada, texto configurável). Vendedor que não está `active` não recebe mensagem; o pedido conclui do mesmo jeito.

### Trocar número

- **CA-12 Trocar número** *(D1)*. GIVEN uma pessoa `pending_optin`, `active` ou `declined`, WHEN o admin usa "Trocar número", informa o novo telefone e confirma no modal, THEN vale a mesma regra de `swap-number`: o cadastro antigo é retirado com motivo "troca de número", um novo é criado com os mesmos dados e `pending_optin`, o aviso de privacidade vai para o número novo, e o admin é levado à ficha nova; tudo numa transação, auditado.
- **CA-13 Número inválido ou já cadastrado.** GIVEN telefone inválido ou de outra pessoa (inclusive com/sem 9º dígito), WHEN o admin confirma, THEN o modal mostra o erro e nada muda.

### Segurança

- **CA-14 Acesso e privacidade.** GIVEN as rotas novas, WHEN acessadas sem sessão de admin ou POST sem CSRF, THEN recusadas como as demais do painel; nenhum log leva nome, telefone, empresa ou gestor; as rotas entram na varredura de segurança do painel.

## 4. Requisitos não funcionais

- Sem dependência nova; SQL compatível com PostgreSQL 16; o app continua sem DDL (porta-fechada.2).
- Mesmo visual do cadastro (Jinja + HTMX + classes já existentes no CSS; o binário do Tailwind não está neste PC).
- Edição e troca em uma transação cada; a mensagem de CA-11 sai pela fila de saída (outbox) depois do COMMIT.

## 5. Fora do escopo

- Editar em lote ou por importação de planilha.
- Mudar status à mão (já existem retirar, reativar e reenviar).
- Editar pela linha de comando (o `swap-number` da CLI continua).
- Avisar o vendedor de mudanças que não sejam liberação de marca.

## 6. Decisões

| # | Pergunta | Decisão / recomendação |
|---|---|---|
| **D1** | Telefone pelo painel? | **Decidido (Caio, 2026-11-18):** botão "Trocar número" com a regra do `swap-number`. |
| **D2** | Liberar marca pedida? | **Decidido (Caio, 2026-11-18):** conclui o pedido e avisa o vendedor. |
| **D3** | Resumo diário no grupo | Fora desta história: vai como tarefa do painel.1 (PN-61, "igual ao AUTO-1", decidido em 2026-11-18). |
| **D4** | A mensagem de CA-11 respeita a janela de 24 h do WAHA (WA-16)? | **Decidido (Caio, 2026-11-18): não** (mesma exceção da resposta humana, ADR 2026-11-17): é resposta a um pedido do próprio vendedor, que pode ser liberado dias depois. Exige estender a ADR. |
| **D5** | Pessoa `declined` pode ser editada? | **Decidido (Caio, 2026-11-18): sim** (corrigir dados antes de reenviar o aviso). |
| **D6** | Quem pode editar? | **Decidido (Caio, 2026-11-18): qualquer admin do painel**, como o resto do cadastro. |

## 7. Correção dos requisitos na Fase 2

**Validade sai da edição.** O cadastro nunca grava `valid_until` (`users.validate_row` devolve sempre `None`, o formulário não tem o campo e a importação também não). Editar algo que não existe em nenhuma outra tela fica fora: os campos editáveis são nome, perfil, empresa, UFs, canais, marcas e gestor (CA-1, CA-2, CA-8). Se a validade do parceiro externo for necessária, é história própria.

## 8. Design

### 8.1 Rotas (novo `app/panel/api/pessoas_editar.py`, descoberto sozinho pelo painel)

| Rota | O que faz |
|---|---|
| `GET /dashboard/pessoas/{id}/editar` | Modal com o `_pessoa_form.html` em modo edição: preenchido, telefone só leitura, campo oculto `versao` (o `updated_at` lido). Pessoa `revoked` ou anonimizada: 409 com o aviso (CA-6). |
| `POST /dashboard/pessoas/{id}/editar` | `require_csrf`. Valida com `users.validate_row` (o telefone atual entra na linha, então a regra é a mesma do cadastro) e chama `users.update`. Sucesso: `HX-Trigger` (`pessoasChanged`, `toast`, `closeOverlays`), como o cadastro; erro de campo ou conflito: o próprio formulário de volta. Depois do COMMIT, as mensagens de CA-11 vão ao despachante (`app.state.outbound_dispatcher`) por BackgroundTask, como a resposta humana. |
| `GET /dashboard/pessoas/{id}/trocar-numero` | Modal com o campo do número novo (mesmo estilo do modal de retirar). |
| `POST /dashboard/pessoas/{id}/trocar-numero` | `require_csrf`. Chama `users.swap_number` com o telefone atual da pessoa, `actor_type="panel"`. Sucesso: `HX-Redirect` para a ficha nova. `DuplicatePhone`/`InvalidValue`: o modal de volta com o erro (CA-13). |

A ficha (`_pessoa_ficha.html`) ganha os botões "Editar" e "Trocar número" só para `pending_optin`, `active` e `declined`, com as classes que já existem no CSS.

### 8.2 Serviço (`app/services/users.py`)

`async def update(conn, user_id, data: ValidatedUser, *, expected_updated_at, actor, actor_type="panel", settings, clock) -> UpdateResult`, numa (sub)transação:

1. `SELECT ... FOR UPDATE`; não existe → `not_found`; `revoked` ou anonimizada → `not_editable`; `updated_at` diferente de `expected_updated_at` → `conflict` com os dados atuais (CA-5).
2. Compara campo a campo; nada mudou → `noop` (CA-4).
3. `UPDATE access.users SET name, profile, company, ufs, channels, brands, manager_name, updated_at` (status, aceite e telefone não estão no UPDATE: CA-7). Se `active_brand` não está nas marcas novas, zera `active_brand`/`active_brand_at` no mesmo UPDATE (CA-10).
4. Para cada marca **acrescentada** com pedido de acesso aberto (`chat.tickets`, `status='awaiting'`, `escalation_reason='brand_access_request'`, mesma pessoa e marca): conclui cada pedido com `tickets_admin.conclude` (autor = primeiro nome do admin, auditado como hoje). Se a pessoa está `active`, enfileira **uma** saída `brand_access_granted` por marca, com `TEXT_BRAND_ACCESS_GRANTED` ("Seu acesso a {marca} foi liberado. Pode perguntar!") e `ticket_id` do pedido mais recente (CA-11).
5. Audita `user.update`: `old`/`new` só com perfil, UFs, canais e marcas; a lista `changed` com os nomes de todos os campos; nome, empresa e gestor nunca com valor (CA-8).

`UpdateResult(status: ok|noop|conflict|not_editable|not_found, changed: tuple[str, ...], granted: tuple[str, ...], outbound_ids: tuple[int, ...], current: UserRow | None)`.

### 8.3 Saída nova `brand_access_granted`

- Migration **403** (`403_brand_access_granted.sql`): recria o CHECK de `chat.outbound_messages.kind` com o kind novo (mesmo padrão da 201/301/401: acha o CHECK pelo catálogo e recria). Sem tabela nova; as permissões do `jarvis_app` não mudam (porta-fechada.2).
- `OutboundKind.BRAND_ACCESS_GRANTED`; `REQUIRED_STATUS` = `active`.
- **Janela de 24 h (D4):** o kind entra nos isentos junto com `human_reply` (`outbox.HUMAN_REPLY_EXEMPT` vira o conjunto dos dois, com o mesmo interruptor `HUMAN_REPLY_IGNORE_WINDOW`). A pausa do Jarvis **segura** esta mensagem (diferente da resposta humana): é aviso automático, não texto escrito pela equipe.
- ADR de 2026-11-17 estendida: "a liberação de marca pedida pelo vendedor também sai fora da janela".

### 8.4 Trocar número

Reaproveita `users.swap_number` inteiro (mesma transação, revoga com `number_change`, cria `pending_optin`, aviso de privacidade na fila, auditoria `user.number_swapped`). A rota só lê o telefone atual pelo id, para o admin nunca digitar o número antigo.

### 8.5 Testes

- `BT/integration/test_users_update.py`: cada status de `UpdateResult`, auditoria sem PII, marca da conversa zerada, pedido concluído e mensagem enfileirada só para `active`, uma mensagem por marca, nada enfileirado sem pedido.
- `BT/panel/integration/test_pessoas_editar.py`: GET/POST de edição e de troca, conflito, revogada sem botão, CSRF, `HX-Redirect` para a ficha nova, erro de duplicado com 9º dígito.
- `BT/integration/test_migration_403.py`; `BT/integration/test_outbox_dispatch.py` (kind novo fora da janela; pausa segura).
- Segurança: `test_route_sweep` e `test_pii_in_logs` com as rotas novas; `AC_MAP.md` com a seção "Cadastro" (CA-1..CA-14).

## 9. Arquivos

| Ação | Arquivo |
|---|---|
| Create | `B/app/panel/api/pessoas_editar.py`, `F/templates/_pessoa_trocar_numero.html`, `B/migrations/403_brand_access_granted.sql`, os testes de 8.5 |
| Modify | `B/app/services/users.py`, `B/app/core/values.py`, `B/app/services/outbox.py`, `B/app/config.py`, `F/templates/_pessoa_form.html` (modo edição), `F/templates/_pessoa_ficha.html` (botões), `BT/panel/security/test_route_sweep.py`, `BT/panel/security/test_pii_in_logs.py`, `BT/panel/AC_MAP.md`, `BT/panel/test_ac_coverage.py` |
| Docs | este spec, `task.md`, `docs/stories/cadastro.1.md`, `docs/STATE.md`, ADR (extensão) em `docs/architecture/overview.md` |

## 10. Riscos

- **Formulário compartilhado:** o modo edição muda `_pessoa_form.html`, usado pelo cadastro e pela autorização de pedido; os testes atuais do cadastro protegem esses fluxos.
- **Mensagem automática fora da janela (D4):** aumenta um pouco o risco de restrição no WAHA; mitigado por sair só quando o próprio vendedor pediu a marca e por ser uma mensagem por marca.
- **Corrida com o vendedor:** o vendedor pode mandar mensagem enquanto o admin edita; o `FOR UPDATE` na linha da pessoa serializa a edição com o processamento da mensagem.

## 11. Divergências da Fase 4 (2026-11-19)

Detalhes em `../task.md` ("Desvios do design"). Resumo: a conclusão dos pedidos e o aviso ficaram em `app/panel/services/people_edit.py` (o serviço de usuários não importa o painel); a versão do formulário sempre avança; a troca de número é feita pelo id (`users.swap_number_by_id`) e devolve a ficha nova no modal; o E2E opcional da edição (CA-T06) não foi feito.

