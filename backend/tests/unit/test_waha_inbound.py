"""Parser dos eventos do WAHA (spec canal-waha.1, WA-5, WA-6, WA-7, WA-18, WA-21; WA-T07)."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.services import waha_inbound as wi
from app.services.inbound import ContentKind, Sender

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "waha"
SESSION = "jarvis"
JARVIS = "+5500000000001"
SELLER = "5500000000002"


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def parse(body: Any, *, jarvis_number: str | None = JARVIS) -> wi.WahaEvent:
    return wi.parse_event(body, session=SESSION, jarvis_number=jarvis_number)


def message_of(event: wi.WahaEvent) -> wi.InboundMessage:
    assert isinstance(event, wi.WahaMessage), event
    return event.message


# ---------------------------------------------------------------- texto e remetente


def test_texto_de_contato_individual() -> None:
    msg = message_of(parse(load("message_text.json")))
    assert msg.kind is ContentKind.TEXT
    assert msg.text == "Quais sao os sabores?" and msg.text_length == len(msg.text)
    assert msg.sender == Sender(wa_id=SELLER)
    assert msg.wa_message_id == f"false_{SELLER}@c.us_AAAAAAAAAAAAAAAAAAA1"
    assert msg.timestamp == 1763049600
    assert msg.button is None and msg.raw_type == "waha:message"


def test_remetente_s_whatsapp_net_tambem_e_individual() -> None:
    body = load("message_text.json")
    body["payload"]["from"] = f"{SELLER}@s.whatsapp.net"
    assert message_of(parse(body)).sender == Sender(wa_id=SELLER)


def test_numero_oculto_vai_no_lugar_do_bsuid() -> None:
    msg = message_of(parse(load("message_lid.json")))
    assert msg.sender == Sender(bsuid="100000000000001@lid")
    assert msg.sender.wa_id is None and msg.kind is ContentKind.TEXT


# ---------------------------------------------------------------- filtros (WA-6)


@pytest.mark.parametrize(
    ("fixture", "reason"),
    [
        ("message_group.json", wi.GROUP),
        ("message_from_me.json", wi.FROM_ME),
        ("message_status_broadcast.json", wi.STATUS_BROADCAST),
        ("message_newsletter.json", wi.NEWSLETTER),
    ],
)
def test_filtros_descartam(fixture: str, reason: str) -> None:
    assert parse(load(fixture)) == wi.WahaIgnored(reason)


def test_lista_de_transmissao_descartada() -> None:
    body = load("message_text.json")
    body["payload"]["from"] = "120363000000000002@broadcast"
    assert parse(body) == wi.WahaIgnored(wi.BROADCAST)


@pytest.mark.parametrize("jid", ["abc@c.us", "5500@x.y", "semarroba", ""])
def test_remetente_estranho_descartado(jid: str) -> None:
    body = load("message_text.json")
    body["payload"]["from"] = jid
    assert isinstance(parse(body), wi.WahaIgnored)


# ---------------------------------------------------------------- sessao (WA-5)


def test_sessao_diferente_descartada() -> None:
    body = load("message_text.json")
    body["session"] = "default"
    assert parse(body) == wi.WahaIgnored(wi.WRONG_RECIPIENT)


def test_me_diferente_do_numero_do_jarvis_descartado() -> None:
    body = load("message_text.json")
    body["me"]["id"] = "5599999999999@c.us"
    assert parse(body) == wi.WahaIgnored(wi.WRONG_RECIPIENT)


def test_sem_me_vale_so_a_sessao() -> None:
    body = load("message_text.json")
    del body["me"]
    assert isinstance(parse(body), wi.WahaMessage)
    assert isinstance(parse(load("message_text.json"), jarvis_number=None), wi.WahaMessage)


# ---------------------------------------------------------------- midia e outros tipos (WA-7)


def test_midia_sem_download() -> None:
    msg = message_of(parse(load("message_media.json")))
    assert msg.kind is ContentKind.AUDIO and msg.is_media
    assert msg.mime_type == "audio/ogg; codecs=opus" and msg.text is None


@pytest.mark.parametrize(
    ("mime", "kind"),
    [
        ("image/jpeg", ContentKind.IMAGE),
        ("image/webp", ContentKind.STICKER),
        ("video/mp4", ContentKind.VIDEO),
        ("application/pdf", ContentKind.DOCUMENT),
        (None, ContentKind.DOCUMENT),
    ],
)
def test_tipo_da_midia_pelo_mimetype(mime: str | None, kind: ContentKind) -> None:
    body = load("message_media.json")
    body["payload"]["media"] = None if mime is None else {"mimetype": mime}
    msg = message_of(parse(body))
    assert msg.kind is kind and msg.is_media


def test_localizacao_e_contato() -> None:
    body = load("message_text.json")
    body["payload"].update(body="", location={"latitude": 1.0, "longitude": 2.0})
    assert message_of(parse(body)).kind is ContentKind.LOCATION
    body = load("message_text.json")
    body["payload"].update(body="", vCards=["BEGIN:VCARD"])
    assert message_of(parse(body)).kind is ContentKind.CONTACTS


@pytest.mark.parametrize("text", ["", "   ", None])
def test_mensagem_sem_texto_nem_midia_e_nao_suportada(text: str | None) -> None:
    body = load("message_text.json")
    body["payload"]["body"] = text
    assert message_of(parse(body)).kind is ContentKind.UNSUPPORTED


def test_reacao_e_reacao_removida() -> None:
    for fixture in ("message_reaction.json", "message_reaction_removed.json"):
        msg = message_of(parse(load(fixture)))
        assert msg.kind is ContentKind.REACTION and msg.sender == Sender(wa_id=SELLER)
        assert msg.text is None and msg.raw_type == "waha:message.reaction"


def test_reacao_em_grupo_descartada() -> None:
    body = load("message_reaction.json")
    body["payload"]["from"] = "120363000000000000@g.us"
    assert parse(body) == wi.WahaIgnored(wi.GROUP)


# ---------------------------------------------------------------- status de entrega (WA-18)


@pytest.mark.parametrize(
    ("fixture", "status"),
    [
        ("ack_error.json", "failed"),
        ("ack_server.json", "sent"),
        ("ack_device.json", "delivered"),
        ("ack_read.json", "read"),
    ],
)
def test_ack_vira_status(fixture: str, status: str) -> None:
    event = parse(load(fixture))
    assert isinstance(event, wi.WahaStatus)
    assert event.status.status == status
    assert event.status.wa_message_id.startswith(f"true_{SELLER}@c.us_")
    assert event.status.recipient_id == SELLER
    assert (event.status.error_title == "waha_ack_error") is (status == "failed")


def test_ack_played_vale_como_lida() -> None:
    body = load("ack_read.json")
    body["payload"]["ack"] = 4
    event = parse(body)
    assert isinstance(event, wi.WahaStatus) and event.status.status == "read"


@pytest.mark.parametrize("ack", [0, 9, "2", None])
def test_ack_pendente_ou_desconhecido_ignorado(ack: Any) -> None:
    body = load("ack_pending.json")
    body["payload"]["ack"] = ack
    assert parse(body) == wi.WahaIgnored(wi.ACK_IGNORED)


def test_ack_de_mensagem_recebida_ignorado() -> None:
    body = load("ack_read.json")
    body["payload"]["fromMe"] = False
    assert parse(body) == wi.WahaIgnored(wi.ACK_IGNORED)


# ---------------------------------------------------------------- enquete (WA-21)


def test_voto_vira_texto_com_a_marca() -> None:
    msg = message_of(parse(load("poll_vote.json")))
    assert msg.kind is ContentKind.TEXT and msg.text == "Jungle"
    assert msg.sender == Sender(wa_id=SELLER)
    assert msg.button is not None
    assert msg.button.payload == f"{wi.POLL_VOTE_PREFIX}true_{SELLER}@c.us_CCCCCCCCCCCCCCCCCCC1"
    assert msg.raw_type == "waha:poll.vote"


def test_troca_de_voto_tem_id_novo_e_reentrega_mesmo_id() -> None:
    first = message_of(parse(load("poll_vote.json")))
    again = message_of(parse(load("poll_vote.json")))
    changed = load("poll_vote.json")
    changed["payload"]["vote"].update(selectedOptions=["Possible"], timestamp=1763049700)
    second = message_of(parse(changed))
    assert first.wa_message_id == again.wa_message_id
    assert second.wa_message_id != first.wa_message_id and second.text == "Possible"


@pytest.mark.parametrize(
    ("options", "reason"),
    [([], wi.POLL_VOTE_EMPTY), (["", " "], wi.POLL_VOTE_EMPTY),
     (["Jungle", "Zaya"], wi.POLL_VOTE_MULTIPLE)],
)  # fmt: skip
def test_voto_vazio_ou_multiplo_descartado(options: list[str], reason: str) -> None:
    body = load("poll_vote.json")
    body["payload"]["vote"]["selectedOptions"] = options
    assert parse(body) == wi.WahaIgnored(reason)


def test_voto_do_proprio_numero_descartado() -> None:
    body = load("poll_vote.json")
    body["payload"]["vote"]["fromMe"] = True
    assert parse(body) == wi.WahaIgnored(wi.FROM_ME)


# ---------------------------------------------------------------- sessao do WAHA (WA-19)


@pytest.mark.parametrize(
    ("fixture", "state"),
    [
        ("session_status_working.json", "WORKING"),
        ("session_status_failed.json", "FAILED"),
        ("session_status_scan_qr.json", "SCAN_QR_CODE"),
    ],
)
def test_estado_da_sessao(fixture: str, state: str) -> None:
    assert parse(load(fixture)) == wi.WahaSession(state)


# ---------------------------------------------------------------- robustez


def test_evento_desconhecido() -> None:
    body = load("message_text.json")
    body["event"] = "presence.update"
    assert parse(body) == wi.WahaIgnored(wi.UNSUPPORTED_EVENT)


@pytest.mark.parametrize("body", [None, [], "texto", 42, {}, {"session": SESSION}])
def test_corpo_invalido_nunca_levanta(body: Any) -> None:
    assert isinstance(parse(body), wi.WahaIgnored)


def _mutations() -> list[dict[str, Any]]:
    """Cada fixture com cada campo do payload trocado por lixo."""
    out: list[dict[str, Any]] = []
    for path in sorted(FIXTURES.glob("*.json")):
        base = json.loads(path.read_text(encoding="utf-8"))
        for key in list(base.get("payload", {})):
            for junk in (None, 0, "", [], {"x": 1}, True):
                body = copy.deepcopy(base)
                body["payload"][key] = junk
                out.append(body)
        broken = copy.deepcopy(base)
        broken["payload"] = "nao-e-objeto"
        out.append(broken)
    return out


def test_payload_malformado_nunca_levanta() -> None:
    for body in _mutations():
        assert isinstance(
            parse(body), wi.WahaMessage | wi.WahaStatus | wi.WahaSession | wi.WahaIgnored
        )
