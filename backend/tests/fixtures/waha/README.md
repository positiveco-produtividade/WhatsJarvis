# Fixtures de eventos do WAHA (CANAL-WAHA-1, T04)

Payloads de exemplo dos webhooks do WAHA usados pelos testes do canal (parser,
webhook e regressão). Montados a partir da documentação oficial do WAHA
(eventos e webhooks, envio de mensagens), consultada em 2026-11-13.

- **Números fictícios:** Jarvis = `5500000000001`, vendedor = `5500000000002`.
  Nenhum telefone real.
- **Sessão:** `jarvis`; **engine:** `GOWS` (D4, a confirmar no go-live).
- **Não são capturas reais.** No go-live (T25), cada evento real é comparado com
  o arquivo correspondente; se o formato divergir, o arquivo é trocado pela
  captura (com os números anonimizados) e o parser é ajustado. Pontos a conferir
  (`spec/research.json`): R1 (`@lid`), R2 (id da mensagem enviada),
  R3 (erros 463/475) e R5 (`poll.vote`).

| Arquivo | Evento | Para testar |
|---|---|---|
| `message_text.json` | `message` | texto de contato individual |
| `message_media.json` | `message` | mídia (`hasMedia`), sem download |
| `message_group.json` | `message` | grupo (`@g.us`): ignorado |
| `message_from_me.json` | `message` | mensagem do próprio número: ignorada |
| `message_status_broadcast.json` | `message` | status: ignorado |
| `message_newsletter.json` | `message` | canal/newsletter: ignorado |
| `message_lid.json` | `message` | remetente oculto (`@lid`) |
| `message_reaction.json` | `message.reaction` | reação 👍 |
| `message_reaction_removed.json` | `message.reaction` | reação removida (texto vazio) |
| `poll_vote.json` | `poll.vote` | voto na enquete de marca |
| `ack_error.json`, `ack_server.json`, `ack_device.json`, `ack_read.json`, `ack_pending.json` | `message.ack` | status de entrega (-1, 1, 2, 3, 0) |
| `session_status_working.json`, `session_status_failed.json`, `session_status_scan_qr.json` | `session.status` | sessão no ar / caída |
