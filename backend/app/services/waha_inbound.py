"""Parser puro dos eventos do webhook do WAHA (spec canal-waha.1, 9.3; task WA-T07).

Traduz cada evento para os mesmos objetos de dominio do parser da Meta (`InboundMessage`,
`InboundStatus`), para o pipeline nao saber de onde a mensagem veio, ou para `WahaSession`
(estado da sessao) e `WahaIgnored` (descartado, com motivo). Sem I/O e sem log; nunca levanta
excecao por payload malformado (o webhook ja validou assinatura e JSON).

- Remetente `<numero>@c.us` vira `Sender(wa_id=<numero>)`, como na Meta; `<id>@lid` (numero
  oculto) vira `Sender(bsuid="<id>@lid")`: o identificador alternativo que ja existe para o
  BSUID da Meta (spec 10, item 1). A resolucao do lid para numero e feita fora daqui.
- Voto na enquete (`poll.vote`) vira texto com o nome da opcao escolhida, como se o vendedor
  tivesse escrito a marca (spec 9.9); o id da enquete vai em `button.payload` como
  `POLL_VOTE:<id da enquete>` para conferir se e a ultima enquete enviada.
- Midia nao e baixada: so o tipo a partir do mimetype.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.inbound import ButtonReply, ContentKind, InboundMessage, InboundStatus, Sender

POLL_VOTE_PREFIX = "POLL_VOTE:"
RAW_TYPE_PREFIX = "waha:"

# Motivos de descarte (contadores e log no webhook; nunca com dado pessoal).
WRONG_RECIPIENT = "wrong_recipient"
FROM_ME = "from_me"
GROUP = "group"
STATUS_BROADCAST = "status_broadcast"
NEWSLETTER = "newsletter"
BROADCAST = "broadcast"
UNKNOWN_CHAT = "unknown_chat"
MALFORMED = "malformed"
UNSUPPORTED_EVENT = "unsupported_event"
ACK_IGNORED = "ack_ignored"
POLL_VOTE_EMPTY = "poll_vote_empty"
POLL_VOTE_MULTIPLE = "poll_vote_multiple"

# message.ack (documentacao do WAHA): -1 ERROR, 0 PENDING, 1 SERVER, 2 DEVICE, 3 READ, 4 PLAYED.
_ACK_STATUS: dict[int, str] = {-1: "failed", 1: "sent", 2: "delivered", 3: "read", 4: "read"}

_MEDIA_BY_MIME_PREFIX: tuple[tuple[str, ContentKind], ...] = (
    ("audio/", ContentKind.AUDIO),
    ("image/webp", ContentKind.STICKER),
    ("image/", ContentKind.IMAGE),
    ("video/", ContentKind.VIDEO),
)


@dataclass(frozen=True)
class WahaMessage:
    message: InboundMessage


@dataclass(frozen=True)
class WahaStatus:
    status: InboundStatus


@dataclass(frozen=True)
class WahaSession:
    state: str  # WORKING, SCAN_QR_CODE, FAILED, STOPPED, STARTING, ...


@dataclass(frozen=True)
class WahaIgnored:
    reason: str


WahaEvent = WahaMessage | WahaStatus | WahaSession | WahaIgnored


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _digits(jid: str | None) -> str | None:
    """`5500000000002@c.us` -> `5500000000002`; so digitos ASCII, senao None."""
    if jid is None:
        return None
    local = jid.split("@", 1)[0].lstrip("+")
    return local if local.isascii() and local.isdigit() else None


def chat_kind(jid: str | None) -> str:
    """Classifica o JID do chat: `individual`, `lid` ou o motivo de descarte."""
    if jid is None:
        return MALFORMED
    if jid == "status@broadcast":
        return STATUS_BROADCAST
    domain = jid.rsplit("@", 1)[-1] if "@" in jid else ""
    if domain == "g.us":
        return GROUP
    if domain == "newsletter":
        return NEWSLETTER
    if domain == "broadcast":
        return BROADCAST
    if domain in ("c.us", "s.whatsapp.net"):
        return "individual" if _digits(jid) else MALFORMED
    if domain == "lid":
        return "lid"
    return UNKNOWN_CHAT


def sender_from_jid(jid: str | None) -> Sender | WahaIgnored:
    kind = chat_kind(jid)
    if kind == "individual":
        return Sender(wa_id=_digits(jid))
    if kind == "lid" and jid is not None:
        return Sender(bsuid=jid)
    return WahaIgnored(kind)


def _wrong_recipient(body: Mapping[str, Any], session: str, jarvis_number: str | None) -> bool:
    if body.get("session") != session:
        return True
    me = _digits(_str(_map(body.get("me")).get("id")))
    expected = _digits(jarvis_number)
    return bool(me and expected and me != expected)


def _media_kind(payload: Mapping[str, Any]) -> tuple[ContentKind, str | None]:
    mime = _str(_map(payload.get("media")).get("mimetype"))
    lowered = (mime or "").lower()
    for prefix, kind in _MEDIA_BY_MIME_PREFIX:
        if lowered.startswith(prefix):
            return kind, mime
    return ContentKind.DOCUMENT, mime


def _content(payload: Mapping[str, Any]) -> tuple[ContentKind, str | None, str | None]:
    """(tipo, texto, mimetype) de um evento `message`."""
    if payload.get("hasMedia") is True:
        kind, mime = _media_kind(payload)
        return kind, None, mime
    if isinstance(payload.get("location"), Mapping):
        return ContentKind.LOCATION, None, None
    vcards = payload.get("vCards")
    if isinstance(vcards, list) and vcards:
        return ContentKind.CONTACTS, None, None
    body = _str(payload.get("body"))
    if body is not None and body.strip():
        return ContentKind.TEXT, body, None
    return ContentKind.UNSUPPORTED, None, None


def _message(payload: Mapping[str, Any]) -> WahaEvent:
    if payload.get("fromMe") is True:
        return WahaIgnored(FROM_ME)
    message_id = _str(payload.get("id"))
    sender = sender_from_jid(_str(payload.get("from")))
    if isinstance(sender, WahaIgnored):
        return sender
    if message_id is None:
        return WahaIgnored(MALFORMED)
    kind, text, mime = _content(payload)
    return WahaMessage(
        InboundMessage(
            wa_message_id=message_id,
            sender=sender,
            kind=kind,
            timestamp=_int(payload.get("timestamp")),
            text=text,
            text_length=len(text) if text is not None else None,
            mime_type=mime,
            raw_type=f"{RAW_TYPE_PREFIX}message",
        )
    )


def _reaction(payload: Mapping[str, Any]) -> WahaEvent:
    if payload.get("fromMe") is True:
        return WahaIgnored(FROM_ME)
    message_id = _str(payload.get("id"))
    sender = sender_from_jid(_str(payload.get("from")))
    if isinstance(sender, WahaIgnored):
        return sender
    if message_id is None:
        return WahaIgnored(MALFORMED)
    return WahaMessage(
        InboundMessage(
            wa_message_id=message_id,
            sender=sender,
            kind=ContentKind.REACTION,
            timestamp=_int(payload.get("timestamp")),
            raw_type=f"{RAW_TYPE_PREFIX}message.reaction",
        )
    )


def _ack(payload: Mapping[str, Any]) -> WahaEvent:
    if payload.get("fromMe") is not True:  # ack de mensagem recebida nao interessa ao outbox
        return WahaIgnored(ACK_IGNORED)
    message_id = _str(payload.get("id"))
    ack = _int(payload.get("ack"))
    status = _ACK_STATUS.get(ack) if ack is not None else None
    if message_id is None or status is None:
        return WahaIgnored(ACK_IGNORED)
    return WahaStatus(
        InboundStatus(
            wa_message_id=message_id,
            status=status,
            recipient_id=_digits(_str(payload.get("from"))),
            timestamp=_int(payload.get("timestamp")),
            error_title="waha_ack_error" if status == "failed" else None,
        )
    )


def _poll_vote(payload: Mapping[str, Any]) -> WahaEvent:
    vote = _map(payload.get("vote"))
    poll = _map(payload.get("poll"))
    if vote.get("fromMe") is True:
        return WahaIgnored(FROM_ME)
    sender = sender_from_jid(_str(vote.get("from")))
    if isinstance(sender, WahaIgnored):
        return sender
    vote_id = _str(vote.get("id"))
    poll_id = _str(poll.get("id"))
    options = vote.get("selectedOptions")
    if vote_id is None or poll_id is None or not isinstance(options, list):
        return WahaIgnored(MALFORMED)
    chosen = [o for o in options if isinstance(o, str) and o.strip()]
    if not chosen:
        return WahaIgnored(POLL_VOTE_EMPTY)
    if len(chosen) > 1:
        return WahaIgnored(POLL_VOTE_MULTIPLE)
    option = chosen[0].strip()
    timestamp = _int(vote.get("timestamp"))
    # Trocar o voto gera novo evento com o mesmo id do voto: o instante entra no id para a
    # troca valer como nova escolha, e a reentrega (mesmo id e instante) seguir idempotente.
    return WahaMessage(
        InboundMessage(
            wa_message_id=f"poll:{vote_id}:{timestamp if timestamp is not None else 0}",
            sender=sender,
            kind=ContentKind.TEXT,
            timestamp=timestamp,
            text=option,
            text_length=len(option),
            button=ButtonReply(payload=f"{POLL_VOTE_PREFIX}{poll_id}", text=option),
            raw_type=f"{RAW_TYPE_PREFIX}poll.vote",
        )
    )


def _session(payload: Mapping[str, Any]) -> WahaEvent:
    state = _str(payload.get("status"))
    if state is None:
        return WahaIgnored(MALFORMED)
    return WahaSession(state.strip().upper())


_HANDLERS = {
    "message": _message,
    "message.reaction": _reaction,
    "message.ack": _ack,
    "poll.vote": _poll_vote,
    "session.status": _session,
}


def parse_event(
    body: Any, *, session: str, jarvis_number: str | None = None
) -> WahaEvent:
    """Traduz um evento do WAHA (ja com assinatura e JSON validos) para o dominio.

    Sessao diferente de `session` ou `me.id` diferente do numero do Jarvis = descarte
    `wrong_recipient` (WA-5). Evento fora da lista = `unsupported_event`.
    """
    if not isinstance(body, Mapping):
        return WahaIgnored(MALFORMED)
    if _wrong_recipient(body, session, jarvis_number):
        return WahaIgnored(WRONG_RECIPIENT)
    handler = _HANDLERS.get(_str(body.get("event")) or "")
    if handler is None:
        return WahaIgnored(UNSUPPORTED_EVENT)
    payload = body.get("payload")
    if not isinstance(payload, Mapping):
        return WahaIgnored(MALFORMED)
    return handler(payload)
