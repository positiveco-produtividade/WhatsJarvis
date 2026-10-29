"""Valores de exibicao do painel (rotulos PT-BR, agentes, marcas, status).

As chaves vem de ``app.core.values`` (dono: porta-fechada.1) e dos CHECKs de
``chat.tickets`` (migration 005); aqui so ha os rotulos. Os testes garantem que
nenhuma chave some nem sobra em relacao a essas fontes.
"""

from app.core.values import BRANDS, CHANNELS, PROFILES, UFS

__all__ = [
    "AGENT_BY_BRAND",
    "ALL_BRANDS_LABEL",
    "BRANDS",
    "BRAND_LABELS",
    "CHANNELS",
    "CHANNEL_LABELS",
    "CLASSIFICATIONS",
    "CLASSIFICATION_LABELS",
    "COORDINATOR_NAME",
    "PROFILES",
    "PROFILE_LABELS",
    "TICKET_STATUSES",
    "TICKET_STATUS_LABELS",
    "UFS",
    "URGENCIES",
    "URGENCY_LABELS",
    "agent_label",
    "agent_name",
    "brand_label",
    "channel_label",
    "classification_label",
    "profile_label",
    "ticket_status_label",
    "urgency_label",
]

ALL_BRANDS_LABEL = "Todas as marcas"
COORDINATOR_NAME = "Jarvis"

BRAND_LABELS: dict[str, str] = {
    "jungle": "Jungle",
    "plant_power": "Plant Power",
    "possible": "Possible",
    "a_tal_da_castanha": "A Tal da Castanha",
    "zaya": "Zaya",
}

# Agente de cada marca (prototipo, BOTS). Sem marca ou varias marcas = Jarvis.
AGENT_BY_BRAND: dict[str, str] = {
    "jungle": "Duda",
    "plant_power": "Léo",
    "possible": "Nico",
    "a_tal_da_castanha": "Rita",
    "zaya": "Tati",
}

PROFILE_LABELS: dict[str, str] = {
    "vendedor": "Vendedor",
    "gestor_comercial": "Gestor",
    "executivo": "Executivo",
    "parceiro_externo": "Parceiro",
    "administrador": "Administrador",
}

CHANNEL_LABELS: dict[str, str] = {
    "atacarejo": "Atacarejo",
    "supermercado": "Supermercado",
    "farma": "Farma",
    "conveniencia": "Conveniência",
    "food_service": "Food service",
    "e_commerce": "E-commerce",
}

# Valores do CHECK de chat.tickets.status (migration 005) e rotulos do prototipo.
TICKET_STATUSES: tuple[str, ...] = ("answered_bot", "answered_human", "awaiting", "send_error")
TICKET_STATUS_LABELS: dict[str, str] = {
    "answered_bot": "Respondido pelo bot",
    "answered_human": "Respondido por humano",
    "awaiting": "Aguardando atendimento",
    "send_error": "Erro no envio",
}

# Valores do CHECK de chat.tickets.classification (migration 005).
CLASSIFICATIONS: tuple[str, ...] = (
    "PRODUTO",
    "PRECO",
    "ARGUMENTO",
    "MATERIAL",
    "CAMPANHA",
    "MIDIA",
    "NAO_IDENTIFICADO",
)
CLASSIFICATION_LABELS: dict[str, str] = {
    "PRODUTO": "Produto",
    "PRECO": "Preço",
    "ARGUMENTO": "Argumento",
    "MATERIAL": "Material",
    "CAMPANHA": "Campanha",
    "MIDIA": "Mídia",
    "NAO_IDENTIFICADO": "Sem classificação",
}

# Valores do CHECK de chat.tickets.urgency (migration 005).
URGENCIES: tuple[str, ...] = ("muito_urgente", "urgente", "pouco_urgente")
URGENCY_LABELS: dict[str, str] = {
    "muito_urgente": "Muito urgente",
    "urgente": "Urgente",
    "pouco_urgente": "Pouco urgente",
}


def _label(table: dict[str, str], key: str | None) -> str:
    """Rotulo da chave; chave desconhecida volta como esta, ausente vira traco."""
    if not key:
        return "—"
    return table.get(key, key)


def brand_label(brand: str | None) -> str:
    return _label(BRAND_LABELS, brand)


def profile_label(profile: str | None) -> str:
    return _label(PROFILE_LABELS, profile)


def channel_label(channel: str | None) -> str:
    return _label(CHANNEL_LABELS, channel)


def ticket_status_label(status: str | None) -> str:
    return _label(TICKET_STATUS_LABELS, status)


def classification_label(classification: str | None) -> str:
    return _label(CLASSIFICATION_LABELS, classification)


def urgency_label(urgency: str | None) -> str:
    return _label(URGENCY_LABELS, urgency)


def agent_name(brand: str | None) -> str:
    """Nome do agente da marca; marca ausente ou desconhecida = Jarvis."""
    return AGENT_BY_BRAND.get(brand or "", COORDINATOR_NAME)


def agent_label(brand: str | None) -> str:
    """Como o prototipo mostra: ``Duda · Jungle``; coordenador: ``Jarvis``."""
    if brand in AGENT_BY_BRAND:
        return f"{AGENT_BY_BRAND[brand]} · {BRAND_LABELS[brand]}"
    return COORDINATOR_NAME
