from app.core import values as core
from app.panel import values as pv


def test_reexports_core_values_without_copy() -> None:
    assert pv.BRANDS is core.BRANDS
    assert pv.PROFILES is core.PROFILES
    assert pv.CHANNELS is core.CHANNELS
    assert pv.UFS is core.UFS


def test_label_tables_cover_exactly_the_core_keys() -> None:
    assert set(pv.BRAND_LABELS) == set(core.BRANDS)
    assert set(pv.AGENT_BY_BRAND) == set(core.BRANDS)
    assert set(pv.PROFILE_LABELS) == set(core.PROFILES)
    assert set(pv.CHANNEL_LABELS) == set(core.CHANNELS)
    assert set(pv.TICKET_STATUS_LABELS) == set(pv.TICKET_STATUSES)
    assert set(pv.CLASSIFICATION_LABELS) == set(pv.CLASSIFICATIONS)
    assert set(pv.URGENCY_LABELS) == set(pv.URGENCIES)


def test_agents_per_brand() -> None:
    assert pv.AGENT_BY_BRAND == {
        "jungle": "Duda",
        "plant_power": "Léo",
        "possible": "Nico",
        "a_tal_da_castanha": "Rita",
        "zaya": "Tati",
    }
    assert pv.COORDINATOR_NAME == "Jarvis"


def test_agent_label_and_fallback_to_jarvis() -> None:
    assert pv.agent_label("jungle") == "Duda · Jungle"
    assert pv.agent_label("a_tal_da_castanha") == "Rita · A Tal da Castanha"
    assert pv.agent_label(None) == "Jarvis"
    assert pv.agent_label("outra") == "Jarvis"
    assert pv.agent_name("zaya") == "Tati"
    assert pv.agent_name(None) == "Jarvis"


def test_labels_ptbr() -> None:
    assert pv.brand_label("plant_power") == "Plant Power"
    assert pv.profile_label("gestor_comercial") == "Gestor"
    assert pv.channel_label("conveniencia") == "Conveniência"
    assert pv.ticket_status_label("answered_human") == "Respondido por humano"
    assert pv.ticket_status_label("awaiting") == "Aguardando atendimento"
    assert pv.classification_label("PRECO") == "Preço"
    assert pv.classification_label("NAO_IDENTIFICADO") == "Sem classificação"
    assert pv.urgency_label("muito_urgente") == "Muito urgente"
    assert pv.ALL_BRANDS_LABEL == "Todas as marcas"


def test_unknown_and_empty_keys_do_not_break() -> None:
    assert pv.brand_label(None) == "—"
    assert pv.brand_label("") == "—"
    assert pv.brand_label("nova_marca") == "nova_marca"


def test_no_beto() -> None:
    text = " ".join([*pv.AGENT_BY_BRAND.values(), pv.COORDINATOR_NAME])
    assert "Beto" not in text
