"""Id generation: prefixed random slugs for entities, calendar day ids, and
claim tokens (distinct from run ids so §7.6 validation checks two values)."""

from __future__ import annotations

import secrets
from datetime import date
from typing import Final

ID_PREFIXES: Final[dict[str, str]] = {
    "sprint": "sp",
    "sprint_item": "si",
    "ticket": "t",
    "idea": "idea",
    "run": "run",
}
SLUG_ALPHABET: Final = "0123456789abcdefghjkmnpqrstuvwxyz"  # lowercase, no i/l/o
SLUG_LEN: Final = 8
CLAIM_LEN: Final = 12  # claim tokens are longer than entity slugs


def _slug(length: int) -> str:
    return "".join(secrets.choice(SLUG_ALPHABET) for _ in range(length))


def new_id(prefix: str) -> str:
    return f"{prefix}_{_slug(SLUG_LEN)}"


def day_id(planning_date: date) -> str:
    return f"day_{planning_date.isoformat()}"


def new_claim() -> str:
    return f"claim_{_slug(CLAIM_LEN)}"
