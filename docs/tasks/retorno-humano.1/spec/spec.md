---
task_id: RETORNO-HUMANO-1
status: implemented
phase: 4-implementation
requirements_approved_by: Caio
requirements_approved_at: 2026-11-17
design_approved_by: Caio
design_approved_at: 2026-11-17
tasks_approved_by: Caio
tasks_approved_at: 2026-11-17
---

# Spec — A equipe responde os escalados pelo Inbox e alimenta a FAQ (RETORNO-HUMANO-1)

> **Story:** [retorno-humano.1](../../../stories/retorno-humano.1.md) · **Epic:** [retorno-humano](../../../epics/retorno-humano.md)
> **Status:** Fase 1 (Requisitos, RH-1 a RH-21; decisões D1–D8 tomadas pelo Caio em 2026-11-17) **aprovada por Caio em 2026-11-17**. Fase 2 (design: seções 9 a 11, `research.json`, `complexity.json`, ADR em `overview.md`) **aprovada por Caio em 2026-11-17**. Fase 3 (tarefas: [`../task.md`](../task.md), 14 tarefas em 6 ondas) **aprovada por Caio em 2026-11-17**. Fase 4 (implementação) em andamento.
> Critérios completos e testáveis: `requirements.json`.

## 1. Objetivo

Hoje, quando o Jarvis não responde, o vendedor ouve "Anotei a pergunta para a equipe"
(ou "avisei a equipe", para mídia, pedido de acesso e 👎), um ticket fica em Escalados
e **nada mais acontece com o vendedor**:

- concluir um ticket só grava `answered_human` (`tickets_admin.conclude`, "nunca envia nada ao vendedor");
- o Inbox é só leitura, sem caixa de resposta (`inbox_chat.py`);
- o aviso ao grupo da equipe existe (`ops.deliveries`, canal `group`), mas está desligado (`team_group_enabled=false`) e não leva o texto da pergunta.

Esta história fecha o ciclo com a escolha **A + C + D** do Caio (2026-11-17), revista
no mesmo dia para que o grupo seja **só aviso**:

- **C:** cada escalado vira um aviso no grupo de WhatsApp da equipe, no estilo do AUTO-1, com a pergunta e um **link que abre o Inbox direto nela**;
- **A:** a equipe responde **pelo Inbox** (caixa de resposta), e o texto sai **pelo número do Jarvis, na conversa do vendedor**. O grupo não aceita respostas: nada escrito nele chega ao vendedor;
- **D:** cada resposta humana a uma pergunta vira uma **sugestão de item da FAQ** em Relatórios, aprovada ou descartada por um admin.

## 2. Escopo

**Dentro:**
- Aviso de escalado no grupo da equipe com o texto da pergunta, motivo, marca e o link para o Inbox.
- Link `/dashboard/inbox/ticket/J-nnnn` que abre a conversa na pergunta, com a caixa de resposta pronta.
- Caixa de resposta no Inbox e no drawer do ticket; envio pela fila de saída existente (`chat.outbound_messages`, outbox transacional).
- Situação do envio (enviada, entregue, falhou, bloqueada) visível no painel.
- Fila "Sugestões para a FAQ" em Relatórios, com aprovar (vira item da FAQ) e descartar.
- Auditoria, logs sem PII, retenção e LGPD para as novas linhas.

**Fora:** ver seção 5.

## 3. Critérios de aceite (EARS)

Convenções: "escalado" é todo ticket criado com status `awaiting` (pergunta sem
resposta, pausa, 👎, pedido de acesso a marca e mídia). "Grupo da equipe" é o chat
configurado em `TEAM_GROUP_CHAT_ID`. "Resposta humana" é um texto escrito por um admin
no painel para o vendedor de um ticket. Critérios que dependem de decisão citam a
decisão (seção 6).

### Aviso no grupo (C)

- **RH-1 Aviso na hora** *(D8)*. GIVEN o grupo da equipe ligado, WHEN um escalado é criado, THEN uma entrega `group` é gravada na mesma transação do ticket e enviada pelo laço de fundo, como hoje; com o grupo desligado continua `panel_only`.
- **RH-2 Conteúdo do aviso** *(D4)*. GIVEN um aviso de escalado, WHEN é montado, THEN segue o modelo:
  ```
  🔔 Vendedor enviou nova pergunta (ticket J-0123)
  Marca: Jungle · Motivo: sem resposta na FAQ
  Vendedor: Ana (CE)

  Mensagem:
  "Tem versão sem lactose?"

  Responda pelo Inbox: <link do ticket>
  (Mensagens escritas aqui no grupo não chegam ao vendedor.)
  ```
  O vendedor aparece pelo primeiro nome e UF, **nunca pelo telefone**. A pergunta vai entre aspas, cortada em 1.000 caracteres. Mídia aparece como `[áudio]`, `[foto]`, `[vídeo]` ou `[arquivo]`, sem o arquivo. O link leva só o número do ticket. O texto fixo fica em configuração.
- **RH-3 Uma mensagem por aviso, sem rajada.** GIVEN vários escalados no mesmo minuto, WHEN são enviados ao grupo, THEN saem em sequência, um por vez, com intervalo mínimo configurável (padrão 3 s); falha de envio segue o backoff das entregas de hoje e nunca perde o ticket.
- **RH-4 O grupo não é lido.** GIVEN qualquer mensagem escrita no grupo da equipe, WHEN chega ao Jarvis, THEN é descartada sem efeito, como toda mensagem de grupo hoje (WA-6): nada é enviado ao vendedor e o ticket não muda.

### Responder pelo Inbox (A)

- **RH-5 Link abre o Inbox na pergunta.** GIVEN o link de um aviso (`/dashboard/inbox/ticket/J-0123`), WHEN alguém o abre, THEN o painel exige login de admin (voltando ao mesmo endereço depois do login) e abre o **Inbox na conversa do vendedor**, rolada até a pergunta do ticket, com ela destacada e a caixa "Responder ao vendedor" já ligada a esse ticket e com o cursor nela. Ticket inexistente devolve 404 do painel; ticket já respondido abre igual, mostrando a resposta anterior (RH-11). Funciona no navegador do celular (o link é aberto a partir do WhatsApp).
- **RH-6 Caixa de resposta** *(D1)*. GIVEN um admin do painel na conversa do Inbox ou no drawer de um ticket escalado, WHEN abre a tela, THEN vê a caixa "Responder ao vendedor" (até 4.000 caracteres) e o botão "Enviar e concluir"; toda requisição exige sessão de admin e CSRF.
- **RH-7 Enviar e concluir.** GIVEN um texto válido, WHEN o admin envia, THEN numa única transação: a resposta entra na fila de saída do vendedor, o ticket vai para `answered_human` com autor (o admin) e data, e a auditoria é gravada; qualquer falha desfaz tudo.
- **RH-8 Concluir sem responder.** GIVEN um ticket escalado, WHEN o admin usa "Concluir" (sem texto), por exemplo porque já respondeu por fora, THEN o comportamento atual se mantém: só muda o status, sem envio e sem sugestão.

### Entrega ao vendedor

- **RH-9 Formato** *(D7)*. GIVEN uma resposta humana, WHEN é enviada ao vendedor, THEN sai pelo número do Jarvis, na conversa dele, como `"Retorno da equipe (<primeiro nome do admin>) sobre sua pergunta:\n\n<texto>"` (cabeçalho em configuração; sem nome na sessão, o cabeçalho sai sem os parênteses), dividida por frase em partes de até 700 caracteres como as respostas do cérebro, sem botões de avaliação.
- **RH-10 Sem prazo** *(D2)*. GIVEN um ticket escalado há qualquer tempo, WHEN o admin envia uma resposta, THEN ela é enviada; a caixa de resposta mostra há quanto tempo o vendedor escreveu.
- **RH-11 Mais de uma resposta** *(D6)*. GIVEN um ticket já `answered_human`, WHEN um admin envia outra resposta, THEN ela também é enviada como complemento, sem mudar o autor e a data da primeira. Clique duplo ou reenvio do mesmo formulário nunca envia a mesma mensagem duas vezes.
- **RH-12 Bloqueios.** GIVEN o vendedor do ticket, WHEN não está `active` (revogado, recusou o aviso, conflito de identidade, ou excluído pela LGPD), THEN a caixa de resposta aparece desabilitada com o motivo, e um envio forçado é recusado sem efeito. O opt-out de avisos ("sair") **não** bloqueia: a resposta é ao que ele perguntou.
- **RH-13 Pausa não bloqueia.** GIVEN o Jarvis pausado ou a marca do ticket desligada, WHEN o admin envia uma resposta, THEN ela é enviada (a pausa cala só as respostas automáticas).
- **RH-14 Histórico e limites.** GIVEN uma resposta humana enviada, WHEN alguém abre a conversa no Inbox, THEN ela aparece na linha do tempo marcada como "Equipe · <autor>"; ela não conta nas 60 mensagens/hora do vendedor e não abre nova sessão de marca.
- **RH-15 Situação do envio.** GIVEN uma resposta humana, WHEN o canal informa o andamento, THEN o painel mostra na própria mensagem: "na fila", "enviada", "entregue", "lida" ou "falhou" (com motivo curto), a partir do status de saída que já existe.

### Sugestões para a FAQ (D)

- **RH-16 Gerar sugestão** *(D3)*. GIVEN uma resposta humana enviada a um ticket de pergunta com texto, WHEN o envio é enfileirado, THEN uma sugestão `pendente` é gravada com: pergunta do vendedor, resposta da equipe, marca, ticket, autor e data. Tickets de mídia e "Concluir sem responder" **não** geram sugestão. Complementos (RH-11) se juntam à sugestão já existente do ticket.
- **RH-17 Tela em Relatórios.** GIVEN um admin em Relatórios, WHEN abre "Sugestões para a FAQ", THEN vê as pendentes (mais recentes primeiro), com filtro por marca e o total de pendentes; cada linha mostra pergunta, resposta, marca, ticket (link) e autor.
- **RH-18 Aprovar.** GIVEN uma sugestão pendente, WHEN o admin clica "Criar item", THEN abre o formulário da FAQ já preenchido (pergunta, resposta, marca), com a validação de sempre; ao salvar, o item nasce ativo com origem `sugestao` e referência ao ticket, e a sugestão fica `aprovada` com autor e data, na mesma transação, com auditoria. Cancelar o formulário não muda a sugestão.
- **RH-19 Descartar.** GIVEN uma sugestão pendente, WHEN o admin descarta, THEN ela fica `descartada` com autor e data, com auditoria, e sai da lista de pendentes (consultável no filtro "Todas").

### Separação do AUTO-1

- **RH-21 Avisos pelo WAHA do Jarvis, sem tocar no AUTO-1** *(D5)*. GIVEN o grupo da equipe ligado, WHEN o Jarvis envia um aviso, THEN usa a instância e a sessão do **WAHA do Jarvis** (as mesmas do canal dos vendedores) e envia **só** ao `TEAM_GROUP_CHAT_ID`; com o grupo ligado, a configuração recusa subir se o envio ao grupo apontar para outra instância ou sessão que não a do Jarvis. Nenhum arquivo, configuração, sessão ou tabela do AUTO-1 é usado ou alterado.

### Privacidade e operação

- **RH-20 Logs, retenção e LGPD.** GIVEN qualquer passo desta história, WHEN gera log, THEN o log traz só ids, número do ticket e códigos curtos (sem texto, telefone ou nome); respostas humanas e sugestões seguem a retenção de 12 meses das conversas; a exclusão LGPD de um vendedor apaga as respostas e as sugestões **pendentes ou descartadas** ligadas a ele (itens já aprovados na FAQ ficam, porque já foram reescritos e revisados por um admin).

## 4. Requisitos não funcionais

| Tema | Requisito |
|---|---|
| Latência | Aviso de escalado no grupo em p95 ≤ 30 s; resposta do Inbox sai ao vendedor em p95 ≤ 10 s |
| Confiabilidade | No máximo um envio por resposta humana; nada externo antes do COMMIT (outbox, ADR 2026-10-23) |
| Segurança | Link do aviso só abre com sessão de admin; nenhum dado pessoal na URL |
| Privacidade | Primeira exceção à regra "grupo nunca carrega texto de mensagem": o aviso leva a pergunta (D4), nunca o telefone |
| Anti-banimento | Avisos em sequência com intervalo mínimo (RH-3); risco de enviar resposta tardia ao vendedor aceito pelo Caio (D2) |
| Convivência | Jarvis e AUTO-1 totalmente separados (RH-21); se a sessão do Jarvis cair, os avisos ficam `pending` e saem quando ela voltar, e o painel continua mostrando os escalados |
| Uso no celular | Inbox aberto pelo link utilizável na largura de celular (caixa de resposta visível sem rolar para o lado) |
| Compatibilidade | Python 3.12 e PostgreSQL 16; nenhuma dependência nova |

## 5. Fora de escopo

- **Responder pelo grupo** (reply na mensagem do aviso ou prefixo `J-nnnn:`): retirado pelo Caio em 2026-11-17, para o grupo não ficar confuso com várias mensagens.
- Resposta com mídia (foto, áudio, arquivo) ao vendedor.
- Rascunho de resposta gerado por IA para a equipe.
- Papel novo de "respondente" no painel ou no IAM (só admins respondem).
- Mensagem ativa a vendedor que não escreveu; avisos no grupo que não sejam de escalados (os alertas do sistema seguem como estão).
- Liberar acesso a marca pela resposta (o pedido de acesso é respondido em texto; a liberação continua em Pessoas).

## 6. Decisões

| # | Decisão | Recomendação |
|---|---|---|
| **D1** | **Decidido pelo Caio em 2026-11-17:** qualquer admin do painel (`PANEL_ADMIN_EMAILS`) pode responder; o autor fica na auditoria. | — |
| **D2** | **Decidido pelo Caio em 2026-11-17:** sem prazo para a resposta humana. Risco aceito: no WAHA, uma resposta horas depois pode parecer mensagem ativa e aumentar o risco de restrição do número. | — |
| **D3** | **Decidido pelo Caio em 2026-11-17:** nada entra direto na FAQ; as respostas viram **sugestões em Relatórios**, que um admin aprova (vira item) ou descarta. | — |
| **D4** | **Decidido pelo Caio em 2026-11-17 (revisto no mesmo dia):** aviso no grupo no estilo do AUTO-1, **com o texto da pergunta** e o link que abre o Inbox na pergunta; o grupo é **só aviso** (sem reply nem prefixo). Muda a regra atual de que o grupo nunca carrega texto de mensagem (`deliveries.py`). | — |
| **D5** | **Decidido pelo Caio em 2026-11-17 (revisto no mesmo dia), com a condição de não atrapalhar os avisos do Instagram (AUTO-1):** os avisos saem pelo **WAHA do próprio Jarvis** (sessão `jarvis`, número +55 85 98208-0355), num grupo da equipe do Jarvis em que esse número é membro. Nada do AUTO-1 é usado nem alterado (RH-21). O envio ao grupo já tem configuração própria (`send_group_text`, `WAHA_*`), separada da fila dos vendedores, e a trava "só responde" (`outbox.py`) vale só para vendedores; as mensagens escritas no grupo já são descartadas pelo Jarvis (WA-6). Risco aceito: o número do Jarvis, um chip novo, passa a iniciar mensagens (só para o grupo de colegas, com intervalo, RH-3); se ele for restrito, os avisos param junto com o atendimento aos vendedores, e os escalados seguem no painel. Alternativa descartada: o WAHA do AUTO-1, que dividiria a sessão e o risco com os avisos do Instagram. | — |
| **D6** | **Decidido pelo Caio em 2026-11-17:** sim, segunda resposta ao mesmo ticket é enviada como complemento (RH-11). | — |
| **D7** | **Decidido pelo Caio em 2026-11-17 ("se possível, sim"):** a mensagem leva o **primeiro nome** de quem respondeu, tirado do nome da sessão do painel (vem do login corporativo): "Retorno da equipe (Caio) sobre sua pergunta:". Sem nome disponível, sai sem o nome (RH-9). | — |
| **D8** | **Decidido pelo Caio em 2026-11-17:** todos os escalados vão ao grupo (sem resposta, pausa, 👎, pedido de acesso, mídia), com o motivo no aviso (RH-1). | — |

## 7. Premissas a verificar na Fase 2

- **O painel precisa estar acessível pela internet** (`jarvis.positiveco.com.br` + login corporativo/IAM, hoje adiados) para o link abrir no celular. Até lá, o link só funciona no PC do Caio; entra no checklist de go-live.
- Só quem é admin consegue usar o link; membros do grupo que não são admins só leem o aviso.
- Como `chat.tickets` e `chat.outbound_messages` acomodam a resposta humana (colunas novas ou tabela própria), o tipo de saída novo na outbox e a migration das sugestões.
- O fluxo de "voltar ao endereço depois do login" no IAM corporativo.

## 8. Referências

- AUTO-1: `agente-instagram-direct-n8n/backend/app/services/human_handoff.py` (formato do aviso "Como responder este ticket").
- `docs/tasks/canal-waha.1/spec/spec.md` (WA-6: mensagens de grupo descartadas).
- `docs/tasks/painel.1/spec/spec.md` (Inbox, Escalados, Relatórios, FAQ, auditoria).
- `docs/prd.md` §10, decisões de 2026-10-21 e 2026-10-23 (grupo da equipe).
- `docs/mapeo-general-2026-11-06.md` (achado: concluir não responde ao vendedor).

## 9. Design (Fase 2)

### 9.1 Componentes

| Onde | O quê |
|---|---|
| `migrations/401_retorno_humano.sql` (novo) | Kind `human_reply`, colunas novas em `chat.outbound_messages`, tabela `content.faq_suggestions`, origem `sugestao` na FAQ (9.2) |
| `app/core/values.py` | `OutboundKind.HUMAN_REPLY` / `ReplyKind.HUMAN_REPLY` |
| `app/services/human_reply.py` (novo) | `send_reply(...)`: regra de negócio da resposta humana numa transação (9.4.2). Não importa `app.panel` |
| `app/services/outbox.py` | `REQUIRED_STATUS[HUMAN_REPLY]=ACTIVE`; `human_reply` **não** passa pela janela de 24 h (exceção à WA-16, 9.8) |
| `app/services/reply_policy.py` | `human_reply` em `PAUSE_EXEMPT_KINDS` (RH-13) |
| `app/services/workers.py` | Ordem das partes: além de `inbound_trace_id`, as partes `human_reply` do mesmo `ticket_id` saem em ordem (`_DUE_OUTBOUND`) |
| `app/services/pipeline.py` | `handle_status`: `delivered`/`read` passam a gravar `delivered_at`/`read_at` em vez de serem ignorados (RH-15) |
| `app/services/deliveries.py` | `build_ticket_group_text` reescrito no formato RH-2 para todo escalado (pergunta e mídia), com o link; envio ao grupo com intervalo mínimo (RH-3) |
| `app/services/waha.py` + `app/config.py` | Envio ao grupo pelo WAHA do Jarvis (`JARVIS_WAHA_*`); `WAHA_BASE_URL/WAHA_API_KEY/WAHA_SESSION` deixam de ser lidos (RH-21) |
| `app/panel/api/inbox.py` | `GET /dashboard/inbox/ticket/{J-n}` → 302 para `/dashboard/inbox?u=<id>&t=<J-n>` (RH-5) |
| `app/panel/api/inbox_chat.py` + `_inbox_chat.html` + `js/inbox_chat.js` | Caixa de resposta, destaque e rolagem até a pergunta `t`, foco; bolhas "Equipe · autor" com situação |
| `app/panel/api/ticket_drawer.py` + `_ticket_drawer.html` | Mesma caixa de resposta no drawer |
| `app/panel/api/tickets_reply.py` (novo) | `POST /dashboard/tickets/{J-n}/responder` (único ponto de envio, usado pelo Inbox e pelo drawer) |
| `app/panel/services/inbox_read.py` | Linha do tempo: `human_reply` sai da parte "bot" e vira "Equipe · autor" com situação |
| `app/panel/api/relatorios.py` + `relatorios.html` + `app/panel/services/faq_suggestions.py` (novo) | Card "Sugestões para a FAQ", filtro Pendentes/Todas, descartar (RH-17, RH-19) |
| `app/panel/api/faq_form.py` + `faq_admin.py` | `?sugestao=<id>` pré-preenche pergunta **e resposta**; ao salvar, cria com `origin='sugestao'` e aprova a sugestão na mesma transação (RH-18) |
| `app/panel/services/lgpd.py`, `retention.py` | Sugestões entram na exclusão e na retenção (RH-20) |

### 9.2 Dados (migration 401, aditiva, idempotente, PG 16)

```sql
-- chat.outbound_messages: kind human_reply (mesmo padrão da 201/301: recria o CHECK pelo catálogo)
ALTER TABLE chat.outbound_messages
  ADD COLUMN IF NOT EXISTS ticket_id    bigint REFERENCES chat.tickets(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS author       text,          -- nome do admin (D7); NULL nas demais saídas
  ADD COLUMN IF NOT EXISTS author_email text,          -- auditoria interna; nunca vai ao vendedor
  ADD COLUMN IF NOT EXISTS client_token uuid UNIQUE,   -- clique duplo / reenvio (RH-11)
  ADD COLUMN IF NOT EXISTS delivered_at timestamptz,   -- ack 2 (RH-15)
  ADD COLUMN IF NOT EXISTS read_at      timestamptz;   -- ack 3/4
CREATE INDEX IF NOT EXISTS outbound_ticket_idx ON chat.outbound_messages (ticket_id, id) WHERE ticket_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS content.faq_suggestions (
  id            bigserial PRIMARY KEY,
  ticket_id     bigint REFERENCES chat.tickets(id) ON DELETE SET NULL,
  ticket_number text NOT NULL UNIQUE,          -- uma sugestão por ticket; complementos se juntam (RH-16)
  user_id       bigint REFERENCES access.users(id),
  brand         text,
  category      text,                          -- classificação do ticket, se for categoria da FAQ
  question      text,                          -- NULL após LGPD
  answer        text,                          -- respostas concatenadas, separadas por linha em branco
  status        text NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente','aprovada','descartada')),
  author        text NOT NULL,                 -- quem respondeu primeiro
  decided_by    text, decided_at timestamptz,
  faq_item_id   bigint REFERENCES content.faq_items(id),
  created_at    timestamptz NOT NULL, updated_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS faq_suggestions_pending_idx ON content.faq_suggestions (created_at DESC) WHERE status = 'pendente';

-- content.faq_items.origin ganha 'sugestao' (recria o CHECK pelo catálogo)
```

`chat.tickets` não muda: `answered_by`/`answered_at` já servem (RH-7). `ops.deliveries` não muda (o grupo não é lido, então o id do aviso não precisa ser guardado). GRANTs para `jarvis_app` na tabela nova entram no checklist (9.10).

### 9.3 Contratos

```python
# app/services/human_reply.py
@dataclass(frozen=True)
class ReplyResult:
    status: Literal["sent", "duplicate", "blocked", "not_found", "invalid"]
    ticket_number: str
    reason: str | None = None          # "revoked", "declined", "conflict", "deleted", "empty", "too_long"
    outbound_ids: tuple[int, ...] = ()

async def send_reply(conn, ticket_number: str, text: str, *, author_name: str | None,
                     author_email: str, client_token: UUID, settings: Settings,
                     clock: Clock | None = None) -> ReplyResult: ...

def header_for(author_name: str | None, settings: Settings) -> str:
    # "Retorno da equipe (Caio) sobre sua pergunta:" | "Retorno da equipe sobre sua pergunta:"
```

Rotas novas (todas com `current_admin`; POST com `require_csrf`):

| Rota | Faz |
|---|---|
| `GET /dashboard/inbox/ticket/{J-n}` | 302 → `/dashboard/inbox?u=<user_id>&t=<J-n>`; 404 se não existe |
| `POST /dashboard/tickets/{J-n}/responder` | campos `texto`, `token` (uuid gerado ao renderizar a caixa), `origem` (`inbox`/`drawer`); devolve o fragmento de onde veio + `HX-Trigger` toast/`ticketChanged` |
| `GET /dashboard/relatorios/sugestoes?status=pendentes\|todas` | fragmento do card (HTMX), respeita a marca em foco |
| `POST /dashboard/relatorios/sugestoes/{id}/descartar` | descarta (RH-19) |
| `GET /dashboard/faq/nova?sugestao=<id>` | formulário pré-preenchido (RH-18); `POST /dashboard/faq/nova` aceita o campo oculto `sugestao` |

Configuração nova: `TEXT_HUMAN_REPLY_HEADER` / `TEXT_HUMAN_REPLY_HEADER_NO_NAME`, `TEXT_TEAM_GROUP_TICKET` (modelo do aviso), `TEAM_GROUP_MIN_INTERVAL_SECONDS=3`, `HUMAN_REPLY_MAX_CHARS=4000`, `HUMAN_REPLY_IGNORE_WINDOW=true`. O link do aviso usa `PANEL_BASE_URL` ou, se vazio, `https://PANEL_PUBLIC_HOST` (`Settings.panel_link_base`; ver seção 12).

### 9.4 Fluxos

**9.4.1 Aviso no grupo.** Ticket criado (transação 1, como hoje) → entrega `group` `pending` → laço de fundo → `build_ticket_group_text` (lê ticket + primeiro nome e UF do vendedor + motivo traduzido) → `send_group_text` pelo WAHA do Jarvis. O laço não envia duas entregas `group` com menos de `TEAM_GROUP_MIN_INTERVAL_SECONDS` entre si (guarda o horário do último envio em memória; reinício só atrasa, nunca adianta).

**9.4.2 Resposta.** `POST …/responder` → `send_reply` numa transação:
1. Valida texto (sem só espaços; ≤ 4.000) → senão `invalid`.
2. `SELECT … FROM chat.tickets WHERE ticket_number=$1 FOR UPDATE` → senão `not_found`.
3. `client_token` já usado → `duplicate` (nada novo; RH-11).
4. Relê o status do vendedor; não `active` → `blocked` com motivo (RH-12); nada gravado.
5. Monta `header + "\n\n" + texto`, divide com o mesmo empacotador de `brain/assembly.py` (≤ 700, por frase; o empacotador é extraído para uma função pública sem mudar o comportamento) e grava cada parte com `outbox.enqueue(kind=HUMAN_REPLY, ticket_id, author, author_email, client_token só na 1ª parte)`.
6. Ticket `awaiting` → `answered_human` com `answered_by=author`, `answered_at=now` (já respondido: não muda, RH-11).
7. Sugestão: se `kind='question'` e há `message`, faz upsert por `ticket_number` (nova `pendente`, ou concatena a resposta na existente se ainda `pendente`).
8. `audit.write("panel", email, "ticket.reply", "ticket", J-n, None, {"parts": n, "chars": len})` (sem o texto).
COMMIT → o laço de fundo envia (sem janela de 24 h; pausa não bloqueia; status do vendedor relido no despacho, como hoje).

**9.4.3 Situação.** `message.ack` 2 → `delivered_at`, 3/4 → `read_at` (só se `NULL`), -1 → `failed` como hoje. A bolha mostra: `pending/sending`→"na fila", `sent`→"enviada", `delivered_at`→"entregue", `read_at`→"lida", `failed/unknown/discarded`→"falhou: <motivo curto>".

**9.4.4 Link do aviso.** Sem sessão, o `_login_abort` atual já guarda caminho + query como `next`; a URL do aviso é de **página** (não fragmento HTMX), então o retorno depois do login funciona. A página do Inbox repassa `t` ao fragmento do chat; o fragmento marca a bolha da pergunta (`data-ticket="J-n"`), o JS rola até ela e põe foco na caixa.

**9.4.5 Aprovar sugestão.** `GET /faq/nova?sugestao=id` (só `pendente`; senão aviso "já decidida") → formulário com pergunta, resposta, marca e categoria → `POST` → `create_item(origin='sugestao', origin_ref=J-n)` e `UPDATE faq_suggestions SET status='aprovada', decided_by, decided_at, faq_item_id` **na mesma transação**; se a sugestão mudou de status no meio, desfaz e avisa.

### 9.5 Logs e privacidade

Eventos novos: `human_reply_queued` (ticket, partes), `human_reply_blocked` (ticket, motivo), `faq_suggestion_created|approved|discarded` (id). Nenhum traz texto, nome ou telefone. O aviso no grupo leva primeiro nome, UF e a pergunta (D4); `test_pii_in_logs` ganha casos para os eventos novos.

### 9.6 Interface (painel)

- Caixa de resposta: `<textarea>` com contador de caracteres, "Vendedor escreveu há X" (RH-10), botão "Enviar e concluir"; desabilitada com o motivo quando bloqueada; em celular ocupa a largura toda, fixa no rodapé do painel de chat.
- Bolha "Equipe · Caio" à direita, com a situação em texto pequeno; a nota antiga "Ticket concluído por X. Nenhuma mensagem foi enviada." continua só para o Concluir sem texto.
- Relatórios: card "Sugestões para a FAQ (N pendentes)" no topo, abas Pendentes/Todas; cada linha com pergunta, resposta (recortada, expande), marca, link do ticket, autor, botões "Criar item" e "Descartar".

### 9.7 Testes

| Camada | Arquivo | Cobre |
|---|---|---|
| unit | `tests/unit/test_human_reply_header.py`, `test_group_ticket_text.py` | RH-2, RH-9 (cabeçalho com/sem nome, recorte 1.000, mídia, sem telefone) |
| integration | `tests/integration/test_human_reply.py` | RH-7, RH-11 (token, complemento), RH-12, RH-13, RH-16, exceção da janela de 24 h, ordem das partes |
| integration | `tests/integration/test_outbound_acks.py` | RH-15 |
| integration | `tests/integration/test_group_notice.py` | RH-1, RH-3, RH-4, RH-21 |
| panel/integration | `test_inbox_reply.py`, `test_inbox_deeplink.py`, `test_ticket_drawer_reply.py` | RH-5, RH-6, RH-8, RH-14, CSRF, login com `next` |
| panel/integration | `test_faq_suggestions.py` | RH-17, RH-18, RH-19 |
| panel/integration | `test_lgpd.py`, `test_retention.py` (ampliados) | RH-20 |
| panel/security | `test_route_sweep.py`, `test_pii_in_logs.py` (ampliados) | rotas novas exigem admin/CSRF; logs |

### 9.8 Trade-offs

- **Exceção à WA-16 (decisão do Caio em 2026-11-17):** `human_reply` é o único kind que sai sem mensagem do vendedor nas últimas 24 h. Custo: o número do Jarvis passa a poder iniciar conversa nesses casos, o que é o principal fator de risco de bloqueio no WAHA. Mitigação: só admin envia, uma resposta por vez, sempre a quem já falou com o Jarvis. Exige ADR (9.11).
- **Aviso com texto da pergunta no grupo (D4):** troca privacidade por velocidade de triagem.
- **Sem id do aviso guardado:** o grupo não é lido, então não vale o custo de coluna nova em `ops.deliveries`.
- **Intervalo do grupo em memória:** simples; após reinício, o primeiro aviso pode sair sem esperar, o que é aceitável.
- **Tabela própria de sugestões** em vez de reaproveitar "Perguntas sem resposta na base": aquela é uma consulta agregada; a sugestão precisa de estado (pendente/aprovada/descartada) e da resposta.

### 9.9 Configuração (`.env`)

```
TEAM_GROUP_ENABLED=true
TEAM_GROUP_CHAT_ID=<id>@g.us          # grupo da equipe com o número do Jarvis
TEAM_GROUP_MIN_INTERVAL_SECONDS=3
PANEL_BASE_URL=https://jarvis.positiveco.com.br   # opcional: vazio usa https://PANEL_PUBLIC_HOST
# WAHA_BASE_URL / WAHA_API_KEY / WAHA_SESSION: não são mais lidos (o grupo usa JARVIS_WAHA_*)
```

### 9.10 Checklist de go-live (cada passo no servidor com confirmação do Caio)

1. Migration 401 + GRANTs de `content.faq_suggestions` e das colunas novas para `jarvis_app`.
2. Painel acessível em `jarvis.positiveco.com.br` com o IAM (pré-requisito do link, seção 7).
3. Criar o grupo da equipe, incluir o número do Jarvis, pegar o `@g.us` e configurar 9.9.
4. Teste ponta a ponta: pergunta sem resposta → aviso no grupo → link → resposta → vendedor recebe → sugestão em Relatórios.

### 9.11 ADR proposto (entra em `overview.md` §8 ao aprovar a Fase 2)

> **2026-11-17 — Resposta humana fora da janela de 24 h no WAHA (exceção à WA-16).** *Context:* a equipe responde escalados pelo painel (retorno-humano.1); o Caio decidiu "sem prazo" (D2). *Decision:* o kind `human_reply` é o único que o outbox envia sem mensagem do vendedor nas últimas 24 h; todo o resto continua "só responde". *Alternatives:* manter 24 h e responder por fora; pedir confirmação após 24 h. *Consequences:* aumenta o risco de restrição do número do Jarvis; reversível por configuração (`HUMAN_REPLY_IGNORE_WINDOW=false`).

## 10. Divergências da Fase 1 (para aprovação junto com o design)

1. **RH-10 / D2:** "sem prazo" só é possível abrindo exceção à WA-16 (janela de 24 h do outbox). **Decidido pelo Caio em 2026-11-17: a resposta da equipe fura a trava.** Fica reversível por `HUMAN_REPLY_IGNORE_WINDOW`.
2. **RH-5:** o link `/dashboard/inbox/ticket/J-n` redireciona para a página `/dashboard/inbox?u=&t=` (o retorno do login só funciona com URL de página, não de fragmento).
3. **RH-21:** em vez de recusar a subida, o envio ao grupo **passa a usar só** a configuração do WAHA do Jarvis; as variáveis `WAHA_*` antigas deixam de ser lidas. Mesmo efeito, menos configuração.
4. **RH-15:** "entregue" e "lida" exigem gravar os acks, hoje ignorados (`pipeline.handle_status`); vale para todas as saídas, não só as humanas.
5. **RH-2:** o texto atual dos avisos de mídia no grupo ("Mídia de usuário #id…") é substituído pelo formato novo.
6. **RH-18:** a FAQ ganha a origem `sugestao` (migration) e o formulário passa a aceitar resposta pré-preenchida (hoje só pergunta, via `?ticket=`).

## 11. Plano de reversão

- Desligar o grupo: `TEAM_GROUP_ENABLED=false` (avisos voltam a `panel_only`).
- Voltar a janela de 24 h para a resposta humana: `HUMAN_REPLY_IGNORE_WINDOW=false`.
- A migration 401 é aditiva; reverter o código não exige desfazer o banco (colunas e tabela novas ficam sem uso).

## 12. Divergências da Fase 4 (implementação, 2026-11-17 a 2026-11-18)

Registradas em detalhe em `../task.md` ("Desvios do design"). Resumo do que mudou em relação às seções 9 a 11:

1. **`PANEL_BASE_URL` não é obrigatório.** O link do aviso usa `PANEL_BASE_URL` ou `https://PANEL_PUBLIC_HOST`, que já existe. `WAHA_BASE_URL/WAHA_API_KEY/WAHA_SESSION` foram removidos da configuração; com o grupo ligado, a exigência é `JARVIS_WAHA_API_KEY` e `TEAM_GROUP_CHAT_ID` terminando em `@g.us` (RH-21).
2. **Janela de 24 h:** `outbox.reply_only_for` devolve `ReplyWindow | None` com os kinds isentos; `main.py` e `workers.py` não mudaram.
3. **Ticket respondido pelo Jarvis não vai ao grupo:** `question_tickets` grava entrega `group` também para `answered_bot`; o despacho marca essas entregas como `skipped` (`not_escalated`). Sem isso, toda pergunta iria ao grupo com a chave ligada.
4. **Envio na hora:** a rota `/responder` entrega todas as partes ao despachante do app depois do COMMIT (a varredura de 15 s fica como reserva).
5. **Leitura da caixa de resposta** em `app/panel/services/human_replies.py` (compartilhada pelo drawer e pelo Inbox). A caixa aparece para ticket `awaiting`, `answered_human` (complemento) ou `send_error`.
6. **Regra de template substituída:** `test_no_reply_to_seller` (PN-13/15) virou `test_reply_to_seller_only_via_responder`. A barra "Responder em: Grupo da equipe" do Inbox virou "Aviso no grupo".
7. **Celular:** sem o binário do Tailwind para gerar classe nova, o JS do chat troca `flex`/`hidden` abaixo de 768 px e há o botão "← Conversas". Nenhum CSS novo. Não foi testado em aparelho real.
8. **Foco no Inbox** (destaque da pergunta e cursor na caixa) só quando o chat é aberto com `t` (link do aviso).
9. **Mapa RH-n → teste** em `backend/tests/panel/AC_MAP.md` (seção "Retorno humano"), validado por `test_ac_coverage.py`.

