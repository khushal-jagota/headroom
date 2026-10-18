"""Concise recap text shared by Ticket list projections."""

from planner.list_reads.configuration import TICKET_RECAP_PREVIEW_CHARS


def recap_preview(recap: str) -> str:
    compact = " ".join(recap.split())
    if len(compact) <= TICKET_RECAP_PREVIEW_CHARS:
        return compact
    return compact[: TICKET_RECAP_PREVIEW_CHARS - 1].rstrip() + "…"
