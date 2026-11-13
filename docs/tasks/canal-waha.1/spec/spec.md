---
task_id: CANAL-WAHA-1
status: draft
phase: 4-implementation
tasks_approved_by: Caio
tasks_approved_at: 2026-11-13
requirements_approved_by: Caio
requirements_approved_at: 2026-11-13
design_approved_by: Caio
design_approved_at: 2026-11-13
---

# Spec — Vendedores pelo WAHA, Meta como segunda opção (CANAL-WAHA-1)

> **Story:** [canal-waha.1](../../../stories/canal-waha.1.md) · **Epic:** [canal-waha](../../../epics/canal-waha.md)
> **Status:** Fase 1 (Requisitos, WA-1 a WA-20, decisões D1–D6) **aprovada por Caio em 2026-11-13**. Fase 2 (design: seções 9 a 11, `research.json`, `complexity.json`, ADR proposto em `overview.md`) **aprovada por Caio em 2026-11-13**, com as revisões de WA-8 e WA-15 (+WA-21 a WA-25). Fase 3 (tarefas: [`../task.md`](../task.md), 25 tarefas em 8 ondas, 74,5 h) **aprovada por Caio em 2026-11-13**. Fase 4 (implementação) em andamento. **Size:** L (18 pontos, ~80 h, com a marca da conversa).
> Critérios completos e testáveis: `requirements.json`.

## 1. Objetivo

Hoje o Jarvis fala com os vendedores só pela WhatsApp Cloud API da Meta
(`/webhook/whatsapp`, `WhatsAppCloudChannel`). O go-live está parado em pendências
da Meta (B-001). Esta história torna o **WAHA** o canal principal dos vendedores e
mantém a **Meta como segunda opção**, escolhida por `CHANNEL_PROVIDER`.

Tudo o que já existe depois da borda do canal continua igual: lista positiva,
limite de 60 mensagens/hora, idempotência, rastro, tickets, alertas, cérebro,
painel e LGPD. O que muda é a borda: como a mensagem entra, como sai, e como se
substituem os botões que o WhatsApp comum não entrega de forma confiável.

O ponto de partida do código ajuda: o parser de entrada já traduz o formato da Meta
para objetos de domínio (`InboundMessage`, `ButtonReply` com payload), e opt-in,
avaliação e "qual marca?" só enxergam esses payloads. A troca fica concentrada na
borda.

## 2. Escopo

**Dentro:**
- Chave `CHANNEL_PROVIDER` (`waha` | `meta`) e configuração própria de cada canal.
- Webhook `POST /webhook/waha`: limite por IP, tamanho, HMAC SHA-512, JSON, sessão esperada, filtros e delegação ao pipeline existente.
- Tradução dos eventos do WAHA (`message`, `message.reaction`, `message.ack`, `session.status`) para o domínio atual.
- Opt-in iniciado pelo vendedor e aceito por texto.
- Respostas sem avaliação no WAHA; "qual marca?" por opção numerada.
- Envio com "visto", "digitando" e partes em ordem; classificação de erros do WAHA.
- Alerta de sessão caída e de restrição do número.

**Fora:** seção 5.

## 3. Critérios de aceite (EARS)

Convenções: "pipeline" é o processamento atual (`Pipeline.handle_message`) a partir
de um `InboundMessage`. "Sem efeito" significa sem rastro de mensagem, sem
resposta e sem chamada externa; só contador de segurança. Os critérios dependentes
de decisão citam a decisão (D1–D6, seção 6).

### Seleção de canal

- **WA-1 Canal por configuração.** GIVEN `CHANNEL_PROVIDER=waha`, WHEN o app sobe, THEN exige só a configuração do WAHA (URL, chave, sessão do Jarvis, segredo HMAC) e **não** exige os campos `WHATSAPP_*` da Meta; com `CHANNEL_PROVIDER=meta`, exige os da Meta como hoje. Valor ausente ou inválido impede a subida com mensagem clara.
- **WA-2 Só uma porta aberta.** GIVEN um canal escolhido, WHEN chega requisição no webhook do outro canal, THEN responde 404 sem efeito.
- **WA-3 Meta preservada.** GIVEN `CHANNEL_PROVIDER=meta`, WHEN a suíte atual roda, THEN todos os testes de porta-fechada.1, painel.1 e cerebro.1 passam sem alteração de comportamento.

### Entrada (webhook do WAHA)

- **WA-4 Ordem de validação.** GIVEN `POST /webhook/waha`, WHEN chega uma requisição, THEN valida nesta ordem: limite por IP → tamanho (≤ `MAX_WEBHOOK_PAYLOAD_BYTES`) → `X-Webhook-Hmac` (HMAC SHA-512 do corpo bruto com `WAHA_WEBHOOK_HMAC_KEY`, comparação em tempo constante, e `X-Webhook-Hmac-Algorithm` = `sha512`) → JSON → sessão; qualquer falha devolve 429/413/403/400 **sem tocar no banco** e incrementa o contador de segurança correspondente.
- **WA-5 Sessão esperada.** GIVEN um evento assinado, WHEN `session` é diferente de `WAHA_JARVIS_SESSION`, THEN é descartado sem efeito e conta como `wrong_recipient`.
- **WA-6 Filtros.** GIVEN um evento `message`, WHEN é mensagem própria (`fromMe`), de grupo (`@g.us`), de status (`status@broadcast`), de canal ou de newsletter, THEN é descartado sem efeito e sem resposta.
- **WA-7 Tradução de conteúdo.** GIVEN um evento `message` de contato individual, WHEN é texto, THEN vira `InboundMessage` de texto; com `hasMedia=true`, vira mídia **sem baixar o arquivo** (mesmo fluxo de ticket e "equipe avisada" de hoje); outros tipos viram `unsupported`; `message.reaction` vira reação.
- **WA-8 Identificação do remetente.** GIVEN um remetente `<número>@c.us`, WHEN o pipeline identifica o usuário, THEN usa o número em E.164 com a tolerância ao 9º dígito de hoje. GIVEN um remetente `@lid` (identificador oculto do WhatsApp), THEN o Jarvis tenta resolver o número pelo WAHA; se não conseguir, trata como número desconhecido (silêncio e registro em "Pedindo acesso"), **nunca** como usuário autorizado. (Mecanismo de resolução: Fase 2.)
- **WA-9 Idempotência.** GIVEN o WAHA reentrega o mesmo evento (política de retentativa), WHEN o id da mensagem já foi registrado, THEN nenhum efeito se repete: sem segundo rastro, resposta, ticket ou alerta.
- **WA-10 Mesmo pipeline.** GIVEN um `InboundMessage` vindo do WAHA, WHEN é processado, THEN lista positiva, limite de 60/h, pausa, rastro, tickets, alertas e cérebro se comportam exatamente como com a Meta (suíte de pipeline executada também com mensagens de origem WAHA).

### Opt-in sem botões

- **WA-11 Cadastro não dispara mensagem** *(D1)*. GIVEN `CHANNEL_PROVIDER=waha`, WHEN uma pessoa é cadastrada (painel, importação ou CLI), THEN o Jarvis **não envia nada** a ela; a ficha mostra um link `wa.me` do número do Jarvis com texto pronto, para o gestor repassar. A ação "Reenviar boas-vindas" vira "Copiar link".
- **WA-12 Aviso na primeira mensagem.** GIVEN pessoa em `pending_optin`, WHEN ela manda a primeira mensagem, THEN recebe o aviso de privacidade (versão `NOTICE_VERSION`) terminado em "Responda **ACEITO** para continuar ou **NÃO** para recusar."
- **WA-13 Aceite e recusa por texto.** GIVEN pessoa em `pending_optin` que já recebeu o aviso, WHEN responde com uma forma de aceite (`aceito`, `aceitar`, `sim`, `1`, sem diferenciar maiúsculas e acentos), THEN o aceite é gravado com data, hora e versão do aviso, como no botão de hoje; WHEN responde com uma forma de recusa (`não`, `nao`, `recuso`, `2`), THEN a recusa é gravada como hoje; qualquer outra coisa recebe o lembrete, no máximo 1 a cada `OPTIN_REMINDER_INTERVAL_HOURS`.

### Avaliação e "qual marca?" sem botões

- **WA-14 Sem avaliação no WAHA** *(D2)*. GIVEN `CHANNEL_PROVIDER=waha`, WHEN uma resposta do cérebro é enviada, THEN sai **sem** pedido de avaliação e sem botões (as partes saem como `brain_answer`, nunca `brain_answer_rate`); reações do vendedor não gravam avaliação nem abrem ticket (só rastro, sem contar no limite de 60/h, como hoje). No painel, o indicador "Avaliados como úteis" mostra "sem dados" em vez de 0%. Com `CHANNEL_PROVIDER=meta`, a avaliação por botões continua como hoje.
### Marca da conversa (revisto com o Caio em 2026-11-13, opção A; substitui o WA-15 original)

- **WA-15 Enquete de marca depois do aceite.** GIVEN um vendedor que acabou de aceitar o aviso, WHEN o aceite é gravado, THEN recebe a confirmação e, se tiver **2 ou mais marcas** no cadastro, uma **enquete de escolha única** "Sobre qual marca você quer falar?" com as marcas do cadastro dele (e só elas). Com **1 marca**, essa marca vira a marca da conversa automaticamente e não há enquete.
- **WA-21 Escolha da marca.** GIVEN uma enquete de marca enviada, WHEN o vendedor vota numa opção **ou** escreve só o nome de uma das suas marcas, THEN essa marca vira a **marca da conversa**, válida até `BRAND_SESSION_MINUTES` (padrão 30) depois da última mensagem do vendedor (janela que se renova a cada mensagem). Se não havia pergunta pendente, responde "Certo, vamos falar de **<marca>**. Pode perguntar!"; se havia (WA-23), responde à pergunta pendente já com essa marca. Voto em marca fora do cadastro ou em enquete que não é a última enviada não tem efeito.
- **WA-22 Pergunta com marca da conversa.** GIVEN marca da conversa válida, WHEN chega uma pergunta que não cita marca, THEN o cérebro busca **só nessa marca** (mais os itens de "todas"), sem perguntar a marca de novo. WHEN a pergunta cita outra marca **do cadastro**, THEN responde sobre a marca citada e ela passa a ser a marca da conversa. WHEN cita marca **fora do cadastro**, THEN segue como hoje (pedido de acesso).
- **WA-23 Marca vencida ou ausente.** GIVEN vendedor com 2 ou mais marcas e sem marca da conversa válida, WHEN chega uma pergunta sem marca citada, THEN o Jarvis **guarda a pergunta** e manda a enquete "Sobre qual marca é a sua pergunta?"; o voto (WA-21) responde à pergunta guardada. O texto **"trocar marca"** (normalizado) a qualquer momento manda a enquete de novo.
- **WA-24 Qualidade preservada.** GIVEN as mudanças de WA-15 e WA-21 a WA-23, WHEN rodam os gates de avaliação, THEN `eval --mode palavras` continua APROVADO (com casos novos de marca da conversa) e `eval --mode ia` continua ≥ 98% factual e 100% preço.
- **WA-25 Enquete na Meta.** GIVEN `CHANNEL_PROVIDER=meta`, WHEN o Jarvis manda a escolha de marca, THEN usa botões de resposta (até 3 marcas) ou a mesma pergunta em texto com as marcas listadas (4 ou mais); o resto de WA-15 e WA-21 a WA-23 vale igual.

### Saída

- **WA-16 Só responde, nunca inicia** *(D1)*. GIVEN `CHANNEL_PROVIDER=waha`, WHEN o outbox vai enviar a um vendedor, THEN só envia se houver mensagem recebida desse vendedor nas últimas 24 h; caso contrário, não envia e registra `no_inbound_window`. (Alertas e avisos internos não passam por aqui.)
- **WA-17 Envio humano e em ordem.** GIVEN uma resposta, WHEN é despachada, THEN o Jarvis marca a mensagem recebida como vista, mostra "digitando" por um tempo proporcional ao texto (limites na Fase 2) e envia; respostas em várias partes saem **na ordem**, cada parte só depois de a anterior ter sido aceita pelo WAHA. O p95 do webhook ao envio continua ≤ 8 s (PRD §6).
- **WA-18 Erros e status.** GIVEN um envio ao WAHA, WHEN falha antes de o pedido sair (conexão recusada, 429, 503), THEN é transitório e pode ser retentado; WHEN o tempo esgota depois do envio, THEN é ambíguo e **não** é retentado (no máximo uma vez, como hoje); WHEN o WAHA devolve erro 463 ou 475 (restrição do número), THEN é falha definitiva e gera alerta. Eventos `message.ack` atualizam o status da saída (`-1` = falha, com o mesmo fluxo de falha de envio de hoje; `2`/`3` = entregue/lida).

### Operação

- **WA-19 Sessão caída.** GIVEN a sessão do Jarvis no WAHA, WHEN chega `session.status` diferente de `WORKING` (por exemplo, `SCAN_QR_CODE`, `FAILED`, `STOPPED`) ou a checagem periódica não a encontra `WORKING`, THEN abre alerta imediato "Jarvis desconectado do WhatsApp" (e-mail e painel, com dedup), o painel mostra uma faixa de canal fora, e `/health` informa o estado do canal. A volta a `WORKING` encerra a faixa.
- **WA-20 Privacidade e segredos.** GIVEN qualquer caminho deste canal, WHEN algo é registrado em log, THEN não há telefone, `@lid`, texto de mensagem, chave do WAHA nem segredo HMAC; a URL do WAHA continua restrita a `127.0.0.1`/`localhost`; a sessão do Jarvis é **diferente** da sessão usada pelo grupo da equipe *(D3)*.

## 4. Requisitos não funcionais

| Tema | Requisito |
|---|---|
| Latência | p95 ≤ 8 s do webhook ao envio, já com "digitando" (WA-17) |
| Confiabilidade | Sem envio duplicado ao vendedor (no máximo uma vez); reentrega do WAHA absorvida pela idempotência |
| Segurança | HMAC SHA-512 obrigatório; sem chamada ao banco antes da assinatura; WAHA só em localhost |
| Anti-banimento | Jarvis nunca inicia conversa no WAHA (WA-16); sem rajadas (partes em sequência) |
| Testes | Payloads de exemplo do WAHA como fixtures; suíte da Meta intacta; testes que não leem o `.env` do desenvolvedor |
| Compatibilidade | Python 3.12 e PostgreSQL 16; nenhuma dependência nova sem constar na Fase 2 |

## 5. Fora de escopo

- Remover ou alterar o comportamento do canal da Meta.
- Enviar a primeira mensagem a quem não escreveu (boas-vindas ativas, campanhas, envio em massa). Fica para a história futura de mensagens ativas a comerciantes, pela Meta (D1).
- Configurar a conta, o número e o token da Meta (só quando a história futura for construída).
- Grupos de WhatsApp como canal de vendedores; responder em grupo.
- Baixar, guardar ou interpretar mídia (continua como hoje: ticket e "equipe avisada").
- Recuperar mensagens recebidas enquanto a sessão estava fora do ar (o WAHA não as reentrega; o vendedor reenvia).
- Parear o número (QR code) pelo painel: feito no painel do próprio WAHA.
- Várias sessões ou vários números do Jarvis ao mesmo tempo.
- Enquetes para outros usos além da escolha de marca (por exemplo, opt-in ou avaliação).

## 6. Decisões para o Caio

| # | Decisão | Recomendação |
|---|---|---|
| **D1** | **Decidido pelo Caio em 2026-11-13.** No WAHA, o opt-in começa pelo vendedor (link `wa.me`), e o Jarvis só responde. Iniciar conversa (por exemplo, com comerciantes) fica para uma **história futura pela Meta**, num número próprio no portfólio da Positive; a Meta só é configurada quando essa história for construída. Nesta história, "só responde" é configuração (`OUTBOUND_REPLY_ONLY`), e a camada de canal admite mais de um canal ligado, com cada conversa presa ao canal por onde chegou. | — |
| **D2** | **Decidido pelo Caio em 2026-11-13:** sem avaliação no canal WAHA (WA-14). O KPI "Avaliados como úteis" fica sem dados enquanto o canal for o WAHA. | — |
| **D3** | Número do Jarvis no WAHA: **+55 85 99410-2873** (informado pelo Caio em 2026-11-13), chip **exclusivo** do Jarvis (confirmado pelo Caio em 2026-11-13), em sessão própria, separada da do grupo da equipe e da do AUTO-1. **Decidido.** | — |
| **D4** | **Decidido pelo Caio em 2026-11-13:** WAHA **próprio do Jarvis** (instalação separada da do AUTO-1, versão Core gratuita, outra porta em `127.0.0.1`), para que um problema em um não derrube o outro. Engine a fechar na Fase 2, depois de medir a memória livre no droplet. | Engine leve (**GOWS** ou **NOWEB**, sem navegador) num servidor compartilhado; WEBJS se a memória sobrar. |
| **D5** | **Decidido pelo Caio em 2026-11-13:** canal padrão `CHANNEL_PROVIDER=waha`; todo o Jarvis opera pelo WAHA. A Meta fica só como opção no código até a história futura de envio ativo. | — |
| **D6** | **Decidido pelo Caio em 2026-11-13 (opção A):** construir com o rascunho do aviso (texto em configuração, terminando em "Responda ACEITO para continuar ou NÃO para recusar."); o jurídico revisa em paralelo, e a **aprovação jurídica é condição de go-live** com vendedores. Mudança posterior do texto troca só a configuração e a `NOTICE_VERSION`. | — |

## 7. Premissas a verificar na Fase 2

- Versão e engine do WAHA instalados no droplet, se o plano (Core ou Plus) entrega `message.reaction`, `message.ack` e `session.status`, e como resolver `@lid` para número nessa versão.
- Formato exato da resposta de `POST /api/sendText` (id da mensagem enviada) e dos códigos de erro 463/475 na engine escolhida.
- Memória livre no droplet para um segundo WAHA (D4) e porta livre para ele; como o WAHA do AUTO-1 é instalado e operado (Docker ou PM2), para seguir o mesmo modelo.

## 8. Referências

- Documentação do WAHA: eventos e webhooks (`/docs/how-to/events/`), envio (`/docs/how-to/send-messages/`), como evitar bloqueio (`/docs/overview/how-to-avoid-blocking/`), consultada em 2026-11-13.
- `docs/architecture/overview.md` §8: ADR 2026-10-19 (interface de canal) e 2026-10-20 (WAHA só para avisos internos), a ser substituído em parte por ADR novo na Fase 2.
- `docs/tasks/porta-fechada.1/spec/spec.md` (opt-in, lista positiva, idempotência) e `docs/tasks/cerebro.1/spec/spec.md` (avaliação, "qual marca?", latência).

## 9. Design (Fase 2)

> Redigido em 2026-11-13. Aguarda aprovação do Caio. Princípio: **mudar só a borda**. O WAHA produz os mesmos `InboundMessage` e `InboundStatus` que a Meta, e o resto do Jarvis não sabe de onde a mensagem veio.

### 9.1 Componentes

```
WAHA do Jarvis (127.0.0.1:<porta>)
   │ POST /webhook/waha  (HMAC SHA-512)
   ▼
api/webhook_waha.py ── limite IP → tamanho → HMAC → JSON → sessão/me.id → filtros
   │  waha_inbound.parse_event()      (puro, sem I/O)
   │  waha_lid.resolve()              (só se o remetente vier como @lid)
   ▼
Pipeline.handle_message / handle_status        ← SEM MUDANÇA de contrato
   │  access.decide()  ← aceita "ACEITO"/"NÃO" em texto quando o canal não tem botões
   ▼
outbox (fila transacional, como hoje) ── regra "só responde" (WA-16) ── partes em ordem
   ▼
WahaChannel (implementa Channel) ── sendSeen → startTyping → espera → stopTyping → sendText
   ▲
SessionMonitor (laço de fundo, 60 s) + evento session.status → alerta "canal fora" (WA-19)
```

| Módulo | Ação | Responsabilidade |
|---|---|---|
| `app/config.py` | Alterar | `CHANNEL_PROVIDER` (`waha` padrão); campos `JARVIS_WAHA_*`; `WHATSAPP_*` passam a ser exigidos **só** com `meta` (WA-1) |
| `app/services/channel.py` | Alterar | `Channel` ganha `capabilities` (`buttons`, `templates`); `SendResult` sem mudança |
| `app/services/channel_factory.py` | Criar | Escolhe `WahaChannel` ou `WhatsAppCloudChannel` pela configuração |
| `app/services/waha_channel.py` | Criar | Cliente do WAHA do Jarvis: envio, "visto", "digitando", classificação de erros (WA-17, WA-18) |
| `app/services/waha_inbound.py` | Criar | Parser puro dos eventos `message`, `message.reaction`, `message.ack`, `session.status` (WA-6, WA-7) |
| `app/services/waha_lid.py` | Criar | Resolução de `@lid` para número pela API do WAHA, com cache em memória (WA-8) |
| `app/api/webhook_waha.py` | Criar | Rota `POST /webhook/waha` na mesma ordem do webhook da Meta (WA-4, WA-5) |
| `app/core/security.py` | Alterar | `verify_waha_hmac()` (SHA-512, tempo constante) |
| `app/main.py` | Alterar | Monta o canal pela fábrica; registra só o webhook do canal ativo (WA-2); liga o `SessionMonitor` |
| `app/services/access.py` | Alterar | Opt-in por texto quando o canal não tem botões (WA-13) |
| `app/services/optin.py` | Alterar | Sem modelos: `issue_notice` não enfileira boas-vindas (WA-11); o lembrete leva o aviso completo (WA-12) |
| `app/services/answering.py` | Alterar | Sem botões: partes só como `brain_answer` (WA-14) |
| `app/services/outbox.py` | Alterar | Regra "só responde" (WA-16); solta a conexão do banco durante o envio |
| `app/services/workers.py` | Alterar | Varredura não pega a parte N enquanto a anterior está pendente (WA-17); laço do `SessionMonitor` |
| `app/services/session_monitor.py` | Criar | Estado da sessão; alertas `channel_down` e `channel_restricted` com dedup (WA-19) |
| `app/panel/...` (ficha, métricas, faixa) | Alterar | Link `wa.me` e "Copiar link" (WA-11); KPI de avaliação "sem dados" (WA-14); faixa "canal fora" (WA-19) |
| `tests/conftest.py` | Alterar | Testes não leem o `.env` do desenvolvedor; suítes antigas fixam `CHANNEL_PROVIDER=meta` (WA-3) |
| `migrations/301_brand_session.sql` | Criar | Marca da conversa e o kind `brand_poll` (9.2) |
| `app/services/brand_session.py` | Criar | Ler, gravar, renovar e vencer a marca da conversa; decidir se manda enquete (WA-15, WA-21, WA-23) |
| `app/services/brain/provider.py` | Alterar | Busca restrita à marca da conversa; troca de marca citada; enquete no lugar de "qual marca?" (WA-22, WA-23) |
| `app/services/optin.py` | Alterar | Depois do aceite, enfileira a enquete ou fixa a marca única (WA-15) |
| `app/services/channel.py`, `waha_channel.py`, `whatsapp.py` | Alterar | `send_poll(to, pergunta, opções)`: enquete no WAHA; botões ou texto na Meta (WA-25) |
| `app/services/waha_inbound.py` | Alterar | Evento `poll.vote` vira `InboundMessage` de escolha de marca (WA-21) |
| `app/services/brain/eval.py` e casos do gate | Alterar | Casos novos de marca da conversa (WA-24) |

**Não muda:** o contrato de `pipeline.py`, `identity.py`, `trace.py`, o cérebro, retenção, LGPD e `whatsapp.py` (Meta).

### 9.2 Dados

**Uma migration, só para a marca da conversa** (`301_brand_session.sql`, aditiva, PostgreSQL 16):
- `access.users.active_brand text NULL` (CHECK nas 5 marcas) e `access.users.active_brand_at timestamptz NULL` (última mensagem do vendedor com essa marca); a validade é `active_brand_at + BRAND_SESSION_MINUTES`, calculada na leitura.
- `chat.outbound_messages.kind` ganha `brand_poll` (mesmo padrão `DO $$ … DROP/ADD CONSTRAINT` da migration 201).
- Não é dado pessoal novo; a exclusão LGPD já apaga a linha do usuário inteira.

O canal em si não pede migration. Conferido nas migrations 002–202:
- `chat.message_trace.wa_message_id` é `text UNIQUE`: o id do WAHA (`false_5585…@c.us_3EB0…`) cabe como está, e a idempotência (WA-9) já vem pronta.
- `chat.outbound_messages.status` já tem `discarded`: a regra "só responde" usa `discarded` com `send_error = 'no_inbound_window'`.
- `ops.alerts.kind` não tem `CHECK`: `channel_down` e `channel_restricted` entram sem migration.
- `chat.message_trace (user_id, received_at DESC)` já tem índice: a checagem de 24 h é barata.
- O estado da sessão do canal fica em `ops.system_state` (chave `channel_session`), tabela que já guarda as flags.

### 9.3 Contratos

```python
# channel.py — acrescenta capacidades; o resto do Protocol fica igual
@dataclass(frozen=True)
class ChannelCapabilities:
    buttons: bool      # Meta: True · WAHA: False
    templates: bool    # Meta: True · WAHA: False

class Channel(Protocol):
    capabilities: ChannelCapabilities
    async def send_text(self, to: str, body: str) -> SendResult: ...
    async def send_template(self, to: str, template: TemplateMessage) -> SendResult: ...
    async def send_reply_buttons(self, to: str, body: str, buttons: Sequence[ReplyButton]) -> SendResult: ...
    def verify_signature(self, raw_body: bytes, header: str) -> bool: ...

# WahaChannel.send_template      -> SendResult(ok=False, error_code="unsupported")  (nunca chamado: WA-11)
# WahaChannel.send_reply_buttons -> envia só o corpo como texto (os botões são descartados)

# waha_inbound.py — puro
def parse_event(body: Mapping[str, Any], *, session: str, jarvis_number: str) -> WahaEvent
# WahaEvent = Message(InboundMessage) | Status(InboundStatus) | Session(state) | Ignored(reason)
```

`message.ack` vira o `InboundStatus` de hoje: `-1` → `failed`, `1` → `sent`, `2` → `delivered`, `3`/`4` → `read`, `0` → ignorado.

Classificação de erros do WAHA (WA-18):

| Situação | Resultado |
|---|---|
| `ConnectError`, `ConnectTimeout`, `PoolTimeout`; HTTP 429 ou 503; sessão sabidamente fora de `WORKING` | transitório (nada saiu; pode retentar) |
| `ReadTimeout` e outros erros depois de enviar; HTTP 500/502/504 sem código conhecido | ambíguo (não retenta) |
| Corpo de erro com `463` ou `475` | definitivo + alerta `channel_restricted` |
| Outro 4xx | definitivo |
| 2xx sem id da mensagem | ambíguo (mesma regra da Meta) |

### 9.4 Fluxos

**Opt-in (WA-11 a WA-13).**
1. Cadastro (painel, importação ou CLI): o aviso é aberto (`optin_requests` `open`), como hoje, mas **nada é enfileirado** quando o canal não tem modelos.
2. O gestor copia na ficha o link `https://wa.me/5585994102873?text=Oi%20Jarvis` e repassa ao vendedor.
3. A primeira mensagem do vendedor cai em `pending_optin`, e `maybe_remind` envia o **aviso completo** (`TEXT_OPTIN_NOTICE`) e grava `optin_reminder_at`.
4. Em `access.decide`, com o canal sem botões, um **texto** normalizado em `{aceito, aceitar, sim, 1}` ou `{nao, recuso, 2}` vale como resposta ao aviso aberto, **desde que o aviso já tenha sido mostrado** (`optin_reminder_at` preenchido). Isso evita que um "sim" solto na primeira mensagem aceite um aviso que a pessoa nem viu.
5. Daí em diante, o fluxo é o atual (`optin.accept` / `optin.decline`, com a mesma gravação de data, hora e versão).

**Resposta (WA-17).** O cérebro enfileira N partes, como hoje. O despacho:
1. pega a parte (claim condicional, como hoje) e **devolve a conexão ao pool**;
2. no WAHA, chama `sendSeen` → `startTyping` → espera `clamp(len(texto) / 60, 1 s, 2,5 s)` → `stopTyping` → `sendText`. Falha em "visto" ou "digitando" só vira log, sem impedir o envio;
3. pega outra conexão e grava o resultado.

A varredura ignora uma parte se existir parte anterior da mesma resposta (mesmo `inbound_trace_id`, `id` menor) em `pending` ou `sending`. Assim as partes saem sempre em ordem.

**Só responde (WA-16).** No despacho, com `OUTBOUND_REPLY_ONLY=true`: se não houver `message_trace` do usuário nas últimas 24 h, a saída vira `discarded` (`no_inbound_window`), sem chamar o WAHA.

**Sessão (WA-19).** O evento `session.status` e um laço de 60 s (`GET /api/sessions/{sessão}`) atualizam `ops.system_state`. Quando a sessão sai de `WORKING`, abre o alerta `channel_down` (dedup até voltar). Quando volta a `WORKING`, o estado é limpo e a faixa some. `/health` passa a devolver `{"status": "ok", "channel": "working|down"}`, mantendo o 503 só para banco fora do ar.

### 9.5 Configuração (`.env`)

| Variável | Padrão | Observação |
|---|---|---|
| `CHANNEL_PROVIDER` | `waha` | `waha` ou `meta` (D5) |
| `JARVIS_WAHA_BASE_URL` | `http://127.0.0.1:3001` | WAHA **do Jarvis**, separado do `WAHA_BASE_URL` do grupo (D4); só `127.0.0.1`/`localhost` |
| `JARVIS_WAHA_API_KEY` | — | Obrigatória com `waha` |
| `JARVIS_WAHA_SESSION` | `jarvis` | Nome da sessão no WAHA do Jarvis |
| `JARVIS_WAHA_HMAC_KEY` | — | Obrigatória com `waha`; a mesma configurada no webhook da sessão |
| `JARVIS_WHATSAPP_NUMBER` | — | `+5585994102873` (D3); usado no link `wa.me` e na checagem de `me.id` |
| `OUTBOUND_REPLY_ONLY` | `true` | WA-16 |
| `TEXT_OPTIN_NOTICE` | rascunho (D6) | Aviso completo enviado na 1ª mensagem |
| `WAHA_TYPING_MAX_SECONDS` | `2.5` | Teto da espera de "digitando" |
| `BRAND_SESSION_MINUTES` | `30` | Validade da marca da conversa desde a última mensagem (WA-21) |
| `TEXT_BRAND_POLL_START` | "Sobre qual marca você quer falar?" | Enquete depois do aceite (WA-15) |
| `TEXT_BRAND_POLL_QUESTION` | "Sobre qual marca é a sua pergunta?" | Enquete com pergunta guardada (WA-23) |
| `TEXT_BRAND_SELECTED` | "Certo, vamos falar de {marca}. Pode perguntar!" | Confirmação da escolha (WA-21) |

O grupo da equipe continua com `WAHA_BASE_URL`, `WAHA_API_KEY` e `WAHA_SESSION`, sem mudança.

### 9.6 Logs e privacidade (WA-20)

Novos eventos: `waha_webhook_received` (contagens), `waha_event_ignored` (`reason`), `waha_lid_unresolved` (sem o lid; a tentativa segue para "Pedindo acesso" como "Número oculto"), `waha_send_failed` (`error_code`, `transient`, `ambiguous`) e `channel_session_changed` (`state`). Nenhum leva telefone, `@lid`, texto, chave ou HMAC. Um teste de segurança varre os logs da suíte do WAHA atrás de números e do segredo, como já existe para a Meta.

### 9.7 Testes

- **Fixtures:** payloads de exemplo da documentação do WAHA (texto, mídia, grupo, `fromMe`, `@lid`, reação, ack, `session.status`) em `tests/fixtures/waha/`.
- **Unitários:** `waha_inbound` (cada filtro e tradução), `verify_waha_hmac`, classificação de erros, opt-in por texto em `access.decide`, regra de 24 h e ordem das partes.
- **Integração:** webhook ponta a ponta com `FakeChannel` sem botões; o pipeline de porta-fechada roda também com mensagens de origem WAHA (WA-10); nenhuma consulta ao banco antes da assinatura (espião no pool, WA-4).
- **Regressão da Meta (WA-3):** suítes atuais com `CHANNEL_PROVIDER=meta` fixado no `conftest`, sem mudar nenhuma asserção.
- **Higiene:** `Settings` dos testes sem ler o `.env` do desenvolvedor. Isso corrige as 72 falhas de ambiente vistas em 2026-11-13 e a falha garantida no CI. Junto: `tzdata` entra em `requirements-dev.txt` (**única dependência nova**, só de desenvolvimento: no Windows o Python não traz a base de fusos e `ZoneInfo("America/Sao_Paulo")` falha; no droplet Linux ela vem do sistema), e o teste `test_resposta_registrada_com_ferramentas` deixa de depender da data de hoje (a consulta usa `now()` do banco e o teste semeia em data fixa).
- **Manual (go-live):** opt-in, pergunta, mídia e "sair" com um celular real contra o WAHA do Jarvis.

### 9.8 Trade-offs

- **Um canal ativo por vez, não vários.** D1 pedia a camada pronta para vários canais. O design deixa a interface e a fábrica prontas, mas **não** grava o canal por conversa: com tudo no WAHA (D5), essa coluna não teria uso. A história futura da Meta acrescenta a coluna `channel` e o roteamento, numa migration aditiva.
- **"Digitando" aumenta a latência** em até 2,5 s por parte. Para respostas de 1 parte ainda cabe no p95 de 8 s; respostas longas (3 partes ou mais) podem passar. Aceito: as partes seguintes saem em ordem, e o PRD mede a primeira entrega.
- **Soltar a conexão durante o envio** muda o despacho, que é núcleo, mas corrige o achado da análise (pool preso durante o HTTP), que pioraria com "digitando". É coberto pelas suítes de outbox.
- **`@lid` no lugar do BSUID.** A coluna se chama `bsuid`, mas passa a guardar o identificador alternativo do canal ativo (BSUID na Meta, LID no WAHA). Evita migration; o nome fica documentado aqui e no `identity.py` (seção 10, item 1).

### 9.9 Marca da conversa (WA-15, WA-21 a WA-25)

**Escolha.** O voto da enquete chega no WAHA como `poll.vote`. O parser o traduz num `InboundMessage` de texto cujo conteúdo é o **nome da opção** (por exemplo, "Jungle"), com o id do voto como id da mensagem (idempotência de graça). Assim, votar e escrever "Jungle" seguem **o mesmo caminho**. Na Meta, o botão faz o mesmo. Um voto só vale se a enquete for a **última `brand_poll`** enviada ao vendedor (comparando o id da enquete com o `wa_message_id` da saída) e se a marca estiver no cadastro dele.

**Onde a regra mora.** `brand_session.py` é chamado pelo provedor do cérebro antes da busca:
1. Mensagem que é **só** o nome de uma marca do cadastro (voto ou texto): grava `active_brand` e `active_brand_at`. Se a última troca da memória (já existente, `memory.py`) é um "qual marca?" (`ask_brand`) com pergunta guardada, responde a essa pergunta com a marca escolhida; senão, responde `TEXT_BRAND_SELECTED`.
2. "trocar marca": limpa a marca e responde com a enquete.
3. Pergunta que cita marca do cadastro: busca nela e a torna a marca da conversa (WA-22).
4. Pergunta sem marca, marca válida: busca com `brands=[active_brand]` (mais "todas") e renova `active_brand_at`.
5. Pergunta sem marca, sem marca válida, 2+ marcas: desfecho `ASK_BRAND` (já existe), agora entregue como **enquete** (`brand_poll`) em vez do texto `TEXT_BRAIN_ASK_BRAND`. A pergunta fica guardada pela memória, como hoje.
6. Vendedor com 1 marca: a marca é fixa e nunca há enquete.

**Depois do aceite.** `optin.accept` enfileira, na mesma transação, a confirmação e, se houver 2+ marcas, a `brand_poll` (WA-15). As duas saem em ordem pela regra de partes (9.4).

**Por que a pergunta guardada funciona sem estado novo:** hoje o desfecho `ask_brand` já entra na memória de contexto (`_ELIGIBLE_OUTCOMES`), e é assim que "Jungle" depois de "Quais os sabores?" é respondido. A enquete só troca a forma de perguntar; a memória continua fazendo a junção.

**Gate (WA-24).** Casos novos no `eval`: pergunta com marca válida (busca restrita), troca de marca citada, marca vencida (enquete), voto respondendo à pergunta guardada, vendedor de marca única. A mudança na busca roda os dois gates, como manda a lição L-004.

### 9.10 Instalação e checklist de go-live (cada passo no servidor com confirmação do Caio)

1. Conferir no farol a memória livre (`free -h`), uma porta livre (`ss -ltnp`) e como o WAHA do AUTO-1 está instalado (`docker ps`, `pm2 ls`). Decidir a engine (D4): GOWS se sobrar menos de ~1 GB; com mais, GOWS ou WEBJS.
2. Subir o WAHA do Jarvis (Core) no mesmo modelo do AUTO-1, em `127.0.0.1:<porta>`, com chave de API própria e sem expor a porta para fora do servidor.
3. Criar a sessão `jarvis`, parear o chip (85) 99410-2873 pelo QR code e configurar o webhook `https://<subdomínio do Jarvis>/webhook/waha` com HMAC e só os eventos `message`, `message.reaction`, `message.ack`, `poll.vote` e `session.status`. Configurar o perfil do número: foto, nome "Jarvis" e recado (recomendação do WAHA contra bloqueio).
4. Capturar um payload real de cada evento e comparar com as fixtures (premissas R1–R3 de `research.json`).
5. Preencher o `.env` do Jarvis (9.5), subir e testar com um celular.
6. Obter a aprovação jurídica do aviso (D6) antes de mandar o link aos vendedores.

## 10. Divergências da Fase 1 (para aprovação junto com o design)

1. **WA-8 (revisto com o Caio em 2026-11-13):** remetente `@lid` que o WAHA não resolve para número **aparece em "Pedindo acesso" como "Número oculto"** e nunca é tratado como autorizado. O `@lid` ocupa o identificador alternativo que já existe para o BSUID da Meta (`Sender.bsuid`, `access.access_attempts.bsuid`, `access.users.bsuid`, todos `UNIQUE`; a tentativa só com esse identificador já é permitida pela `CHECK has_identifier`), **sem migration**. Ao autorizar um pedido de "Número oculto", o administrador informa o telefone verdadeiro no cadastro, e o `@lid` da tentativa é gravado em `access.users.bsuid` na mesma transação; daí em diante, `identity.resolve` reconhece o vendedor pelo `@lid`. A tela mostra o aviso "Número oculto: confirme com a pessoa antes de autorizar". O opt-in continua obrigatório. Arquivos a mais: `app/panel/api/pessoas_cadastro.py`, `app/panel/services/people_read.py` (rótulo "Número oculto"), `frontend/templates/_pessoas_pedidos.html` e `_pessoa_form.html`.
2. **WA-15 (revisto com o Caio em 2026-11-13, opção A):** a lista numerada foi trocada por **enquete do WhatsApp** (o único formato de "caixinha" confiável no WAHA; botões e listas exigem conta oficial), e a escolha de marca deixou de ser só resposta à ambiguidade: passa a ser a **marca da conversa**, escolhida logo depois do aceite e válida por 30 minutos desde a última mensagem (WA-15, WA-21 a WA-25). Isso muda o cérebro, e não só o canal; ver 9.9.
3. **D1 (vários canais):** ver 9.8, primeiro item.

## 11. Plano de reversão

- **Voltar para a Meta:** `CHANNEL_PROVIDER=meta` e os `WHATSAPP_*` no `.env`, e reiniciar. A migration 301 é aditiva e pode ficar; a marca da conversa funciona também na Meta (WA-25).
- **Desligar a marca da conversa:** `BRAND_SESSION_MINUTES=0` faz o Jarvis voltar ao comportamento atual ("qual marca?" só na ambiguidade).
- **Desligar o canal sem derrubar o painel:** pausa global do Jarvis (já existe) ou parar o WAHA do Jarvis. O painel e o AUTO-1 seguem funcionando.
- **Código:** as mudanças em `access`, `optin`, `answering` e `outbox` dependem das capacidades do canal; com a Meta, o caminho é o de hoje (WA-3). Reverter é um `git revert` dos commits desta história.
