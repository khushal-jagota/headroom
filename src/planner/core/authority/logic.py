"""The one rule: you may act on anything strictly below you.

Dependency-free. Everything the rule needs from the database arrives as
:class:`ChainFacts`, so the rule itself is a function of its arguments and is tested
with no mocks and no connection.

Three sentences decide every call in Panels.

1. **Position.** Khushal and the Chief stand above everything. An Outcome stands above
   its Tickets. An ordinary Ticket stands above nothing. A planning Ticket stands above
   the plan its Worker type declares.
2. **The rule.** You may act on anything strictly below you. Nothing is below itself, so
   a Ticket's worker cannot decide its own proposal. That falls out of the rule rather
   than being written down as an exception.
3. **Your own record.** A principal writes its own record through its own operations.
   That is not authority over anything, so :func:`stands_above` does not answer it;
   :func:`is_self` does, and the call site says which question it is asking.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from planner.core.authority.contracts import Target, TargetKind
from planner.core.contracts import Principal, PrincipalKind

_ABOVE_EVERYTHING = frozenset({PrincipalKind.owner, PrincipalKind.chief})


@dataclass(frozen=True, slots=True)
class ChainFacts:
    """What the database says about one target and one caller.

    ``target_parent_outcome_id`` is the Outcome a Ticket target sits under right now, or
    ``None`` when the target is not a Ticket or the Ticket sits under no Outcome.

    ``caller_declared_targets`` are the targets the caller's Worker type declares it
    stands above. It is empty for every caller that is not a Ticket, and for every
    Ticket whose Worker type declares nothing.
    """

    target_parent_outcome_id: str | None = None
    caller_declared_targets: tuple[Target, ...] = field(default_factory=tuple)


def is_self(caller: Principal, target: Target) -> bool:
    """Whether the caller *is* the target, rather than standing above it."""
    if caller.kind is PrincipalKind.ticket:
        return target.kind is TargetKind.ticket and caller.id == target.id
    if caller.kind is PrincipalKind.sprint_item:
        return target.kind is TargetKind.outcome and caller.id == target.id
    return False


def stands_above(caller: Principal, target: Target, facts: ChainFacts) -> bool:
    """Whether the caller stands strictly above the target."""
    if caller.kind in _ABOVE_EVERYTHING:
        return True
    if is_self(caller, target):
        return False
    if caller.kind is PrincipalKind.sprint_item:
        return target.kind is TargetKind.ticket and facts.target_parent_outcome_id == caller.id
    if caller.kind is PrincipalKind.ticket:
        return any(declared.covers(target) for declared in facts.caller_declared_targets)
    return False


def stands_above_or_is_self(caller: Principal, target: Target, facts: ChainFacts) -> bool:
    """The rule, or the caller acting on its own record."""
    return is_self(caller, target) or stands_above(caller, target, facts)


__all__ = ["ChainFacts", "is_self", "stands_above", "stands_above_or_is_self"]
