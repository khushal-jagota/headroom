"""Concise recap text shared by Ticket list projections."""

import re
from typing import Final

from planner.core.contracts import ErrorCode, PlannerError
from planner.core.ids import CLAIM_LEN, ID_PREFIXES, SLUG_ALPHABET, SLUG_LEN
from planner.list_reads.configuration import TICKET_RECAP_PREVIEW_CHARS

RECAP_MAX_CHARS: Final = 80

# Prefixes that new_id() builds from, plus the ones callers pass directly.
_ID_PREFIXES: Final = (
    *ID_PREFIXES.values(),
    "block",
    "chart",
    "credential",
    "dataset",
    "goal",
    "measure",
    "source",
    "source_run",
)
_SLUG: Final = f"[{SLUG_ALPHABET}]"
_PANELS_ID: Final = re.compile(
    rf"(?<!\w)(?:(?:{'|'.join(_ID_PREFIXES)})_{_SLUG}{{{SLUG_LEN}}}|claim_{_SLUG}{{{CLAIM_LEN}}})(?!\w)"
)
_UUID: Final = re.compile(
    r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])",
    re.IGNORECASE,
)
# A commit hash is 7 to 40 hex characters. Needing a digit and a letter leaves out
# plain words ("defaced") and plain numbers.
_HEX_RUN: Final = re.compile(r"(?<![0-9a-zA-Z])[0-9a-f]{7,40}(?![0-9a-zA-Z])", re.IGNORECASE)


def _find_id(text: str) -> str | None:
    for pattern in (_PANELS_ID, _UUID):
        match = pattern.search(text)
        if match:
            return match.group(0)
    for match in _HEX_RUN.finditer(text):
        run = match.group(0)
        if any(c.isdigit() for c in run) and any(c.isalpha() for c in run):
            return run
    return None


def validate_recap(recap: str) -> None:
    """Refuse a recap that is too long or names an internal ID. Empty clears it."""
    text = recap.strip()
    if len(text) > RECAP_MAX_CHARS:
        raise PlannerError(
            ErrorCode.validation,
            f"Recap is {len(text)} characters. Rewrite it in {RECAP_MAX_CHARS} or fewer.",
            {},
        )
    found = _find_id(text)
    if found is not None:
        raise PlannerError(
            ErrorCode.validation,
            f"Recap names an ID ({found}). Say what the work is in plain words and rewrite it.",
            {},
        )


def recap_preview(recap: str) -> str:
    compact = " ".join(recap.split())
    if len(compact) <= TICKET_RECAP_PREVIEW_CHARS:
        return compact
    return compact[: TICKET_RECAP_PREVIEW_CHARS - 1].rstrip() + "…"
