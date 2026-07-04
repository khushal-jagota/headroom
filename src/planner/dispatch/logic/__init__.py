"""Pure dispatch rules (§7.2, §7.3 arithmetic, §7.5). Stdlib + contracts only —
no sqlite3, no FastAPI, no planner.core.db/events imports anywhere in this package.
No logic module imports a sibling logic module; this facade is the only re-export point."""

from planner.dispatch.logic.breaker import next_breaker_state
from planner.dispatch.logic.claims import expiry_at, has_active_claim, is_expired
from planner.dispatch.logic.eligibility import gating_field_pending, is_eligible
from planner.dispatch.logic.ordering import ordering_key

__all__ = [
    "expiry_at",
    "gating_field_pending",
    "has_active_claim",
    "is_eligible",
    "is_expired",
    "next_breaker_state",
    "ordering_key",
]
