"""Fabricas do cerebro (CB-T02). Dados sinteticos: nenhum dado licenciado nem PII real."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncpg
import httpx

from app.services.phone import normalize_phone

__all__ = [
    "BRAND_A",
    "BRAND_B",
    "GEMINI_BASE_URL",
    "FakeGemini",
    "create_active_user",
    "create_faq_items",
    "create_pending_trace",
    "gemini_text_response",
]

BRAND_A = "plant_power"
BRAND_B = "a_tal_da_castanha"
GEMINI_BASE_URL = "https://gemini.test"

_SYNTHETIC_FAQ = (
    ("PRODUTO", "Qual o sabor do produto exemplo A?", "Sabor sintetico A, para teste."),
    ("PRODUTO", "Quantas unidades vem na caixa do produto B?", "Doze unidades (sintetico)."),
    ("ARGUMENTO", "Como apresentar o produto exemplo A ao comprador?", "Argumento sintetico A."),
    ("MATERIAL", "Onde encontro o material de apoio exemplo?", "No portal de teste (sintetico)."),
    ("CAMPANHA", "Qual a campanha vigente de exemplo?", "Campanha sintetica de teste."),
)


async def create_active_user(
    conn: asyncpg.Connection,
    now: datetime,
    *,
    seq: int = 1,
    brands: list[str] | None = None,
    status: str = "active",
) -> int:
    """Insere um usuario (ativo, com aceite) em `access.users`."""
    phone = normalize_phone(f"+55119100{seq:05d}")
    phone_key = phone.match_key
    active = status == "active"
    return int(
        await conn.fetchval(
            "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, ufs, "
            "channels, brands, manager_name, status, status_changed_at, accepted_at, "
            "accepted_notice_version, created_at, created_by, updated_at) "
            "VALUES ($1, $2, $3, 'vendedor', 'Empresa Teste', '{SP}', '{farma}', $4, "
            "'Gestora Teste', $5, $6, $7, $8, $6, 'teste', $6) RETURNING id",
            f"Vendedor Cerebro {seq}",
            phone.phone_e164,
            phone_key,
            brands if brands is not None else [BRAND_A, BRAND_B],
            status,
            now,
            now if active else None,
            "v1" if active else None,
        )
    )


async def create_pending_trace(
    conn: asyncpg.Connection,
    user_id: int,
    now: datetime,
    *,
    text: str = "Qual o sabor do produto exemplo A?",
    wa_message_id: str = "wamid.BRAIN1",
) -> int:
    """Rastro de mensagem de texto de usuario ativo, ainda `pending` (aguarda resposta)."""
    return int(
        await conn.fetchval(
            "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
            "access_decision, decision, reply_kind, body) "
            "VALUES ($1, $2, 'text', $3, 'allowed', 'allowed', 'pending', $4) RETURNING id",
            wa_message_id,
            now,
            user_id,
            text,
        )
    )


async def create_faq_items(
    conn: asyncpg.Connection, now: datetime, *, brand: str = BRAND_A
) -> tuple[int, ...]:
    """Itens de FAQ sinteticos ativos (`bot_responde`) para a marca."""
    ids: list[int] = []
    for category, question, answer in _SYNTHETIC_FAQ:
        key = " ".join(question.lower().split())
        item_id = await conn.fetchval(
            "INSERT INTO content.faq_items (brand, category, question, question_key, "
            "answer, bot_responde, origin, created_at, created_by, updated_at, updated_by) "
            "VALUES ($1, $2, $3, $4, $5, true, 'seed', $6, 'teste', $6, 'teste') RETURNING id",
            brand,
            category,
            question,
            key,
            answer,
            now,
        )
        ids.append(int(item_id))
    return tuple(ids)


def gemini_text_response(
    text: str, *, prompt_tokens: int = 100, output_tokens: int = 20
) -> dict[str, Any]:
    """Corpo minimo de resposta generateContent com uso de tokens."""
    return {
        "candidates": [
            {"content": {"parts": [{"text": text}], "role": "model"}, "finishReason": "STOP"}
        ],
        "usageMetadata": {
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": output_tokens,
            "totalTokenCount": prompt_tokens + output_tokens,
        },
    }


@dataclass
class FakeGemini:
    """Transporte falso do Gemini (`httpx.MockTransport`): respostas em fila, chamadas gravadas."""

    responses: list[httpx.Response] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.transport = httpx.MockTransport(self._handle)

    def queue_text(self, text: str, *, prompt_tokens: int = 100, output_tokens: int = 20) -> None:
        body = gemini_text_response(text, prompt_tokens=prompt_tokens, output_tokens=output_tokens)
        self.responses.append(httpx.Response(200, json=body))

    def queue_status(self, status: int, body: dict[str, Any] | None = None) -> None:
        self.responses.append(httpx.Response(status, json=body or {"error": {"code": status}}))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            return httpx.Response(500, json={"error": "sem resposta enfileirada"})
        return self.responses.pop(0)

    def last_json(self) -> Any:
        return json.loads(self.requests[-1].content)
