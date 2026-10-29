from pathlib import Path

import pytest

from app.panel.assistant import agents
from app.panel.assistant.agents import (
    AGENTS,
    COORDINATOR,
    agent_for_brand,
    choose_agent,
    detect_brands,
)
from app.panel.values import BRAND_LABELS

REPO = Path(__file__).resolve().parents[4]
_FORBIDDEN = "be" + "to"


@pytest.mark.parametrize(
    ("question", "name", "brand"),
    [
        ("Qual o preço da Jungle?", "Duda", "jungle"),
        ("argumentos da PLANT POWER", "Léo", "plant_power"),
        ("e a plantpower?", "Léo", "plant_power"),
        ("Plant-Power tem refil?", "Léo", "plant_power"),
        ("o que é a Possible", "Nico", "possible"),
        ("fale da castanha", "Rita", "a_tal_da_castanha"),
        ("produtos da atal", "Rita", "a_tal_da_castanha"),
        ("produtos da A Tal da Castanha", "Rita", "a_tal_da_castanha"),
        ("A TAL DA CASTANHA", "Rita", "a_tal_da_castanha"),
        ("preços da Zaya", "Tati", "zaya"),
        ("A Tal da Castanha tem glúten?", "Rita", "a_tal_da_castanha"),
    ],
)
def test_one_brand_picks_its_agent(question: str, name: str, brand: str) -> None:
    agent = choose_agent(question)
    assert agent.name == name
    assert agent.brand == brand


@pytest.mark.parametrize(
    "question",
    [
        "",
        "   ",
        "a tal coisa",
        "como está o dia?",
        "jungle e zaya, qual vende mais?",
        "castanha e possible",
    ],
)
def test_zero_or_many_brands_is_jarvis(question: str) -> None:
    assert choose_agent(question) is COORDINATOR
    assert COORDINATOR.name == "Jarvis"
    assert COORDINATOR.brand is None


def test_accented_input_is_tolerated() -> None:
    assert detect_brands("Cástánha") == ["a_tal_da_castanha"]


def test_no_false_positive_inside_words() -> None:
    assert detect_brands("para tal fim, atalho, impossible") == []


def test_agents_cover_every_brand_with_labels() -> None:
    assert set(AGENTS) == set(BRAND_LABELS)
    assert AGENTS["jungle"].label == "Duda · Jungle"
    assert COORDINATOR.label == "Jarvis"


def test_agent_for_brand() -> None:
    assert agent_for_brand("zaya").name == "Tati"
    assert agent_for_brand(None) is COORDINATOR
    assert agent_for_brand("inexistente") is COORDINATOR


def test_images_exist_in_frontend() -> None:
    img_dir = REPO / "frontend" / "static" / "img" / "bots"
    for agent in [COORDINATOR, *AGENTS.values()]:
        assert (img_dir / agent.bot_image).is_file(), agent.bot_image
        assert (img_dir / agent.face_image).is_file(), agent.face_image
    assert COORDINATOR.bot_image == "bot-positive.png"
    assert AGENTS["a_tal_da_castanha"].face_image == "rosto-atal.png"


def test_forbidden_name_absent() -> None:
    source = Path(agents.__file__).read_text(encoding="utf-8").lower()
    assert _FORBIDDEN not in source
    names = [a.name for a in [COORDINATOR, *AGENTS.values()]]
    assert all(_FORBIDDEN not in n.lower() for n in names)
