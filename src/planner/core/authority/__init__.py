"""One rule about who may call what: you may act on anything strictly below you."""

from planner.core.authority.contracts import (
    ANY_ID,
    Target,
    TargetKind,
    outcome,
    owner_only,
    plan,
    ticket,
)
from planner.core.authority.logic import (
    ChainFacts,
    is_below,
    is_self,
    shares_the_chain,
    stands_above,
    stands_above_or_is_self,
)
from planner.core.authority.service import (
    chain_facts,
    is_above,
    is_above_or_self,
    refuse_outcome_re_parenting,
    require_above,
    require_above_a_ticket_being_created,
    require_above_or_self,
    require_holder_can_be_asked,
    require_in_chain,
    require_self,
)

__all__ = [
    "ANY_ID",
    "ChainFacts",
    "Target",
    "TargetKind",
    "chain_facts",
    "is_above",
    "is_above_or_self",
    "is_below",
    "is_self",
    "outcome",
    "owner_only",
    "plan",
    "refuse_outcome_re_parenting",
    "require_above",
    "require_above_a_ticket_being_created",
    "require_above_or_self",
    "require_holder_can_be_asked",
    "require_in_chain",
    "require_self",
    "shares_the_chain",
    "stands_above",
    "stands_above_or_is_self",
    "ticket",
]
