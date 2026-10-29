"""Parser puro do payload do webhook da WhatsApp Cloud API (sem I/O).

Normaliza mensagens e statuses em dataclasses imutaveis. Nunca levanta excecao
por payload malformado: itens invalidos sao descartados (o webhook ja validou
assinatura e JSON). Nada de midia e baixado: so tipo, mime_type e tamanho do texto.

Premissa Q5: o BSUID pode vir em `contacts[].user_id` e/ou `messages[].from_user_id`
(formato documentado, nao capturado). Toda essa suposicao fica em `extract_sender`.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

EXPECTED_OBJECT = "whatsapp_business_account"
MESSAGES_FIELD = "messages"


class ContentKind(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    STICKER = "sticker"
    LOCATION = "location"
    CONTACTS = "contacts"
    BUTTON = "button"  # botao de modelo OU interactive.button_reply
    INTERACTIVE = "interactive"  # demais interativos (lista, flow, ...)
    REACTION = "reaction"
    SYSTEM = "system"
    UNSUPPORTED = "unsupported"

    @property
    def is_media(self) -> bool:
        return self in _MEDIA_KINDS


_MEDIA_KINDS = frozenset(
    {
        ContentKind.AUDIO,
        ContentKind.IMAGE,
        ContentKind.VIDEO,
        ContentKind.DOCUMENT,
        ContentKind.STICKER,
        ContentKind.LOCATION,
        ContentKind.CONTACTS,
    }
)
_KIND_BY_TYPE: dict[str, ContentKind] = {
    "text": ContentKind.TEXT,
    "audio": ContentKind.AUDIO,
    "image": ContentKind.IMAGE,
    "video": ContentKind.VIDEO,
    "document": ContentKind.DOCUMENT,
    "sticker": ContentKind.STICKER,
    "location": ContentKind.LOCATION,
    "contacts": ContentKind.CONTACTS,
    "reaction": ContentKind.REACTION,
    "system": ContentKind.SYSTEM,
}
_MIME_KINDS = frozenset(
    {
        ContentKind.AUDIO,
        ContentKind.IMAGE,
        ContentKind.VIDEO,
        ContentKind.DOCUMENT,
        ContentKind.STICKER,
    }
)


@dataclass(frozen=True)
class Sender:
    """Identificadores do remetente: wa_id (telefone, pode vir sem o 9) e/ou BSUID."""

    wa_id: str | None = None
    bsuid: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.wa_id is None and self.bsuid is None


@dataclass(frozen=True)
class ButtonReply:
    """Clique em botao (de modelo ou interativo) unificado: so o payload importa."""

    payload: str
    text: str | None = None


@dataclass(frozen=True)
class InboundMessage:
    wa_message_id: str
    sender: Sender
    kind: ContentKind
    timestamp: int | None = None
    text: str | None = None  # so para kind == TEXT
    text_length: int | None = None  # caracteres (nao bytes)
    mime_type: str | None = None  # so para midia com mime
    button: ButtonReply | None = None  # so para kind == BUTTON
    raw_type: str = ""

    @property
    def is_media(self) -> bool:
        return self.kind.is_media


@dataclass(frozen=True)
class InboundStatus:
    wa_message_id: str
    status: str  # sent | delivered | read | failed | ...
    recipient_id: str | None = None
    timestamp: int | None = None
    error_code: int | None = None
    error_title: str | None = None

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"


@dataclass(frozen=True)
class ParsedValue:
    """Um `value` de `entry/changes` (field == messages) com seu destinatario."""

    phone_number_id: str | None
    messages: tuple[InboundMessage, ...] = ()
    statuses: tuple[InboundStatus, ...] = ()


@dataclass(frozen=True)
class ParsedPayload:
    values: tuple[ParsedValue, ...] = ()

    @property
    def messages(self) -> tuple[InboundMessage, ...]:
        return tuple(m for v in self.values for m in v.messages)

    @property
    def statuses(self) -> tuple[InboundStatus, ...]:
        return tuple(s for v in self.values for s in v.statuses)


@dataclass(frozen=True)
class RecipientSplit:
    accepted: ParsedPayload
    wrong_recipient_count: int  # numero de `value` descartados


def _as_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    # Limite de 18 digitos: int() acima de 4300 digitos levanta ValueError.
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 18:
        return int(value)
    return None


def _dicts(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _sub(obj: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    inner = obj.get(key)
    return inner if isinstance(inner, dict) else {}


def extract_sender(msg: Mapping[str, Any], contacts: list[Mapping[str, Any]]) -> Sender:
    """Unico ponto que sabe onde estao wa_id e BSUID (premissa Q5).

    wa_id: `messages[].from` (cai para `contacts[].wa_id`). BSUID: `messages[].from_user_id`
    (cai para `contacts[].user_id`). O contato so vale se casar com a mensagem pelo wa_id;
    se a mensagem nao tem wa_id, usa o unico contato presente.
    """
    wa_id = _as_str(msg.get("from"))
    bsuid = _as_str(msg.get("from_user_id"))
    contact = _matching_contact(wa_id, contacts)
    if contact is not None:
        wa_id = wa_id or _as_str(contact.get("wa_id"))
        bsuid = bsuid or _as_str(contact.get("user_id"))
    return Sender(wa_id=wa_id, bsuid=bsuid)


def _matching_contact(
    wa_id: str | None, contacts: list[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    if wa_id is not None:
        for contact in contacts:
            if _as_str(contact.get("wa_id")) == wa_id:
                return contact
    if len(contacts) == 1 and (wa_id is None or _as_str(contacts[0].get("wa_id")) is None):
        return contacts[0]
    return None


def _button_content(
    msg: Mapping[str, Any], msg_type: str
) -> tuple[ContentKind, ButtonReply | None]:
    if msg_type == "button":
        data = _sub(msg, "button")
        payload = _as_str(data.get("payload"))
        if payload is None:
            return ContentKind.UNSUPPORTED, None
        return ContentKind.BUTTON, ButtonReply(payload=payload, text=_as_str(data.get("text")))
    interactive = _sub(msg, "interactive")
    if interactive.get("type") == "button_reply":
        reply = _sub(interactive, "button_reply")
        payload = _as_str(reply.get("id"))
        if payload is None:
            return ContentKind.UNSUPPORTED, None
        return ContentKind.BUTTON, ButtonReply(payload=payload, text=_as_str(reply.get("title")))
    return ContentKind.INTERACTIVE, None


def _parse_message(
    msg: Mapping[str, Any], contacts: list[Mapping[str, Any]]
) -> InboundMessage | None:
    wa_message_id = _as_str(msg.get("id"))
    if wa_message_id is None:
        return None
    raw_type = msg.get("type")
    raw_type = raw_type if isinstance(raw_type, str) else ""
    kind: ContentKind
    button: ButtonReply | None = None
    if raw_type in ("button", "interactive"):
        kind, button = _button_content(msg, raw_type)
    else:
        kind = _KIND_BY_TYPE.get(raw_type, ContentKind.UNSUPPORTED)
    text: str | None = None
    text_length: int | None = None
    if kind is ContentKind.TEXT:
        body = _sub(msg, "text").get("body")
        text = body if isinstance(body, str) else ""
        text_length = len(text)
    mime_type = _as_str(_sub(msg, raw_type).get("mime_type")) if kind in _MIME_KINDS else None
    return InboundMessage(
        wa_message_id=wa_message_id,
        sender=extract_sender(msg, contacts),
        kind=kind,
        timestamp=_as_int(msg.get("timestamp")),
        text=text,
        text_length=text_length,
        mime_type=mime_type,
        button=button,
        raw_type=raw_type,
    )


def _parse_status(st: Mapping[str, Any]) -> InboundStatus | None:
    wa_message_id = _as_str(st.get("id"))
    status = _as_str(st.get("status"))
    if wa_message_id is None or status is None:
        return None
    errors = _dicts(st.get("errors"))
    first = errors[0] if errors else {}
    return InboundStatus(
        wa_message_id=wa_message_id,
        status=status,
        recipient_id=_as_str(st.get("recipient_id")),
        timestamp=_as_int(st.get("timestamp")),
        error_code=_as_int(first.get("code")),
        error_title=_as_str(first.get("title")),
    )


def _parse_value(value: Mapping[str, Any]) -> ParsedValue:
    contacts = _dicts(value.get("contacts"))
    messages = tuple(
        m for raw in _dicts(value.get("messages")) if (m := _parse_message(raw, contacts))
    )
    statuses = tuple(s for raw in _dicts(value.get("statuses")) if (s := _parse_status(raw)))
    return ParsedValue(
        phone_number_id=_as_str(_sub(value, "metadata").get("phone_number_id")),
        messages=messages,
        statuses=statuses,
    )


def parse_payload(obj: object) -> ParsedPayload:
    """Normaliza o JSON ja decodificado. Objeto diferente de whatsapp_business_account = vazio."""
    if not isinstance(obj, dict) or obj.get("object") != EXPECTED_OBJECT:
        return ParsedPayload()
    values: list[ParsedValue] = []
    for entry in _dicts(obj.get("entry")):
        for change in _dicts(entry.get("changes")):
            value = change.get("value")
            if change.get("field") == MESSAGES_FIELD and isinstance(value, dict):
                values.append(_parse_value(value))
    return ParsedPayload(values=tuple(values))


def split_by_recipient(parsed: ParsedPayload, phone_number_id: str) -> RecipientSplit:
    """Mantem so os `value` do nosso numero; os demais sao descartados por inteiro (AC-6)."""
    kept = tuple(v for v in parsed.values if v.phone_number_id == phone_number_id)
    return RecipientSplit(
        accepted=ParsedPayload(values=kept),
        wrong_recipient_count=len(parsed.values) - len(kept),
    )
