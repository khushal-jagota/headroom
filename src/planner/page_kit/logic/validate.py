"""Turn a proposal body into stored text.

Prose is stored unchanged. A page is the same document a goal page uses: the
shared check accepts it, fills in any missing identities, and stores one
canonical JSON value. A page that leaves the kit is refused. Sorting, row
selection, and which view is open are not fields on the document, so they
cannot be stored from here.
"""

from __future__ import annotations

import json
from typing import Any

from planner.core.contracts import ErrorCode, PlannerError
from planner.core.ids import new_id
from planner.goals.logic.documents import prepare_document

PAGE_KIND = "headroom.page"


def page_intent(body: str) -> str:
    """``page``, ``invalid``, or ``prose``.

    Invalid means the text claims to be a page and is not readable JSON.
    Any other non-page text, including a JSON object of another kind, is prose.
    """
    if not body.lstrip().startswith("{"):
        return "prose"
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        if PAGE_KIND in body[:2000]:
            return "invalid"
        return "prose"
    if isinstance(value, dict) and value.get("kind") == PAGE_KIND:
        return "page"
    return "prose"


def canonical_page(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def prepare_stored_text(body: str, what: str) -> str:
    """The text to store for a proposal or a settled field.

    Prose comes back unchanged, including its whitespace. A page comes back as
    the canonical document. A page that fails the kit raises ``PlannerError``.
    """
    if not body:
        raise PlannerError(ErrorCode.validation, f"{what} must be non-empty")
    intent = page_intent(body)
    if intent == "prose":
        return body
    if intent == "invalid":
        raise PlannerError(
            ErrorCode.validation,
            f"{what} looks like a proposal page but is not valid JSON",
        )
    try:
        document = prepare_document(json.loads(body), lambda: new_id("block"))
    except ValueError as exc:
        message = str(exc)
        path, separator, reason = message.partition(": ")
        if not separator:
            path, reason = "$", message
        raise PlannerError(
            ErrorCode.validation,
            f"{what} is outside the page kit: {message}",
            {"path": path, "reason": reason},
        ) from exc
    return canonical_page(dict(document))
