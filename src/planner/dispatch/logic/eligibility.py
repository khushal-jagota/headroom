"""§7.2 dispatch eligibility over a pure DispatchCandidate, plus the gating-field
pending check the data layer feeds it. Stdlib + contracts only; logic never touches
the DB."""

from __future__ import annotations

from typing import Final

from planner.core.contracts import JsonDict
from planner.dispatch.contracts import DispatchCandidate
from planner.tickets.contracts import (
    ADVANCE_TARGET,
    GATING_FIELD,
    STATE_ORDER,
    AtCap,
    TicketState,
)

# Position of each state in the linear pipeline; drives the "<= ceiling" comparison.
_ORDER_INDEX: Final[dict[TicketState, int]] = {s: i for i, s in enumerate(STATE_ORDER)}


def gating_field_pending(state: TicketState, fields: JsonDict) -> bool:
    """True iff the state's gating field carries a pending proposal in the parsed
    tickets.fields JSON. States without a gating field (needs_review, done, dropped)
    return False."""
    name = GATING_FIELD.get(state)
    if name is None:
        return False
    slot = fields.get(name.value)
    if not isinstance(slot, dict):
        return False
    return slot.get("proposal") is not None


def is_eligible(candidate: DispatchCandidate) -> bool:
    """§7.2 (SPEC line 149): the dispatch-eligibility conjunction plus the two
    advance branches. Checks run in the order below; the terminal-state guard also
    guarantees ADVANCE_TARGET[state] below never KeyErrors (the four remaining states
    are exactly its keys)."""
    if candidate.state in (
        TicketState.done,
        TicketState.dropped,
        TicketState.needs_review,
    ):
        return False
    if candidate.is_blocked:            # §3.6 line 57
        return False
    if candidate.auto_blocked:          # §7.5
        return False
    if candidate.has_active_claim:
        return False
    if candidate.gating_pending:        # current gating field has a pending proposal
        return False
    target = ADVANCE_TARGET[candidate.state]
    if _ORDER_INDEX[target] <= _ORDER_INDEX[candidate.ceiling]:
        return True                     # branch (a): the agent can move it
    # branch (b): at ceiling + propose eligible; at ceiling + stop and past-ceiling not.
    return candidate.state == candidate.ceiling and candidate.at_cap is AtCap.propose
