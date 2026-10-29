---
task_id: PAINEL-1
phase: 3-tasks
status: approved
approved_by: Caio
approved_at: 2026-10-23
spec: spec/spec.md
total_tasks: 70
total_waves: 16
estimated_hours: 245
generated: 2026-10-23
---

# Task breakdown - PAINEL-1 (Painel de gestao do Jarvis)

Fonte: `spec/spec.md` (4, 5, 8, 9.1), `spec/requirements.json`, `spec/complexity.json`, `spec/research.json`, prototipo `docs/designs/painel-prototipo.html`. Mapa AC: tabela de grupos do spec secao 3 (PN-1..PN-67; PN-46, PN-62 e PN-64 removidos).

## Convencoes

- **IDs:** `PN-T01..PN-T71`. O numero **T45 nao existe** (pulado de proposito); ha 70 tarefas. Duracao 1-4 h (inclui os testes da propria tarefa). Commit: `type(painel): PAINEL-1.Txx - descricao`.
- **Pre-requisitos externos: "PF foundations"** (dono: porta-fechada.1; os IDs PF-xx entram quando o task.md de PF existir). Componentes referenciados por nome:
  - `PF skeleton+config` = `backend/app/{main,config}.py`, estrutura do pacote, logging JSON, `/health`
  - `PF db` = pool asyncpg, runner de migrations (`python -m app.db.migrate`), migrations `001-005` (inclui `chat.tickets` e `ops.v_tickets`), fixture de banco de teste
  - `PF users` = `users.validate_row`, `register`, `revoke`, `resend`, `attempts.dismiss`
  - `PF flags/policy` = `services/flags.py` (`paused`, `brand_enabled:*`, `team_group_enabled`, `set_bool`), `reply_policy.FlagsReplyPolicy`
  - `PF alerts` = `alerts.raise_alert(..., email=)`, `deliveries`, `outbox`, `audit`
  - `PF cli` = `app/cli.py` com grupo de comandos extensivel
- **O painel nunca reimplementa nem edita** o que e de PF. Excecoes aditivas ja aprovadas: `main.py`, `config.py`, `cli.py` (so as tarefas que listam esses arquivos).
- **Fase 4 so comeca com as PF foundations aplicadas no banco de teste.** Cada tarefa indica qual componente PF exige.
- **Verificacao padrao** de toda tarefa: o `pytest` indicado + `ruff check backend` (+ `mypy backend/app` quando ha codigo). Para templates, tambem `pytest BT/unit/test_templates_rules.py -q` (criado em T07). Caminhos relativos a raiz do repo; `B/` = `backend/`, `F/` = `frontend/`, `BT/` = `backend/tests/panel/`.
- **Parciais de outras tarefas:** `{% include "_x.html" ignore missing %}` quando o parcial pertence a tarefa da mesma onda ou posterior; os testes de cada tarefa nao podem exigir conteudo de parcial alheio.
- **Tailwind:** nenhuma tarefa de tela commita `tailwind.css`; T03 gera o inicial e **T68 regenera** no fim. Durante o desenvolvimento, `npm run build:css` local sem commitar.
- **Ambiente:** Windows, Python 3.14 via `py` (codigo compativel com 3.12), pytest. Node/Tailwind e Playwright so na maquina de desenvolvimento e **opcionais** (T03, T65, T66). Sem Gemini real (assistente desligado). Sem CDN: vendor local com hash registrado.

## Desvios de arquivo em relacao ao spec 4.2/4.5 (para escopos disjuntos; mesmo comportamento)

1. Routers extras, um por responsabilidade: `api/ticket_drawer.py`, `inbox_chat.py`, `faq_form.py`, `faq_import.py`, `pessoas_{cadastro,ficha,pedidos,importar,lgpd}.py`, `controls.py`, `logs_{auditoria,retencao}.py`, `assistente_perguntas.py`. `routes.py` os descobre sozinho (T06), entao nenhuma tarefa de tela edita `routes.py`.
2. Abas Auditoria e Retencao de Logs viram sub-rotas HTMX `/dashboard/logs/auditoria` e `/dashboard/logs/retencao`; `?aba=` continua valendo como link que carrega a sub-rota.
3. `services/faq_read.py` (novo) para a listagem da FAQ, separado de `faq_admin.py`; `services/faq_seed.py` (novo) para a carga das 112.
4. `services/workers.py` nasce em T06 como registro minimo de laços; cada servico com laço expoe `LOOP`.
5. Migration `105` entra em T51 (inicio de P8), nao em P2, porque as views do assistente definem o que ele le.

---

## P0 - Casca

| ID | Titulo | h | depends_on | files_touched (whitelist) | AC | Verificacao |
|---|---|---|---|---|---|---|
| PN-T01 | Config, dependencias e paths | 2 | PF skeleton+config | `B/app/config.py` (aditivo, vars 4.11), `B/app/paths.py`, `B/requirements.txt` (jinja2, python-multipart, openpyxl, reportlab), `B/requirements-dev.txt` (playwright, opcional), `B/configuracao-modelo.txt`, `BT/__init__.py`, `BT/unit/__init__.py`, `BT/unit/test_config_panel.py` | base de PN-59; 4.11 (PANEL_ADMIN_EMAILS e PANEL_CSRF_SECRET sem padrao, fail-fast em producao) | `pytest BT/unit/test_config_panel.py -q` |
| PN-T02 | Valores, formatacao e mascara (funcoes puras) | 3 | PF skeleton+config | `B/app/panel/values.py`, `B/app/panel/services/formatting.py`, `B/app/panel/services/mask.py`, `BT/unit/test_values.py`, `BT/unit/test_formatting.py`, `BT/unit/test_mask.py` | PN-12 (separador de dia, "respondeu em X"), PN-59 (dd/mm/aaaa, America/Sao_Paulo, PT-BR), PN-31 (telefone mascarado `+55 85 9••••-1201`) | `pytest BT/unit/test_values.py BT/unit/test_formatting.py BT/unit/test_mask.py -q` |
| PN-T03 | Pipeline Tailwind (opcional: exige Node na maquina dev) | 3 | (nenhuma; le o prototipo) | `F/tailwind.config.js`, `F/package.json`, `F/package-lock.json`, `F/static/css/input.css`, `F/static/css/tailwind.css` (versao inicial) | PN-59 (visual fiel ao prototipo); 4.5 | `npm --prefix frontend run build:css`; segundo build sem diff em `tailwind.css` |
| PN-T04 | Vendor local e imagens dos agentes | 2.5 | (nenhuma) | `F/static/vendor/{htmx,alpine-csp,chart,marked,purify}.min.js`, `F/static/vendor/VENDOR.md` (versao, origem, sha384), `F/static/img/bots/*.png` (otimizadas de `docs/designs/bots/`), `BT/unit/test_vendor_manifest.py` | 4.4.4 (CSP sem CDN); seletor de marca com rosto do agente | `pytest BT/unit/test_vendor_manifest.py -q` (confere hashes e imagens) |
| PN-T05 | Infra de teste do painel | 4 | PF db (runner + 001-005 + fixture de banco), PN-T01 | `BT/conftest.py`, `BT/factories.py` (duas marcas; 3 vendedores: uma marca / duas marcas / so midia; tickets de todos os status; FAQ de 25 itens), `BT/fakes.py` (`FakeClock`, IAM `httpx.MockTransport` 200/401/timeout/5xx, spies Resend/WAHA/canal), `BT/unit/test_factories.py` | 4.12 (todas as camadas) | `pytest BT/unit/test_factories.py -q` |
| PN-T06 | Montagem do pacote, cabecalhos e registro de laços | 4 | PN-T01, PF skeleton+config (`main.py`) | `B/app/panel/__init__.py`, `B/app/panel/routes.py` (descobre `panel/api/*.py` com `router`), `B/app/panel/templating.py` (Jinja com autoescape, filtro `sp_dt`, `static_v`), `B/app/panel/headers.py` (CSP `script-src 'self'`, `no-store, private`), `B/app/panel/services/workers.py` (registro de `LOOP`, trava consultiva), `B/app/main.py` (aditivo: monta se `PANEL_ENABLED`), `BT/unit/test_panel_mount.py`, `BT/security/test_headers.py`, `BT/unit/test_architecture.py` | 4.1 (webhook nao importa `app.panel`; `PANEL_ENABLED=false` sem rotas); 4.4.4 | `pytest BT/unit/test_panel_mount.py BT/security/test_headers.py BT/unit/test_architecture.py -q` |
| PN-T07 | Templates base, macros e teste de regras de template | 4 | PN-T02, PN-T03, PN-T06 | `F/templates/base.html` (modal, drawer, toast, `hx-headers` CSRF, include opcional do widget), `F/templates/_nav.html` (menu completo do prototipo), `F/templates/_macros.html`, `F/templates/_banner_estado.html`, `F/templates/404.html`, `F/templates/500.html`, `F/static/js/base.js` (componentes Alpine CSP: modal, drawer, selecao), `BT/unit/test_templates_rules.py` | PN-43 (nada quebra com listas vazias), PN-59; 4.12 (sem `\|safe`, `confirm(`, `alert(`, "Beto", CDN, `onclick`; sem campo de resposta ao vendedor; modal com `role="dialog"`) | `pytest BT/unit/test_templates_rules.py -q` |

## P1 - Acesso

| ID | Titulo | h | depends_on | files_touched | AC | Verificacao |
|---|---|---|---|---|---|---|
| PN-T08 | Migrations 101 (FAQ) e 103 (ops do painel) | 3 | PF db, PN-T05 | `B/migrations/101_content_faq.sql` (inclui gatilho de `faq_revision`), `B/migrations/103_ops_panel.sql` (`panel_sessions`, `import_batches`, `assistant_questions`, indices de `audit_log`/`alerts`), `BT/integration/test_migrations_101_103.py` | base de PN-24, PN-25, PN-29, PN-40, PN-3, PN-53 | `pytest BT/integration/test_migrations_101_103.py -q` (esquema, CHECKs, unico parcial `faq_active_question_uq`) |
| PN-T09 | Migrations 102 (tickets: busca e indices), 104 (busca de pessoas) e 107 (opcional) | 3 | PF db (005 aplicada), PN-T05 | `B/migrations/102_tickets_panel.sql`, `B/migrations/104_access_search.sql`, `B/migrations/107_trgm_optional.sql` (condicional a `pg_trgm`), `BT/integration/test_migrations_102_104.py` | base de PN-8, PN-31, PN-59; 4.3.3 (visao so ganha coluna) | `pytest BT/integration/test_migrations_102_104.py -q` (busca sem acento; colunas de PF intactas na visao) |
| PN-T10 | Cliente IAM | 3.5 | PN-T01, PN-T05 | `B/app/panel/auth/__init__.py`, `B/app/panel/auth/iam.py` (whoami, LRU 30 s, single-flight, disjuntor 3 falhas/10 s, timeout 3 s, allow-list SSRF, `next` so para hosts permitidos), `BT/unit/test_iam_client.py` | PN-1, PN-3 | `pytest BT/unit/test_iam_client.py -q` |
| PN-T11 | Sessao do painel e CSRF | 4 | PN-T08, PN-T05, PN-T01 | `B/app/panel/auth/sessions.py` (cookie opaco `jarvis_panel`, so hash no banco, ociosa 2 h / absoluta 12 h, revalida a cada 5 min, 401 revoga, IAM fora nao derruba sessao viva), `B/app/panel/auth/csrf.py` (HMAC + Origin/Referer, tempo constante), `BT/integration/test_sessions.py`, `BT/unit/test_csrf.py` | PN-1, PN-3, PN-4 | `pytest BT/integration/test_sessions.py BT/unit/test_csrf.py -q` |
| PN-T12 | Escopo por marca | 2 | PN-T02, PN-T05 | `B/app/panel/services/scope.py`, `BT/unit/test_brand_scope.py` | PN-5, PN-6 | `pytest BT/unit/test_brand_scope.py -q` |
| PN-T13 | Gate de admin, dependencias e rotas de sistema | 4 | PN-T07, PN-T10, PN-T11, PN-T12 | `B/app/panel/deps.py` (`current_admin`, `require_csrf`, `brand_scope`), `B/app/panel/api/system.py` (`/`, `/dashboard`, `sair`, `marca`, `estado`, entrada/retorno do IAM), `F/templates/403.html`, `F/templates/503_iam.html`, `BT/integration/test_access.py` | PN-1, PN-2 (403 sem dados; `panel.forbidden` limitado a 1/min), PN-3 (pagina 503), PN-4, PN-5 (cookie `jarvis_brand`) | `pytest BT/integration/test_access.py -q` |
| PN-T14 | Varredura de rotas (autorizacao e CSRF) | 3 | PN-T13 | `BT/security/test_route_sweep.py` (enumera `app.routes`: sem sessao, nao-admin, admin sem CSRF), `BT/security/test_no_data_in_403.py` | PN-2, PN-4 | `pytest BT/security/test_route_sweep.py BT/security/test_no_data_in_403.py -q` (teste vivo: rotas das ondas seguintes entram sozinhas) |

## P2 - Leitura da FAQ pelo bot

| ID | Titulo | h | depends_on | files_touched | AC | Verificacao |
|---|---|---|---|---|---|---|
| PN-T15 | `FaqReader` por revisao | 3 | PN-T08, PN-T05 | `B/app/services/faq_reader.py`, `BT/integration/test_faq_reader.py` | PN-25 (59 s e 301 s com `FakeClock`; revisao reflete sem esperar o teto de 60 s), PN-24 (desativada nao e lida), `bot_responde=false` devolvido com a flag | `pytest BT/integration/test_faq_reader.py -q` |

## P3 - Tickets, Inbox, Escalados

| ID | Titulo | h | depends_on | files_touched | AC | Verificacao |
|---|---|---|---|---|---|---|
| PN-T16 | Leitura de tickets (lista, KPIs, busca) | 3.5 | PN-T09, PN-T12, PN-T05 | `B/app/panel/services/tickets_read.py`, `BT/integration/test_tickets_read.py` | PN-7 (5 KPIs), PN-8 (busca, classificacao, status, periodo), PN-6, PN-59 (consulta unica `COUNT FILTER`, 25 por pagina) | `pytest BT/integration/test_tickets_read.py -q` |
| PN-T17 | Escrita de tickets (concluir, reclassificar, urgencia, mover) | 3.5 | PN-T09, PN-T05, PF alerts (audit) | `B/app/panel/services/tickets_admin.py`, `BT/integration/test_tickets_admin.py` | PN-16 (nada enviado; autor obrigatorio), PN-17 (dois `concluir` concorrentes), PN-18, PN-21 (no-op com aviso se ja concluido), PN-63, PN-55 (auditoria na mesma transacao) | `pytest BT/integration/test_tickets_admin.py -q` |
| PN-T18 | Pagina Tickets | 4 | PN-T07, PN-T13, PN-T16 | `B/app/panel/api/tickets.py`, `F/templates/tickets.html`, `F/templates/_tickets_kpis.html`, `F/templates/_tickets_tabela.html`, `F/static/js/tickets.js`, `BT/integration/test_tickets_page.py` | PN-7, PN-8, PN-9 (linha abre Inbox; "Nenhum ticket encontrado."), PN-43 | `pytest BT/integration/test_tickets_page.py BT/unit/test_templates_rules.py -q` |
| PN-T19 | Drawer do ticket e acoes | 4 | PN-T13, PN-T16, PN-T17 | `B/app/panel/api/ticket_drawer.py`, `F/templates/_ticket_drawer.html`, `F/static/js/drawer.js`, `BT/integration/test_ticket_drawer.py` | PN-15 (sem "Responder ao vendedor"), PN-16, PN-17 ("Ja concluido por X em hh:mm"), PN-18, PN-4 | `pytest BT/integration/test_ticket_drawer.py BT/unit/test_templates_rules.py -q` |
| PN-T20 | Leitura do Inbox e do chat | 4 | PN-T09, PN-T12, PN-T05 | `B/app/panel/services/inbox_read.py`, `BT/integration/test_inbox_read.py` | PN-10 (conversas/midias, so aguardando), PN-12 (horarios, separadores, "respondeu em X"), PN-6 (midia sem marca em todas), resposta `media_ack` exibida como "Bot", PN-59 (`LATERAL`) | `pytest BT/integration/test_inbox_read.py -q` |
| PN-T21 | Pagina Inbox (lista) | 4 | PN-T07, PN-T13, PN-T20 | `B/app/panel/api/inbox.py`, `F/templates/inbox.html`, `F/templates/_inbox_lista.html`, `F/static/js/inbox.js`, `BT/integration/test_inbox_page.py` | PN-10, PN-9, PN-43 ("Nada aguardando aqui.") | `pytest BT/integration/test_inbox_page.py BT/unit/test_templates_rules.py -q` |
| PN-T22 | Chat do Inbox, concluir e lote | 4 | PN-T19, PN-T20, PN-T21 | `B/app/panel/api/inbox_chat.py`, `F/templates/_inbox_chat.html`, `F/static/js/inbox_chat.js`, `BT/integration/test_inbox_chat.py` | PN-11 (Concluir em lote), PN-12, PN-13 (sem caixa de resposta; "Responder em: Grupo da equipe" desativado com a chave desligada, lida de PF flags), PN-14 (Tickets recentes abre o drawer), PN-16 | `pytest BT/integration/test_inbox_chat.py BT/unit/test_templates_rules.py -q` |
| PN-T23 | Pagina Escalados | 4 | PN-T13, PN-T16, PN-T17, PN-T19 | `B/app/panel/api/escalados.py`, `F/templates/escalados.html`, `F/templates/_kanban_coluna.html`, `F/templates/_kanban_cartao.html`, `F/static/js/escalados.js`, `BT/integration/test_escalados.py` | PN-19 (quadro por classificacao), PN-20 (botao Midias (N) respeita a marca), PN-21, PN-63, frase "08:05" so com a chave do grupo ligada (PN-60/61) | `pytest BT/integration/test_escalados.py BT/unit/test_templates_rules.py -q` |

## P4 - FAQ

| ID | Titulo | h | depends_on | files_touched | AC | Verificacao |
|---|---|---|---|---|---|---|
| PN-T24 | Validacao da FAQ (pura) | 3 | PN-T02 | `B/app/panel/services/faq_validation.py`, `BT/unit/test_faq_validation.py` | PN-23 (1-500, resposta obrigatoria se `bot_responde`, sem telefone/e-mail/link, duplicata por `question_key`, todos os problemas de uma vez), PN-28 (uma regra por caso) | `pytest BT/unit/test_faq_validation.py -q` |
| PN-T25 | Escrita da FAQ (versoes, otimista, desativar) | 4 | PN-T08, PN-T24, PN-T05 | `B/app/panel/services/faq_admin.py`, `BT/integration/test_faq_admin.py` | PN-23, PN-24 (nao apaga; auditado), PN-26 (`force=1`), versao em `faq_item_versions`, PN-55 | `pytest BT/integration/test_faq_admin.py -q` |
| PN-T26 | Pagina FAQ (lista e filtros) | 4 | PN-T07, PN-T08, PN-T13 | `B/app/panel/services/faq_read.py`, `B/app/panel/api/faq.py`, `F/templates/faq.html`, `F/templates/_faq_linha.html`, `F/static/js/faq.js`, `BT/integration/test_faq_page.py` | PN-22 (marca + `todas` primeiro, categoria, ativas/desativadas, busca, 10 por pagina), PN-43, PN-5 | `pytest BT/integration/test_faq_page.py BT/unit/test_templates_rules.py -q` |
| PN-T27 | Formulario da FAQ e acoes | 4 | PN-T07, PN-T13, PN-T25 | `B/app/panel/api/faq_form.py`, `F/templates/_faq_form.html`, `F/static/js/faq_form.js`, `BT/integration/test_faq_form.py` | PN-23, PN-24, PN-26 (aviso com texto atual), preenchimento por `?ticket=J-nnnn` (nunca por texto na URL), PN-4 | `pytest BT/integration/test_faq_form.py BT/unit/test_templates_rules.py -q` |
| PN-T28 | Servico de lotes de importacao (generico) | 3 | PN-T08, PN-T05 | `B/app/panel/services/import_batches.py`, `BT/integration/test_import_batches.py` | PN-29 (gravar atomico; segunda gravacao nao duplica via `status='preview'`), PN-40 (linhas de pessoas apagadas ao gravar/cancelar/expirar), TTL 2 h (FAQ) e 1 h (pessoas) | `pytest BT/integration/test_import_batches.py -q` |
| PN-T29 | Leitura e classificacao da planilha da FAQ | 4 | PN-T24, PN-T28 | `B/app/panel/services/faq_import.py` (xlsx `read_only`/`data_only` e CSV `,`/`;`/BOM em `asyncio.to_thread`, teto 2 MB e 200 linhas), `BT/unit/test_faq_import_rules.py`, `BT/integration/test_faq_import_service.py` | PN-27 (Entra / Fica de fora / Erro), PN-28 (cabecalho, tamanho, 200 linhas, regras por linha), PN-29 (revalida ao gravar; falha reverte tudo) | `pytest BT/unit/test_faq_import_rules.py BT/integration/test_faq_import_service.py -q` |
| PN-T30 | Importar FAQ: telas e rotas | 4 | PN-T07, PN-T13, PN-T25, PN-T29 | `B/app/panel/api/faq_import.py` (modelo .xlsx, enviar, conferencia reabrivel, gravar, cancelar), `F/templates/_faq_importar_form.html`, `F/templates/faq_importar_previa.html`, `F/static/js/faq_import.js`, `BT/integration/test_faq_import_routes.py` | PN-27, PN-28 (mensagem + link do modelo), PN-29, PN-4 | `pytest BT/integration/test_faq_import_routes.py BT/unit/test_templates_rules.py -q` |
| PN-T31 | Carga inicial das 112 perguntas (CLI) | 3 | PN-T24, PN-T25, PF cli, `piloto/guia.json` | `B/app/cli.py` (aditivo: `faq seed`), `B/app/panel/services/faq_seed.py`, `BT/integration/test_faq_seed.py` | PN-30 (idempotente por `(brand, question_key)`, `origin='seed'`, gerais em `todas`, saude sem resposta com `bot_responde=false`; total 112) | `pytest BT/integration/test_faq_seed.py -q` |

## P5 - Pessoas

| ID | Titulo | h | depends_on | files_touched | AC | Verificacao |
|---|---|---|---|---|---|---|
| PN-T32 | Leitura de pessoas | 4 | PN-T09, PN-T05, PF users | `B/app/panel/services/people_read.py` (tres abas, busca unica, `pending_requests` exclusivo e condicionado a `unknown_first_text_display`), `BT/integration/test_people_read.py` | PN-31 (abas, busca por nome/empresa/UF/telefone, "X de Y", 50 por pagina), PN-37 (so a primeira mensagem, so em Pedindo acesso, so com a chave ligada), PN-67 | `pytest BT/integration/test_people_read.py -q` |
| PN-T33 | Pagina Pessoas (3 abas) | 4 | PN-T07, PN-T13, PN-T32 | `B/app/panel/api/pessoas.py`, `F/templates/pessoas.html`, `F/templates/_pessoas_lista.html`, `F/static/js/pessoas.js`, `BT/integration/test_pessoas_page.py` | PN-31, PN-43; telefone mascarado fora da ficha | `pytest BT/integration/test_pessoas_page.py BT/unit/test_templates_rules.py -q` |
| PN-T34 | Cadastrar pessoa (e autorizar pedido) | 4 | PN-T07, PN-T13, PF users | `B/app/panel/api/pessoas_cadastro.py`, `F/templates/_pessoa_form.html`, `F/static/js/pessoa_form.js`, `BT/integration/test_pessoas_cadastro.py` | PN-32 (8 campos obrigatorios; erro nomeia o campo; boas-vindas na fila), PN-33 (telefone incompleto/duplicado, 9o digito), PN-36 (`?pedido=<attempt_id>` pre-preenche e autoriza), PN-4 | `pytest BT/integration/test_pessoas_cadastro.py BT/unit/test_templates_rules.py -q` |
| PN-T35 | Ficha, revogar, reativar e reenviar | 4 | PN-T07, PN-T13, PN-T32, PF users | `B/app/panel/api/pessoas_ficha.py`, `F/templates/_pessoa_ficha.html`, `F/templates/_pessoa_revogar.html`, `F/static/js/pessoa_ficha.js`, `BT/integration/test_pessoas_ficha.py` | PN-34 (motivo da lista fixa; resposta so apos COMMIT; auditoria), PN-35 (spy de envio sem chamadas), PN-39 (volta a `pending_optin`, aceite novo), sem botao Revogar para o proprio admin (tambem no servidor), PN-55 | `pytest BT/integration/test_pessoas_ficha.py BT/unit/test_templates_rules.py -q` |
| PN-T36 | Pedindo acesso: ignorar, exibicao da 1a mensagem e botao da chave `unknown_first_text_display` (so admin, com auditoria; decisao do Caio 2026-10-26) | 4 | PN-T13, PN-T32, PN-T33 | `B/app/panel/api/pessoas_pedidos.py` (ignorar), `F/templates/_pessoas_pedidos.html`, `BT/integration/test_pessoas_pedidos.py` | PN-38 (volta se escrever de novo; contagem preservada), PN-37 (texto so aqui; desligado por padrao), PN-67 (nenhum `raise_alert`/Resend por desconhecido) | `pytest BT/integration/test_pessoas_pedidos.py BT/unit/test_templates_rules.py -q` |
| PN-T37 | Leitura e validacao do CSV de pessoas | 4 | PN-T28, PN-T05, PF users (`validate_row`, `register`) | `B/app/panel/services/people_import.py`, `BT/unit/test_people_import_rules.py`, `BT/integration/test_people_import_service.py` | PN-40 (cabecalho de 8 campos; multiplos por `\|` ou `;`; duplicata no arquivo e na base por `phone_key`; grava so as validas e reporta as invalidas; lote perde as linhas ao terminar), PN-33 | `pytest BT/unit/test_people_import_rules.py BT/integration/test_people_import_service.py -q` |
| PN-T38 | Importar pessoas: telas e rotas | 4 | PN-T07, PN-T13, PN-T37 | `B/app/panel/api/pessoas_importar.py`, `F/templates/_pessoas_importar_form.html`, `F/templates/pessoas_importar_previa.html`, `F/static/js/pessoas_importar.js`, `BT/integration/test_pessoas_importar_routes.py` | PN-40, PN-4 | `pytest BT/integration/test_pessoas_importar_routes.py BT/unit/test_templates_rules.py -q` |
| PN-T39 | LGPD: exportar e excluir (servico) | 4 | PN-T05, PF users, PF db | `B/app/panel/services/lgpd.py`, `BT/integration/test_lgpd.py` | PN-56 (so dados da pessoa; auditoria sem PII), PN-57 (anonimiza em uma transacao; `unknown_first_messages` apagada; contagens auditadas; some das buscas) | `pytest BT/integration/test_lgpd.py -q` |
| PN-T40 | LGPD: botoes e modal na ficha | 3 | PN-T13, PN-T35, PN-T39 | `B/app/panel/api/pessoas_lgpd.py` (POST exportar com `no-store`, POST excluir), `F/templates/_pessoa_lgpd.html` (modal exige digitar o nome), `BT/integration/test_pessoas_lgpd_routes.py` | PN-56, PN-57, PN-4 (nenhum GET dispara exportacao/exclusao) | `pytest BT/integration/test_pessoas_lgpd_routes.py BT/unit/test_templates_rules.py -q` |

## P6 - Controles e alertas

| ID | Titulo | h | depends_on | files_touched | AC | Verificacao |
|---|---|---|---|---|---|---|
| PN-T41 | Monitor de sistema | 4 | PN-T05, PN-T06 (workers), PF alerts | `B/app/panel/services/monitor.py` (`volume_spike`, `error_rate`, `api_failure`; laço de 10 min com `LOOP`; dedup por `dedup_key`; limiares em config), `BT/integration/test_monitor.py` | PN-44 (tipos de sistema), PN-65 (alerta novo gera e-mail via PF, sem PII) | `pytest BT/integration/test_monitor.py -q` |
| PN-T42 | Pagina Alertas | 4 | PN-T07, PN-T13 | `B/app/panel/api/alerts.py` (lista, filtros, `ack`, `?abrir=<id>`), `F/templates/alerts.html`, `F/templates/_alert_card.html`, `F/static/js/alerts.js`, `BT/integration/test_alerts_page.py` | PN-44 (so sistema e seguranca; rotulos PT-BR incluindo `welcome_failed` e `identity_conflict`), PN-43 ("Nenhum alerta. Sistema saudavel."), PN-5 | `pytest BT/integration/test_alerts_page.py BT/unit/test_templates_rules.py -q` |
| PN-T43 | Controles: pausa e marcas | 4 | PN-T07, PN-T13, PF flags/policy, PF alerts | `B/app/panel/api/controls.py`, `F/templates/_control_pausa.html`, `F/templates/_control_marcas.html`, `F/static/js/controls.js`, `BT/integration/test_alerts_controls.py` | PN-45 (pausa/retomada; consentimento nao bloqueado), PN-47 (marca; agente de cada marca), PN-48 (estado desejado, idempotente, modal mostra o estado atual, dois POST nao alternam), auditoria + `raise_alert` so se mudou (PN-55) | `pytest BT/integration/test_alerts_controls.py BT/unit/test_templates_rules.py -q` |
| PN-T44 | Testes de integracao: e-mail, grupo e "nao e alerta" | 3.5 | PN-T41, PN-T42, PN-T43, PF alerts (deliveries/Resend), PF flags/policy | `BT/integration/test_email_alerts.py`, `BT/integration/test_team_group_flag.py`, `BT/integration/test_no_alert_counts.py` | PN-65 (conteudo minimo; link `.../alerts?abrir=<id>`), PN-66 (falha do Resend nao bloqueia, retenta e registra), PN-60 (desligado: nada ao WAHA), PN-61 (ligado: aviso e resumo 08:05), PN-49, PN-44/PN-67 (escalado, midia e desconhecido: zero alertas e zero e-mails) | `pytest BT/integration/test_email_alerts.py BT/integration/test_team_group_flag.py BT/integration/test_no_alert_counts.py -q` |

## P7 - Metricas e Relatorios

| ID | Titulo | h | depends_on | files_touched | AC | Verificacao |
|---|---|---|---|---|---|---|
| PN-T46 | Consultas de Metricas | 4 | PN-T09, PN-T12, PN-T05 | `B/app/panel/services/metrics_read.py` (cache 60 s por marca e periodo), `BT/integration/test_metrics_read.py` | PN-41 (KPIs, mensagens por dia, por marca, tempo de resposta p50/p95, bloqueios), PN-43 (sem divisao por zero), "sem avaliacoes" | `pytest BT/integration/test_metrics_read.py -q` |
| PN-T47 | Pagina Metricas e graficos | 4 | PN-T04 (chart), PN-T07, PN-T13, PN-T46 | `B/app/panel/api/metrics.py`, `F/templates/metrics.html`, `F/templates/_metrics_kpis.html`, `F/templates/_metrics_charts.html`, `F/static/js/metrics-charts.js`, `BT/integration/test_metrics_page.py` | PN-41, PN-43 (estado vazio sem grafico quebrado), CSP (dados em `<script type="application/json">`) | `pytest BT/integration/test_metrics_page.py BT/unit/test_templates_rules.py -q` |
| PN-T48 | Consultas de Relatorios | 4 | PN-T09, PN-T12, PN-T05 | `B/app/panel/services/reports.py`, `BT/integration/test_reports.py` | PN-42 (perguntas frequentes, objecoes, sem resposta, dados do mensal), PN-43 | `pytest BT/integration/test_reports.py -q` |
| PN-T49 | PDF mensal | 3 | PN-T01, PN-T48 | `B/app/panel/services/report_pdf.py` (ReportLab em `asyncio.to_thread`), `BT/unit/test_report_pdf.py` | PN-42 (PDF valido com KPIs, tickets por marca/classificacao, top 10, tempo de resposta; mes sem dados gera PDF coerente) | `pytest BT/unit/test_report_pdf.py -q` |
| PN-T50 | Pagina Relatorios e rota do PDF | 3 | PN-T07, PN-T13, PN-T48, PN-T49 | `B/app/panel/api/relatorios.py`, `F/templates/relatorios.html`, `BT/integration/test_relatorios_page.py` | PN-42 ("Criar na FAQ" abre `faq/nova` pelo ticket/ID, sem texto na URL), PN-43, PN-4 | `pytest BT/integration/test_relatorios_page.py BT/unit/test_templates_rules.py -q` |

## P8 - Assistente e Perguntas

| ID | Titulo | h | depends_on | files_touched | AC | Verificacao |
|---|---|---|---|---|---|---|
| PN-T51 | Migration 105: papel somente leitura e views sem PII | 4 | PN-T08, PN-T05, PF db | `B/migrations/105_roles_assistant.sql` (papel `jarvis_assistant`, schema `assistant`), `BT/integration/test_assistant_role.py` | PN-52 (INSERT/UPDATE/DELETE negados; sem acesso a `message_trace.body`, `unknown_first_messages`, telefones, `audit_log`) | `pytest BT/integration/test_assistant_role.py -q` |
| PN-T52 | Provedores do assistente | 4 | PN-T01, PN-T05 | `B/app/panel/assistant/__init__.py`, `B/app/panel/assistant/provider.py`, `B/app/panel/assistant/disabled.py`, `B/app/panel/assistant/gemini.py` (httpx REST, `MockTransport`), `BT/unit/test_assistant_providers.py` | PN-50 (estado desligado), PN-52 (rodadas de ferramenta), 4.10 (timeout 60 s, allow-list SSRF) | `pytest BT/unit/test_assistant_providers.py -q` |
| PN-T53 | Agentes e escolha por marca | 2.5 | PN-T02 | `B/app/panel/assistant/agents.py`, `BT/unit/test_assistant_agents.py` | PN-51 (sem acento: "castanha", "atal", "plant power", "plantpower"; zero ou varias marcas = Jarvis), nenhuma ocorrencia de "Beto" | `pytest BT/unit/test_assistant_agents.py -q` |
| PN-T54 | Registro de perguntas | 2.5 | PN-T08, PN-T05 | `B/app/panel/assistant/log.py`, `BT/integration/test_assistant_log.py` | PN-53 (resposta e erro gravados; contadores de 30 dias e pessoas distintas) | `pytest BT/integration/test_assistant_log.py -q` |
| PN-T55 | Ferramentas e servico do assistente | 4 | PN-T51, PN-T52, PN-T53, PN-T54 | `B/app/panel/assistant/tools.py`, `B/app/panel/assistant/service.py`, `BT/integration/test_assistant_service.py` | PN-52 (pool proprio, `read_only`, `statement_timeout=5s`, resultado como dado delimitado, sem ferramenta de escrita), PN-53, limite diario | `pytest BT/integration/test_assistant_service.py -q` |
| PN-T56 | API do assistente | 3.5 | PN-T13, PN-T55 | `B/app/panel/api/assistant.py` (`GET /api/assistant/status`, `POST /api/assistant/chat`), `BT/integration/test_assistant_api.py` | PN-50 (503 sem registrar quando desligado), PN-53, limites (12/min, 4.000 caracteres), PN-4 | `pytest BT/integration/test_assistant_api.py -q` |
| PN-T57 | Widget flutuante | 4 | PN-T04, PN-T07, PN-T56 | `F/templates/_assistant_widget.html`, `F/static/js/assistente.js`, `BT/integration/test_assistant_widget.py` | PN-50 (abre desligado, chips desabilitados, mensagem "O assistente sera ativado quando a chave do Gemini for configurada"), PN-51 (cabecalho mostra o agente da ultima resposta) | `pytest BT/integration/test_assistant_widget.py BT/unit/test_templates_rules.py -q` |
| PN-T58 | Pagina Perguntas | 2.5 | PN-T07, PN-T13, PN-T54 | `B/app/panel/api/assistente_perguntas.py`, `F/templates/assistente_perguntas.html`, `BT/integration/test_perguntas_page.py` | PN-53, PN-43 | `pytest BT/integration/test_perguntas_page.py BT/unit/test_templates_rules.py -q` |

## P9 - Logs, auditoria, retencao

| ID | Titulo | h | depends_on | files_touched | AC | Verificacao |
|---|---|---|---|---|---|---|
| PN-T59 | Auditoria: leitura e aba | 3.5 | PN-T07, PN-T09, PN-T13 | `B/app/panel/services/audit_read.py`, `B/app/panel/api/logs_auditoria.py`, `F/templates/_logs_auditoria.html`, `BT/integration/test_audit_tab.py` | PN-55 (filtro por acao e alvo; sem PII; falha de auditoria aborta a escrita, testada na camada de servico) | `pytest BT/integration/test_audit_tab.py BT/unit/test_templates_rules.py -q` |
| PN-T60 | Logs e Erros (problemas e servidor) | 4 | PN-T07, PN-T09, PN-T13 | `B/app/panel/api/logs.py`, `F/templates/logs.html` (abas apontam as sub-rotas de T59/T62), `F/templates/_logs_problemas.html`, `F/templates/_logs_servidor.html`, `BT/integration/test_logs_page.py` | PN-54 (Ticket/Data/Problema/Detalhe; final do log; telefone mascarado, sem corpo de mensagem), PN-43 | `pytest BT/integration/test_logs_page.py BT/unit/test_templates_rules.py -q` |
| PN-T61 | Rotina de retencao | 4 | PN-T08, PN-T05, PN-T06 (workers), PF db | `B/app/panel/services/retention.py` (`LOOP` 02:30 America/Sao_Paulo, lotes de 5.000, ordem das FKs, `RETENTION_DRY_RUN`, funcao de leitura do volume), `BT/integration/test_retention.py` | PN-58 (12 meses e 1 dia some; dry-run so conta; `retention.purge` com contagens; piso `>= now - 12 meses`) | `pytest BT/integration/test_retention.py -q` |
| PN-T62 | Aba Retencao | 3 | PN-T07, PN-T13, PN-T61 | `B/app/panel/api/logs_retencao.py`, `F/templates/_logs_retencao.html`, `BT/integration/test_retention_tab.py` | PN-58 (volume por mes; o que expira em 30 dias) | `pytest BT/integration/test_retention_tab.py BT/unit/test_templates_rules.py -q` |

## P10 - Fechamento

| ID | Titulo | h | depends_on | files_touched | AC | Verificacao |
|---|---|---|---|---|---|---|
| PN-T63 | Suite de seguranca | 4 | T14 e todas as telas (T18-T23, T26, T27, T30, T33-T36, T38, T40, T42, T43, T47, T50, T57, T58, T59, T60, T62) | `BT/security/test_pii_in_logs.py`, `BT/security/test_unknown_text_only_in_pending.py` (varre templates e consultas), `BT/security/test_template_safety.py`, `BT/security/test_assistant_no_write.py`, `BT/security/test_rate_limit.py` (240/60 s por IP) | PN-37, PN-52, PN-54, PN-65 (e-mail sem telefone), PN-67, PN-2, PN-4, logs sem endereco do Caio | `pytest BT/security -q` |
| PN-T64 | Desempenho (marcador `perf`) | 4 | T09, T18, T21, T26, T33, T47 | `BT/perf/__init__.py`, `BT/perf/seed.py` (2.000 usuarios, 20 mil mensagens/dia por 30 dias, 200 mil tickets), `BT/perf/test_perf.py` | PN-59 (p95 <= 2 s em Tickets, Inbox, Pessoas, FAQ, Metricas) | `pytest BT/perf -m perf -q` (nao bloqueia PR; bloqueia release) |
| PN-T65 | E2E base (opcional: Playwright) | 4 | T13, T18, T19, T22, T23, T43 | `F/e2e/auth_marca.spec.ts`, `F/e2e/drawer.spec.ts`, `F/playwright.config.ts` | PN-1 (IAM mockado), PN-5 (marca persiste entre telas), PN-14, PN-15 | `npx --prefix frontend playwright test e2e/auth_marca.spec.ts e2e/drawer.spec.ts` (sem Playwright: registrar "nao executado", sem bloquear) |
| PN-T66 | E2E modais e widget (opcional: Playwright) | 3.5 | T57, T65 | `F/e2e/modais.spec.ts`, `F/e2e/assistente.spec.ts` | PN-45/47/48 (modal, nunca `confirm()`), PN-50 (widget desligado e com provedor falso) | `npx --prefix frontend playwright test e2e/modais.spec.ts e2e/assistente.spec.ts` (mesma regra de opcionalidade) |
| PN-T67 | Mapa AC -> teste | 2.5 | T63, T64, T65, T66 e todos os testes das telas | `BT/test_ac_coverage.py` (cada PN ativo aparece em ao menos um teste automatizado ou na lista "manual"), `BT/AC_MAP.md` | os 64 PN ativos (manuais: revisao visual de PN-59 e smoke com o Caio) | `pytest BT/test_ac_coverage.py -q` |
| PN-T68 | CSS final, comandos de verificacao e roteiro de revisao visual | 3 | todas as telas (T18-T62), T03 | `F/static/css/tailwind.css` (regenerado), `BT/unit/test_css_in_sync.py`, `.claude/CLAUDE.md` (so a secao Verification commands), `docs/STATE.md`, `docs/tasks/painel.1/visual-review.md` (checklist tela a tela contra o prototipo) | PN-59 (visual); 4.12 (comandos); divergencias do prototipo 1-11 | `npm --prefix frontend run build:css` sem diff em `tailwind.css`; `pytest BT/unit/test_css_in_sync.py -q` |

## P11 - Deploy (OPCIONAL, ultima onda; so arquivos, nenhuma acao no servidor)

| ID | Titulo | h | depends_on | files_touched | AC | Verificacao |
|---|---|---|---|---|---|---|
| PN-T69 | Anexo do nginx e modelo de variaveis | 2.5 | PN-T01 | `deploy/painel/nginx-painel-blocks.conf` (spec 8.3; mesclar em `deploy/nginx/jarvis.conf.template` e passo manual, dono PF), `deploy/painel/env-painel.example` (so nomes, sem valores), `BT/unit/test_deploy_artifacts.py` | 8.3, 8.2 passo 3 | `pytest BT/unit/test_deploy_artifacts.py -q` (locations, limites, ausencia de segredos) |
| PN-T70 | Runbook e roteiro de smoke | 3 | PN-T01 | `deploy/painel/RUNBOOK-painel.md` (checklist 8.2 e reversao 9; cada passo no servidor exige confirmacao do Caio), `deploy/painel/smoke-checklist.md` | 8.2 (smoke com o Caio), 9 | revisao manual contra spec 8.2 e 9 |
| PN-T71 | Copiar as 7 propostas de ADR para o overview | 1.5 | aprovacao explicita do Caio | `docs/architecture/overview.md` (so a secao 8, ADRs 1-7 de spec 9.1) | CLAUDE.md (mudanca em architecture exige ADR) | revisao manual; serializar com qualquer edicao de PF no mesmo arquivo |

---

## Parallel Execution Map

Regra: no maximo 5 tarefas por onda, arquivos disjuntos, nenhuma dependencia entre irmas. As PF foundations precisam estar prontas antes da W1 (skeleton+config) e antes da W2 (`PF db`); os demais componentes PF (users, flags/policy, alerts, cli) antes das tarefas que os citam.

| Onda | Tarefas | Horas | Observacao |
|---|---|---|---|
| W1 | T01, T02, T03, T04 | 10.5 | so dependem de PF skeleton+config |
| W2 | T05, T06, T24, T53 | 13.5 | T05 exige `PF db` |
| W3 | T07, T08, T09, T10, T12 | 15.5 | migrations 101/103 e 102/104 em arquivos distintos |
| W4 | T11, T16, T17, T20, T25 | 19 | |
| W5 | T13, T15, T28, T32, T41 | 18 | T13 libera todas as telas |
| W6 | T18, T19, T21, T51, T52 | 20 | telas P3 + inicio de P8 |
| W7 | T22, T23, T26, T48, T54 | 18.5 | |
| W8 | T27, T29, T33, T46, T55 | 20 | |
| W9 | T30, T34, T35, T37, T56 | 19.5 | |
| W10 | T36, T38, T47, T49, T57 | 18 | |
| W11 | T39, T42, T43, T50, T61 | 19 | |
| W12 | T40, T44, T58, T59, T60 | 16.5 | |
| W13 | T14, T31, T62, T64, T65 | 17 | T65 opcional |
| W14 | T63, T66, T68 | 10.5 | T66 opcional |
| W15 | T67 | 2.5 | |
| W16 (OPCIONAL, deploy so arquivos) | T69, T70, T71 | 7 | T71 so com aprovacao do Caio |

Totais: **70 tarefas, 16 ondas, 245 h** (238 h sem a W16; 230.5 h sem tambem os E2E opcionais T65/T66). Caminho critico: T01 -> T05 -> T08 -> T11 -> T13 -> T19/T21 -> T22, e a cadeia do assistente T52/T54 -> T55 -> T56 -> T57 -> T66.

### Verificacao das ondas (checagem feita na redacao)

- Dependencias dentro de cada onda: nenhuma (cada `depends_on` aponta para onda anterior ou para PF).
- Arquivos: cada arquivo pertence a uma unica tarefa. Excecoes aditivas em arquivo de PF: `main.py` (T06), `config.py` e `requirements*.txt` (T01), `cli.py` (T31). `.claude/CLAUDE.md` e `docs/STATE.md` so em T68. `tailwind.css`: T03 cria, T68 regenera (nunca na mesma onda).
- Parciais entre tarefas: `_ticket_drawer.html` (T19) usado por T22/T23 (ondas posteriores); `_pessoa_lgpd.html` (T40), `_pessoas_pedidos.html` (T36) e `_control_*` (T43) incluidos por T35/T33/T42 com `ignore missing`.

## Rastreabilidade PN -> tarefa

| PN | Tarefas |
|---|---|
| 1-4 | T10, T11, T13, T14 |
| 5-6 | T12, T13, T16, T20 |
| 7-9 | T16, T18, T21 |
| 10-14 | T20, T21, T22 |
| 15-18 | T17, T19 |
| 19-21, 63 | T17, T23 |
| 22-30 | T15, T24-T31 |
| 31-40, 67 | T32-T38 |
| 41-43 | T46-T50 |
| 44-48 | T41-T44 |
| 49, 60, 61 | T44 (+ PF), T22, T23 |
| 50-53 | T51-T58 |
| 54 | T60 |
| 55 | T17, T25, T35, T43, T59 |
| 56-57 | T39, T40 |
| 58 | T61, T62 |
| 59 | T02, T03, T09, T64, T68 |
| 65-66 | T41, T44 |

## Itens que precisam de decisao do Caio antes da Fase 4

1. **PF foundations primeiro.** A Fase 4 do painel so inicia depois que PF entregar skeleton+config, `PF db`, `PF users`, `PF flags/policy`, `PF alerts` e `PF cli`. Quando o task.md de PF existir, trocar os nomes de componente por IDs PF-xx.
2. **Chave `unknown_first_text_display`:** o spec so a define em `system_state` e nao ha tela para liga-la. Aprovar ligar/desligar pelo `flag set` do CLI de PF (sem UI), ou pedir um controle na aba Pedindo acesso.
3. **Desvios de arquivo** listados no topo (routers e parciais extras, abas de Logs como sub-rotas, `faq_read.py`, `faq_seed.py`, `105` em P8): aprovar.
4. **Node/Tailwind e Playwright** sao opcionais e so da maquina de desenvolvimento: T03, T65 e T66 ficam "nao executado" se faltarem; `tailwind.css` e versionado.
5. **Mesclagem no nginx/RUNBOOK de PF e ADRs no overview** (T69-T71) tocam arquivos que PF tambem edita: so depois de PF e com autorizacao; nada e aplicado no servidor.
6. **Questoes abertas da Fase 2 com padrao assumido** (Q3, Q7, Q17, Q-P1, Q-P2, Q-P3, Q-P5 a Q-P8, Q-P10) nao bloqueiam tarefas; Q-P7 (ReportLab ou pagina de impressao) afeta so T49.

## Decisoes do Caio na Fase 4 (2026-10-26)

- **Item 2 resolvido:** a chave `unknown_first_text_display` ganha **botao na aba Pedindo acesso** (so admin, grava em `system_state` via `Flags.set_bool(..., actor_type="panel")` com auditoria; desligada por padrao ate o aval juridico). Entra em PN-T36 (+1 h). A chave de PF que guarda o texto (`UNKNOWN_FIRST_TEXT_STORE`) continua so por configuracao.
- **Itens 3, 4 e 6:** aprovados como estao (lista aprovada em 2026-10-23).
- **Vendor (T04):** htmx 1.9.12, Alpine CSP 3.13.10 e Chart.js 4.4.6 baixados **uma unica vez** com autorizacao do Caio; sha384 identicos aos SRI que o AUTO-1 usa em producao. `marked`/`purify` copiados do AUTO-1.
- **Tailwind (T03):** sem `npm install`; o build usa o binario do Tailwind ja instalado no AUTO-1.
- **Playwright:** nao instalado; T65/T66 ficam "nao executado".
- **PF pronto:** todas as "PF foundations" existem (porta-fechada.1 concluida, PF-01..PF-41). Assinaturas finais nas notas ao fim de `docs/tasks/porta-fechada.1/task.md`.

## Notas da Fase 4 (ondas 1-12, 2026-10-26)

- **Resumo das 08:05 no grupo (PN-61): não implementado.** O grupo da equipe está desligado por decisão do Caio ("deixar o caminho pronto, não usar ainda"); o texto "Resumo automático no grupo todo dia 08:05" só aparece com a chave ligada. Teste `test_team_group_flag.py::test_resumo_diario_das_0805_existe_como_laco_do_painel` fica `xfail(strict=True)` até a tarefa que ligar o grupo implementar o laço.
- **E-mail de alerta com `?abrir=<id>` (PN-65):** corrigido em `app/services/deliveries.py` (PF) — `build_email(..., alert_id=)`.
- **"Final do log" (PN-54):** buffer em memória das últimas 500 linhas redigidas (spec linha 73), sem ler arquivo do servidor.
- **Limiares do monitor:** `MONITOR_*` acrescentados à spec 4.11 com valores iniciais, a calibrar no piloto.
- **Reações e limite / lembrete:** decisões provisórias "sim" (ver `docs/STATE.md`), pendentes do Caio.
- **Retiradas (decisão do Caio, 2026-10-27):** cada linha tem botão **Excluir** (abre a mesma confirmação LGPD, digitando o nome; admin continua bloqueado). Quem teve os dados excluídos (`anon:<id>`) **some de todas as abas e contagens**; o registro fica só no banco para o histórico de conversas, tickets e auditoria.
