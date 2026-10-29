"""The one rule: you may act on anything strictly below you.

Dependency-free. Everything the rule needs from the database arrives as
:class:`ChainFacts`, so the rule itself is a function of its arguments and is tested
with no mocks and no connection.

Three sentences decide every call in Panels.

1. **Position.** Khushal and the Chief stand above everything. A Manager stands above
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

    ``target_parent_manager_id`` is the Manager a Ticket target sits under right now, or
    ``None`` when the target is not a Ticket or the Ticket sits under no Manager.

    ``caller_declared_targets`` are the targets the caller's Worker type declares it
    stands above. It is empty for every caller that is not a Ticket, and for every
    Ticket whose Worker type declares nothing.

    ``caller_parent_manager_id`` is the Manager the caller sits under right now, or
    ``None`` when the caller is not a Ticket or sits under no Manager. It is the same
    column as ``target_parent_manager_id``, read about the caller instead of the target,
    and only :func:`is_below` uses it.

    ``target_is_itself_a_principal`` is whether the target is a thing that can act at all:
    a Ticket row or Manager row that exists. A nonexistent identity cannot act as itself.
    """

    target_parent_manager_id: str | None = None
    caller_declared_targets: tuple[Target, ...] = field(default_factory=tuple)
    target_is_itself_a_principal: bool = False
    caller_parent_manager_id: str | None = None


def is_self(caller: Principal, target: Target, facts: ChainFacts) -> bool:
    """Whether the caller *is* the target, rather than standing above it.

    A target that is not a live principal is nobody, so nobody is it.
    """
    if not facts.target_is_itself_a_principal:
        return False
    if caller.kind is PrincipalKind.ticket:
        return target.kind is TargetKind.ticket and caller.id == target.id
    if caller.kind is PrincipalKind.goal:
        return target.kind is TargetKind.goal and caller.id == target.id
    if caller.kind is PrincipalKind.manager:
        return target.kind is TargetKind.manager and caller.id == target.id
    return False


def stands_above(caller: Principal, target: Target, facts: ChainFacts) -> bool:
    """Whether the caller stands strictly above the target."""
    if caller.kind in _ABOVE_EVERYTHING:
        return True
    if is_self(caller, target, facts):
        return False
    if caller.kind is PrincipalKind.manager:
        return target.kind is TargetKind.ticket and facts.target_parent_manager_id == caller.id
    if caller.kind is PrincipalKind.ticket:
        return any(declared.covers(target) for declared in facts.caller_declared_targets)
    return False


def stands_above_or_is_self(caller: Principal, target: Target, facts: ChainFacts) -> bool:
    """The rule, or the caller acting on its own record."""
    return is_self(caller, target, facts) or stands_above(caller, target, facts)


def is_below(caller: Principal, target: Target, facts: ChainFacts) -> bool:
    """Whether the caller sits strictly below the target: the mirror of :func:`stands_above`.

    Only a Ticket is below anything, and the only thing it is below is its own Manager.
    This is not authority and nothing is decided by it alone. It exists because creation
    asks a question the rule above cannot express: not "may I act on this Manager" but
    "am I already in it". See :func:`shares_the_chain`.
    """
    if not facts.target_is_itself_a_principal:
        return False
    if caller.kind is not PrincipalKind.ticket:
        return False
    return target.kind is TargetKind.manager and facts.caller_parent_manager_id == target.id


def shares_the_chain(caller: Principal, target: Target, facts: ChainFacts) -> bool:
    """Whether the caller and the target are in one chain: above it, it, or below it.

    The question creation asks about a parent Manager. ``docs/authority.md`` states the
    answer it serves: a created thing belongs to its creator's chain. Putting work under
    a Manager you are already in adds nothing to your reach, because the Manager that
    gains a child is one you already answer to. Naming any other Manager is borrowing a
    position you do not hold, which is what this refuses.

    It decides a parent and nothing else. Whether the creator may also state that
    Ticket's ceiling or its holder is :func:`stands_above`, asked separately.
    """
    return stands_above_or_is_self(caller, target, facts) or is_below(caller, target, facts)


__all__ = [
    "ChainFacts",
    "is_below",
    "is_self",
    "shares_the_chain",
    "stands_above",
    "stands_above_or_is_self",
]
