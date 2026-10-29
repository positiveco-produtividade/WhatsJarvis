"""PN-T04: confere hashes dos arquivos vendor e as imagens dos agentes."""

import base64
import hashlib
from pathlib import Path

import pytest

FRONTEND_STATIC = Path(__file__).resolve().parents[4] / "frontend" / "static"
VENDOR_DIR = FRONTEND_STATIC / "vendor"
BOTS_DIR = FRONTEND_STATIC / "img" / "bots"

VENDOR_FILES = ("htmx", "alpine-csp", "chart", "marked", "purify")
FONT_FILES = ("inter-latin.woff2", "inter-latin-ext.woff2")
WOFF2_MAGIC = b"wOF2"
AGENTS = ("positive", "jungle", "plant", "possible", "atal", "zaya")
BOT_FILES = [f"{kind}-{agent}.png" for kind in ("bot", "rosto") for agent in AGENTS]

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# As imagens originais tem ~3 a 7 KB; 50 KB deixa folga sem permitir peso indevido.
MAX_IMAGE_BYTES = 50 * 1024


def _sha384_b64(path: Path) -> str:
    return base64.b64encode(hashlib.sha384(path.read_bytes()).digest()).decode()


def _parse_manifest() -> dict[str, str]:
    """Le a tabela principal do VENDOR.md: arquivo -> sha384."""
    manifest: dict[str, str] = {}
    for line in (VENDOR_DIR / "VENDOR.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and cells[0].endswith((".min.js", ".woff2")):
            manifest[cells[0]] = cells[4]
    return manifest


def test_manifest_lists_every_vendor_file() -> None:
    assert set(_parse_manifest()) == {f"{n}.min.js" for n in VENDOR_FILES} | set(FONT_FILES)


@pytest.mark.parametrize("name", VENDOR_FILES)
def test_vendor_hash_matches_manifest(name: str) -> None:
    file_name = f"{name}.min.js"
    path = VENDOR_DIR / file_name
    assert path.is_file()
    assert _sha384_b64(path) == _parse_manifest()[file_name]


@pytest.mark.parametrize("file_name", BOT_FILES)
def test_bot_image_is_valid_png_under_cap(file_name: str) -> None:
    path = BOTS_DIR / file_name
    assert path.is_file()
    data = path.read_bytes()
    assert data.startswith(PNG_MAGIC)
    assert len(data) <= MAX_IMAGE_BYTES


@pytest.mark.parametrize("file_name", FONT_FILES)
def test_font_hash_matches_manifest(file_name: str) -> None:
    path = VENDOR_DIR / file_name
    assert path.read_bytes().startswith(WOFF2_MAGIC)
    assert _sha384_b64(path) == _parse_manifest()[file_name]
