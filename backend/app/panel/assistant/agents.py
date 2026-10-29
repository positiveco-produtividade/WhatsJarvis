"""Agentes do assistente e escolha por marca (PN-51).

Funcoes puras, sem banco. Nomes e rotulos vem de ``app.panel.values``; as
imagens sao as de ``frontend/static/img/bots/`` (``bot-*.png`` e ``rosto-*.png``).
A pergunta cita exatamente uma marca = agente dela; nenhuma ou varias = Jarvis.
"""

import re
import unicodedata
from dataclasses import dataclass

from app.panel.values import (
    AGENT_BY_BRAND,
    BRAND_LABELS,
    COORDINATOR_NAME,
    agent_label,
)

__all__ = [
    "AGENTS",
    "COORDINATOR",
    "Agent",
    "agent_for_brand",
    "choose_agent",
    "detect_brands",
]

# Sufixo do arquivo de imagem de cada marca; o coordenador usa "positive".
_IMAGE_SLUG_BY_BRAND: dict[str, str] = {
    "jungle": "jungle",
    "plant_power": "plant",
    "possible": "possible",
    "a_tal_da_castanha": "atal",
    "zaya": "zaya",
}
_COORDINATOR_SLUG = "positive"

# Padroes aplicados ao texto ja sem acento, minusculo e sem "_" / "-".
_BRAND_PATTERNS: dict[str, re.Pattern[str]] = {
    "jungle": re.compile(r"\bjungle\b"),
    "plant_power": re.compile(r"\bplant\s*power\b"),
    "possible": re.compile(r"\bpossible\b"),
    "a_tal_da_castanha": re.compile(r"\bcastanha\b|\batal\b"),
    "zaya": re.compile(r"\bzaya\b"),
}


@dataclass(frozen=True)
class Agent:
    """Um agente do assistente. ``brand`` e None no coordenador."""

    name: str
    brand: str | None
    label: str
    bot_image: str
    face_image: str


def _build(name: str, brand: str | None, slug: str) -> Agent:
    return Agent(
        name=name,
        brand=brand,
        label=agent_label(brand),
        bot_image=f"bot-{slug}.png",
        face_image=f"rosto-{slug}.png",
    )


COORDINATOR: Agent = _build(COORDINATOR_NAME, None, _COORDINATOR_SLUG)

AGENTS: dict[str, Agent] = {
    brand: _build(AGENT_BY_BRAND[brand], brand, _IMAGE_SLUG_BY_BRAND[brand])
    for brand in BRAND_LABELS
}


def _normalize(text: str) -> str:
    """Minusculo, sem acento, com ``_`` e ``-`` como espaco."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return plain.lower().replace("_", " ").replace("-", " ")


def detect_brands(question: str) -> list[str]:
    """Chaves das marcas citadas na pergunta, na ordem de ``BRAND_LABELS``."""
    text = _normalize(question)
    return [brand for brand, pattern in _BRAND_PATTERNS.items() if pattern.search(text)]


def agent_for_brand(brand: str | None) -> Agent:
    """Agente da marca; marca ausente ou desconhecida = Jarvis."""
    return AGENTS.get(brand or "", COORDINATOR)


def choose_agent(question: str) -> Agent:
    """Exatamente uma marca citada = agente dela; nenhuma ou varias = Jarvis."""
    brands = detect_brands(question)
    if len(brands) == 1:
        return AGENTS[brands[0]]
    return COORDINATOR
