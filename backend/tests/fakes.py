"""Dubles de teste compartilhados (dono: PF-09). Tarefas seguintes so importam daqui."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import SecretStr

from app.core.security import verify_hub_signature
from app.services.channel import ReplyButton, SendResult, TemplateMessage

FailureKind = Literal["transient", "ambiguous", "definitive"]
Method = Literal["send_text", "send_template", "send_reply_buttons"]


@dataclass(frozen=True)
class SentCall:
    """Uma chamada registrada, na ordem em que aconteceu."""

    method: Method
    to: str
    body: str | None = None
    template: TemplateMessage | None = None
    buttons: tuple[ReplyButton, ...] = ()


def failure_result(kind: FailureKind, code: str = "fake_error") -> SendResult:
    if kind == "transient":
        return SendResult(ok=False, error_code=code, transient=True)
    if kind == "ambiguous":
        return SendResult(ok=False, error_code=code, ambiguous=True)
    return SendResult(ok=False, error_code=code)


class FakeChannel:
    """`Channel` em memoria: registra chamadas em ordem e falha sob comando.

    `fail_next(kind, count=1, method=None)` enfileira falhas; sem `method`, vale para
    qualquer metodo de envio. `fail_always(kind)` falha ate `clear_failures()`.
    """

    def __init__(self, app_secret: str | SecretStr = "fake-app-secret") -> None:
        self.calls: list[SentCall] = []
        self._app_secret = app_secret
        self._queued: list[tuple[Method | None, SendResult]] = []
        self._always: SendResult | None = None
        self._counter = 0

    def fail_next(
        self,
        kind: FailureKind,
        *,
        count: int = 1,
        method: Method | None = None,
        code: str = "fake_error",
    ) -> None:
        for _ in range(count):
            self._queued.append((method, failure_result(kind, code)))

    def fail_always(self, kind: FailureKind, code: str = "fake_error") -> None:
        self._always = failure_result(kind, code)

    def clear_failures(self) -> None:
        self._queued.clear()
        self._always = None

    @property
    def methods(self) -> list[str]:
        return [c.method for c in self.calls]

    def _result_for(self, method: Method) -> SendResult:
        for index, (wanted, result) in enumerate(self._queued):
            if wanted is None or wanted == method:
                del self._queued[index]
                return result
        if self._always is not None:
            return self._always
        self._counter += 1
        return SendResult(ok=True, wa_message_id=f"wamid.FAKE{self._counter}")

    async def send_text(self, to: str, body: str) -> SendResult:
        self.calls.append(SentCall("send_text", to, body=body))
        return self._result_for("send_text")

    async def send_template(self, to: str, template: TemplateMessage) -> SendResult:
        self.calls.append(SentCall("send_template", to, template=template))
        return self._result_for("send_template")

    async def send_reply_buttons(
        self, to: str, body: str, buttons: Sequence[ReplyButton]
    ) -> SendResult:
        self.calls.append(SentCall("send_reply_buttons", to, body=body, buttons=tuple(buttons)))
        return self._result_for("send_reply_buttons")

    def verify_signature(self, raw_body: bytes, header: str) -> bool:
        return verify_hub_signature(raw_body, header, self._app_secret)
