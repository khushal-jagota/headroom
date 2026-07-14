"""Recognition helpers for data written by the retired Ticket execution-route feature."""

from __future__ import annotations

from typing import Final

LEGACY_EXECUTION_ROUTE_FIELDS: Final = frozenset({"execution_route", "implementer"})
LEGACY_EXECUTION_ROUTE_VALUES: Final = frozenset(
    {"unassigned", "panels_worker", "hermes_codex", "hermes_claude"}
)


def redact_generated_execution_route_segment(text: str) -> str:
    """Remove only the route clause from the old generated worker-step prompt."""
    marker = "Execution route: "
    start = text.find(marker)
    if start < 0:
        return text
    end = text.find(". Stage owner: ", start + len(marker))
    if end < 0:
        return text
    route = text[start + len(marker) : end]
    if route not in LEGACY_EXECUTION_ROUTE_VALUES:
        return text
    return text[:start] + text[end + 2 :]
