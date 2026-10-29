# Engineering State — Jarvis

> **Last updated:** 2026-10-29
> **Current work:** cerebro.1 concluído (CB-T01..CB-T35; gate com IA real aprovado e teste no celular feito); porta-fechada.1 implementada (PF-01..PF-41); painel.1 implementado até a onda 14; go-live depende das decisões do dono (ver Current Work e Active Blockers)

Live engineering memory for this project. Where the PRD Decision Log records
**product** decisions (frozen, historical), this file tracks the **running
state** of implementation: technical decisions, active blockers, lessons, and
what the next session needs to know to resume without reconstructing context.

Read this first when resuming work. Update it at the end of every session and
whenever a decision, blocker, or lesson appears (see the rules in the
`sdd-workflow` skill).

Keep it lean — when it grows past ~10k tokens, archive entries older than
60 days to `STATE-ARCHIVE.md` (preserve active blockers and recent lessons).

---

## Current Work

- **Active task:** CEREBRO-1 concluída (T01..T35). Etapa 1, tarefa 1.1 (fechar decisões abertas do PRD §9 com o Felipe) segue em aberto.
- **Status:** cérebro **desligado por padrão** no código (`BRAIN_ENABLED=false`); ligado só no `.env` local do Caio para teste. Gate `palavras` e `ia` com 100% (preço 20/20, US$ 0,067). Teste no celular aprovado, com os botões com emoji renderizando: `docs/tasks/cerebro.1/gate-ia-relatorio.md`.
- **Achados da T35 a decidir:** sinônimos "leite / leite de vaca" → lactose/lácteo (ajuste em `matching.py`, aguarda o Caio); a FAQ da Jungle não tem item de lactose (conteúdo, pelo painel).
- **Pendências para ligar em produção (nenhuma é de código):**
  - **Token permanente** da Meta (o temporário de 24 h venceu durante o teste: erro 190) e número próprio.
  - ~~**D12:** Gemini API direta x Vertex AI~~ resolvida em 2026-10-29: Gemini aprovado pela API direta (ADR de 2026-10-29). ~~**D7:** licença Nielsen~~ resolvida em 2026-10-29: a Nielsen aprovou o repasse aos vendedores (informado pelo Caio).
  - **GRANTs** das tabelas novas (`chat.brain_runs`, `chat.answer_ratings`, colunas novas) para `jarvis_app` no droplet: `docs/tasks/cerebro.1/spec/verificacao-externa.md`, item 5.
  - **Preços por token** (`BRAIN_PRICE_IN/OUT_PER_MTOK_USD`, usar 1,50 / 7,50): sem eles `BRAIN_ENABLED=true` impede o app de subir.
  - **Textos fixos para o dono confirmar** (4 rascunhos em `app/config.py`): pedido de acesso, recusa, saudação e "qual marca?".
  - **Canal WhatsApp:** `whatsapp.py` trunca o corpo interativo em 4096 (o limite da Meta é 1024); o outbox já impõe 1024 antes de enviar, então não há falha hoje, mas o canal não protege sozinho.
  - **Testes do painel intermitentes** quando o PC entra em repouso durante a suíte (já vistos; passam ao repetir).
- **LGPD:** dados no droplet DigitalOcean (fora do Brasil) aprovados pelo Caio em 2026-10-29 (PRD §10).
- **Deploy adiado (2026-10-29):** não subir no droplet por enquanto; o número próprio do Jarvis ainda vai ser fornecido. Desenvolvimento e testes seguem no PC do Caio com o número de teste.
- **Next step:** Caio decide o ajuste de sinônimos de leite e cadastra as lacunas da FAQ; go-live só depois do número próprio com token permanente e dos GRANTs no droplet

## Recent Decisions (last 60 days)

Technical decisions made during implementation. Product-level decisions belong
in `prd.md` §10. New entries at the top. Reference the task that prompted them.

### AD-007 — Cérebro: fonte de conhecimento trocável  ·  2026-10-28  ·  CB-T09  ·  **decidido pelo Caio (D1)**
- **Chose:** o cérebro busca por uma interface `KnowledgeSource` (`brain/source.py`); hoje `FaqSource` lê a FAQ do painel, depois entra o banco de conhecimento. O pacote `app/services/brain/` não importa `app.panel` (teste de arquitetura).
- **Gave up:** reutilizar o `FaqReader` direto; custa uma consulta extra para a origem do item (spec, divergência 4).
- **Affects:** `app/services/brain/source.py`, `faq_source.py`, `tests/brain/security/test_architecture.py`.

### AD-008 — Cérebro: teto de US$ 100/mês só do cérebro, alerta aos 80%  ·  2026-10-28  ·  CB-T15  ·  **decidido pelo Caio (D11)**
- **Chose:** US$ 100 por mês só para o cérebro (o Assistente do painel tem contador próprio); aos 80% alerta `brain_budget` por e-mail e no painel (dedup mensal); aos 100% segue só com a busca por palavras até virar o mês (fuso America/Sao_Paulo). Custo = tokens reais × preço configurado, gravado em `chat.brain_runs` desde o dia 1; os tokens de pensamento do Gemini são cobrados e entram na conta.
- **Affects:** `app/services/brain/meter.py`, `chat.brain_runs`, `BRAIN_MONTHLY_BUDGET_USD`, `BRAIN_BUDGET_ALERT_PCT`.

### AD-009 — Cérebro: Gemini 3.8 Flash, pensamento baixo, sem temperature  ·  2026-10-28  ·  CB-T01, CB-T13
- **Chose:** `gemini-3.8-flash` pela REST, `thinkingLevel` baixo, **sem** `temperature`, saída JSON. Preços por token ficam em configuração (obrigatórios para ligar); valores de 2027 recomendados na verificação externa: **US$ 1,50 entrada / US$ 7,50 saída por milhão de tokens**. Só `brain/llm.py` fala com o Gemini (allow-list de host, sem redirect, chave só no cabeçalho, nunca loga prompt nem resposta).
- **Affects:** `app/services/brain/llm.py`, `BRAIN_PRICE_IN/OUT_PER_MTOK_USD`, `verificacao-externa.md`.

### AD-010 — Cérebro: avaliação em `chat.answer_ratings`; pedido de acesso como ticket "Sem classificação"  ·  2026-10-28  ·  CB-T10, CB-T21, CB-T22  ·  **decidido pelo Caio (D6, D14)**
- **Chose:** a avaliação 👍/👎 fica na tabela própria `chat.answer_ratings` (uma por ticket; `chat.tickets` não tem colunas para isso) e alimenta o KPI "Avaliados como úteis" (meta ≥ 85%); 👎 abre ticket escalado (`rating_down`). Pedido de acesso a marca é ticket `kind='question'` com `escalation_reason='brand_access_request'` e aparece em "Sem classificação" (aceito em 2026-10-28), sem alerta nem e-mail. Toques não contam nas 60 mensagens/hora (mesma razão de AD-003).
- **Affects:** migration 202, `app/services/ratings.py`, `question_tickets.py`, `pipeline._on_active`, `metrics_read.py`.

### AD-011 — Cérebro: pausa não gasta IA; resposta nunca cortada  ·  2026-10-28  ·  R01, CB-T18  ·  **decidido pelo Caio (D9 revisto)**
- **Chose:** (1) com o sistema pausado o provedor não busca nem chama o Gemini: a pergunta é escalada com motivo `paused`; (2) nenhuma resposta é cortada: `assembly.py` divide por frase em quantas partes de ≤ 700 caracteres forem precisas; só a última leva assinatura, fonte e (se for avaliável) os botões.
- **Affects:** `app/services/brain/provider.py`, `assembly.py`, `answering.py`, `outbox.py`.

### AD-001 — Versões de Python e PostgreSQL: dev ≠ produção  ·  2026-10-26  ·  PF-41
- **Chose:** desenvolver em Python 3.14 + PostgreSQL 18 (máquina do Caio) mantendo o código compatível com Python 3.12 e SQL compatível com PostgreSQL 16 (droplet); o CI roda a suíte em 3.12 + PG 16.
- **Why:** é o que está instalado localmente; produção é 3.12/PG 16.
- **Gave up:** a certeza de ter rodado a suíte no ambiente de produção (ver B-002).
- **Affects:** `ruff` `py312`, `mypy` 3.12, migrations 001–005, `.github/workflows/ci.yml`.

### AD-002 — Comandos canônicos de verificação  ·  2026-10-26  ·  PF-41
- **Chose:** a partir de `backend/`: `.venv\Scripts\python -m pytest`, `-m ruff check app tests`, `-m mypy app`, `-m app.db.migrate --check`; registrados em `.claude/CLAUDE.md`. Suíte final: 963 testes, 0 pulados.
- **Affects:** `.claude/CLAUDE.md`, spec porta-fechada.1 seções 4.6 e 4.7 (eventos de log e mapa AC → teste reconciliados com o código).

### AD-003 — Reação com emoji  ·  2026-10-26  ·  PF-32  ·  **decidido pelo Caio**
- **Chose:** (1) reação de quem ainda está em `pending_optin` **pode** gerar o lembrete interativo (1 por 24 h) — mantido; (2) reação e tipos ignorados de usuário ativo **não contam** nas 60 mensagens/hora (`counted_in_limit=false`, só rastro).
- **Affects:** `app/services/pipeline.py` (`_on_active`), `tests/integration/test_rate_limit.py`, `test_pipeline_active.py`.

### AD-004 — LGPD: admin não pode ter dados excluídos pelo painel  ·  2026-10-26  ·  PN-T40  ·  **decidido pelo Caio**
- **Chose:** quem tem perfil `administrador` não mostra o botão "Excluir dados" e o servidor recusa (403) antes de qualquer escrita. Exportar continua permitido.
- **Affects:** `app/panel/api/pessoas_lgpd.py`, `frontend/templates/_pessoa_lgpd.html`, `tests/panel/integration/test_pessoas_lgpd_routes.py`.

### AD-005 — Fonte do painel: Inter local, igual ao AUTO-1  ·  2026-10-26  ·  PN-T68  ·  **decidido pelo Caio**
- **Chose:** a Inter (mesma fonte do AUTO-1 e do protótipo), baixada **uma vez** do Google Fonts (subconjuntos latin e latin-ext, fonte variável 100-900) e servida de `frontend/static/vendor/` com sha384 em `VENDOR.md`. O painel continua sem carregar nada de fora (CSP `font-src 'self'`). Substitui a decisão anterior do mesmo dia de manter a fonte do sistema.

### AD-006 — Teste real no WhatsApp (número de teste)  ·  2026-10-27  ·  **concluído**
- **Resultado:** Jarvis novo (porta fechada + painel) testado no celular com 2 números: boas-vindas pelo modelo aprovado `jarvis_boas_vindas` (Utilidade, pt_BR), aceite, "em construção" para texto, "equipe avisada" + ticket para mídia, silêncio para desconhecidos (viram "Pedindo acesso"). Rodando local na porta 8010 com túnel do VS Code (`/webhook/whatsapp`), `PRODUCTION=false`.
- **Defeitos achados no teste real e corrigidos:** aviso (toast) em branco; modal de cadastro não fechava; número já cadastrado seguia em "Pedindo acesso"; ficha anonimizada oferecia Reativar; Retiradas sem Excluir.

## Active Blockers

Open issues stopping progress. Remove (or move to Lessons) once resolved.

### B-001 — Pendências do dono (porta-fechada.1)  ·  found 2026-10-26  ·  impede go-live, não impede o código
- **Problem:** itens que dependem do Caio/Positive e não podem ser resolvidos por código:
  - **Produção no WhatsApp:** o número de teste só manda para até 5 números da lista "Até". Para os 100 vendedores: número próprio do Jarvis (chip novo, não a Positive Co), forma de pagamento na conta da Meta, empresa verificada, e **token permanente de usuário do sistema** (o temporário caiu durante o teste, erro 190).
  - **Editar cadastro no painel (lacuna):** não há tela para alterar perfil, marcas, UFs, canais ou gestor de uma pessoa já cadastrada (só cadastrar, retirar, reativar, reenviar, trocar número via CLI e LGPD). Em 2026-10-27 foi preciso mudar um perfil de teste direto no banco (com auditoria). Candidato a tarefa do próximo ciclo do painel.
  - **Q8:** aprovação e pagamento do modelo de boas-vindas na Meta (sem ele não há envio real da boas-vindas).
  - **Q12:** para produção, o Jarvis precisa de uma conta WhatsApp própria (não usar a Positive Co). Acesso do app à Positive Co verificado em 2026-10-26: inexistente.
  - **Q17:** validação jurídica do texto da 1ª mensagem de desconhecido (`UNKNOWN_FIRST_TEXT_STORE` continua desligada).
  - **DNS:** subdomínio `*.positiveco.com.br` do Jarvis ainda não aponta para o droplet.
  - **Chaves do WhatsApp ausentes em `backend/.env`:** a CLI (`python -m app.cli`) e qualquer execução real carregam o `Settings` completo; preencher ou criar um `Settings` reduzido para a CLI.
  - **Remoto GitHub não criado:** o `ci.yml` nunca rodou.
  - **PRD §10 (mídia):** o texto do PRD sobre mídia diverge do AC-36; corrigir exige decisão do Caio e ADR (`docs/prd.md` não foi alterado). Apenas listado.
  - **Nomes comerciais dos produtos** (respostas da GMR) e **chave do Gemini** (cérebro real, fora desta entrega).
  - **Revisão jurídica** do aviso de opt-in e das mensagens.
- **Workaround:** o piloto e os testes automatizados cobrem tudo menos o envio real do modelo e do e-mail.
- **Path to resolve:** Caio, com Felipe/jurídico/marketing conforme o item.

### B-003 — Pendências do dono (painel.1)  ·  found 2026-10-26  ·  não impedem o código
- **Papel `jarvis_assistant`:** criado no banco local em 2026-10-26 (testes do assistente rodando, 0 pulados). Falta só a senha (`\password jarvis_assistant`) e o `ASSISTANT_DATABASE_URL` no `.env` — necessários apenas quando houver a chave do Gemini.
- **Resumo das 08:05 no grupo:** não implementado (grupo da equipe desligado); teste `xfail(strict)` marca a lacuna.
- **Métricas:** primeira carga sem cache levou 1,7 s; observar no piloto.
- **FAQ inicial:** a carga das 112 perguntas é uma base que a equipe edita (o item de creatina/Anvisa está sem resposta).
- **Playwright não instalado:** T65/T66 (E2E) não executadas.
- **`frontend/package-lock.json` não gerado:** precisa de rede (o CSS foi gerado com o binário do AUTO-1, sem `npm install`).
- **Revisão visual:** `docs/tasks/painel.1/visual-review.md` (PN-59) é conferida pelo Caio.

### B-002 — Python 3.12 nunca executado localmente  ·  found 2026-10-26  ·  baixo
- **Problem:** a suíte só rodou em Python 3.14 (+ PG 18); produção é 3.12 + PG 16.
- **Workaround:** ruff `py312` e mypy 3.12 estáticos.
- **Path to resolve:** criar o remoto GitHub para o CI rodar em 3.12 + PG 16 (ou instalar 3.12 localmente).

## Lessons Learned

Non-obvious things discovered the hard way. Keep these even after archiving.

### L-005 — Resposta em várias partes: o outbox descartava as partes sem botão  ·  2026-10-28  ·  CEREBRO R03
- **Situation:** `Answer.parts` com N partes e botões de avaliação.
- **Problem:** todas as partes saíam com o `kind` `brain_answer_rate`, mas só a última tinha botões; a validação do outbox falha alto e descartava as anteriores, e o vendedor recebia só o final da resposta. Os testes unitários de montagem (`assembly`) e de outbox passavam, cada um com o seu pedaço.
- **Fix:** partes que não são a última saem como `brain_answer`; só a última como `brain_answer_rate`.
- **Prevent next time:** para um contrato que atravessa camadas (montagem, fila, envio), manter teste de ponta a ponta com 2+ partes conferindo **todas** as saídas, não só a última.

### L-004 — Busca por palavras: substring de marca e stopword depois do plural  ·  2026-10-28  ·  CEREBRO R02
- **Situation:** o caso `ataque-04` do gate offline respondia em vez de recusar.
- **Problem:** "protein" casava dentro de outras palavras (precisa de palavra inteira, `\bprotein\b`); o plural era retirado antes de checar a lista de stopwords; faltavam sinônimos. Os testes unitários usavam frases escolhidas à mão, não o corpus real do Guia.
- **Fix:** palavra inteira, sinônimos e checagem de stopword antes de retirar o plural.
- **Prevent next time:** mudança em normalização/matching roda o gate (`eval --mode palavras`) além dos testes unitários; o gate é o detector de regressão da recuperação.

### L-003 — Pausa só no despacho não evita o custo de IA  ·  2026-10-28  ·  CEREBRO R01
- **Situation:** sistema pausado (pausa global do Jarvis).
- **Problem:** o outbox descartava a saída, mas o provedor já tinha buscado e chamado o Gemini: pagava a IA para nada. O teste de pausa só olhava o descarte, não o gasto.
- **Fix:** o provedor consulta a pausa primeiro; escala com motivo `paused`, sem busca nem Gemini.
- **Prevent next time:** todo teste de "não gera custo" afirma a **ausência de chamadas** (busca e HTTP do Gemini via espião), nunca só o resultado visível.

### L-001 — Testes de banco fora do loop do TestClient  ·  2026-10-26
- **Situation:** testes que usam `TestClient` e também o banco.
- **Problem:** o pool do app pertence ao loop do `TestClient`; usá-lo de fora falha.
- **Fix:** abrir conexão própria no teste.
- **Prevent next time:** nunca reutilizar `app.state.pool` fora do `TestClient`.

### L-002 — Nomes de fonte únicos por arquivo de teste  ·  2026-10-26
- **Situation:** onda paralela de tarefas.
- **Problem:** arquivos de teste previstos na spec (`test_alerts_deliveries`, `test_reply_policy_flags`) teriam dois donos.
- **Fix:** dividir em `test_alerts` + `test_deliveries` e `test_flags` + `test_reply_policy`.
- **Prevent next time:** na Fase 3, dono único por arquivo; a spec 4.7 foi reconciliada.

## Deferred Ideas

Good ideas that are out of current scope. Capture so they aren't lost, defer so
they don't cause scope creep.

- {idea} — surfaced during {task ID}, deferred because {reason}.

## Quick Tasks Completed

Trivial work that skipped the full SDD pipeline (bug fixes, config, copy edits).
Logged here for traceability instead of vanishing into git history alone.

| Date | Description | Commit | Files |
|---|---|---|---|
| 2026-10-28 | Caso `piloto-3` do gate: "oi" espera `saudacao` (novo valor esperado, desfecho `greeting`) em vez de `responde` | CB-T34 (a commitar) | 4 |
| 2026-10-28 | `tests/brain/factories.py`: `create_active_user` normaliza o telefone (`phone_key` pela regra do app, sem o 9º dígito) | CB-T34 (a commitar) | 1 |

## Todos

Loose action items not yet formalized into tasks.

- [ ] Desenho da lista positiva (incremento 1): WhatsApp está lançando nomes de usuário e o BSUID (ID por empresa). Mensagens de quem usa nome de usuário podem chegar sem telefone — guardar telefone **e** BSUID no aceite; não depender só do número. Visto em 2026-10-20 no painel da Meta.
- [ ] Forma de pagamento no WhatsApp Manager — necessária antes de enviar modelos (boas-vindas); não precisa para testes iniciados pelo usuário.
- [ ] **Conta WhatsApp "Positive Co" (ID 1903250273702051, número +55 11 5196-0583) NÃO é conta de teste**: já está em uso pela Positive (~37,7 mil entregas e ~US$ 945 de cobrança no mês, visto em 2026-10-20). O app Jarvis foi autorizado nela por engano. Descobrir com o marketing quem usa; criar uma conta WhatsApp separada para o Jarvis e depois remover o acesso do app à Positive Co. Webhooks de uma conta vão para todos os apps inscritos — compartilhar conta exporia as conversas do Jarvis ao outro sistema.
- [x] Conta de teste da Meta criada com número +1 555 670 8419 (objeto 1361767417026383 citado no erro de registro — confirmar se é a conta ou o número). Primeira mensagem (hello_world) recebida no celular do Caio em 2026-10-20. Desenvolvimento segue nessa conta.
- [x] Confirmar que o token do app Jarvis não inclui mais a conta Positive Co. **Verificado em 2026-10-26:** a conta não lista o Jarvis em apps conectados e o token do Jarvis recebe erro de permissão ao ler a conta 1903250273702051. O número só aparece no campo "De" da Configuração da API porque a lista mostra o que a conta pessoal enxerga no portfólio.
- [x] Stack decidida em 2026-10-20: Opção A, padrão AUTO-1 (ver ADR em `docs/architecture/overview.md`).
- [ ] Subdomínio `jarvis.positiveco.com.br` apontando para o droplet (159.89.55.129) — descobrir quem gerencia o DNS.
- [ ] No droplet: criar banco `jarvis` no Postgres, checar pgvector, pasta `/opt/jarvis`, processo PM2, site nginx + certificado. Pedir confirmação antes de cada mudança (servidor compartilhado em produção).
- [ ] Spec do incremento 1 (webhook WhatsApp + lista positiva + aceite) via `/spec-new`.
- [x] **Piloto técnico (PILOTO-TECNICO-1) validado em 2026-10-23**: celular do Caio → Meta (número de teste) → `piloto/server.js` local (túnel do VS Code) → resposta pela FAQ sem IA (Guia + Nielsen + GMR, 223 itens), assinada pelo agente da marca. 5/5 perguntas de teste corretas, incluindo o "não sei" para preço. Lições: (1) a Meta entrega o remetente BR sem o 9º dígito, mas o número de teste só envia para o número cadastrado com o 9 → responder sempre para o número da lista positiva; (2) cumprimentos variam ("oiii", "olá, tudo bem?") → normalizar antes de detectar.
- [ ] Nomes comerciais dos produtos para as respostas da GMR (hoje saem como descrição SAP, ex.: "Suplemento Alim ATDC Ucof Capp" = Ultracoffee Cappuccino).
- [x] App "Jarvis" criado no Meta for Developers com caso de uso "Conectar-se com os clientes pelo WhatsApp" (2026-10-20).
