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
    is_self,
    stands_above,
    stands_above_or_is_self,
)
from planner.core.authority.service import (
    chain_facts,
    is_above,
    is_above_or_self,
    require_above,
    require_above_or_self,
)

__all__ = [
    "ANY_ID",
    "ChainFacts",
    "Target",
    "TargetKind",
    "chain_facts",
    "is_above",
    "is_above_or_self",
    "is_self",
    "outcome",
    "owner_only",
    "plan",
    "require_above",
    "require_above_or_self",
    "stands_above",
    "stands_above_or_is_self",
    "ticket",
]
