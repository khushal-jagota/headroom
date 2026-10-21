"""What an operation acts on, and what a Worker type stands above.

A :class:`Target` names the one thing an operation acts on. It says nothing about who
is asking. :func:`planner.core.authority.service.stands_above` answers that.

Four kinds cover every guarded operation in Panels:

``ticket``
    One Ticket. Its Manager stands above it, because ``tickets.manager_id`` says so.
``manager``
    One Manager. Khushal and the Chief stand above it.
``plan``
    A retained historical declaration. No current Worker type grants shared-plan authority.
``owner_only``
    Everything else Khushal owns outright: projects, settings, skills, Worker types,
    feedback. Khushal and the Chief stand above it and nothing else does. ``id`` names
    what it is, and is read only by a refusal.

A Worker type declares the targets it stands above with the same type, so there is one
vocabulary and not two. ``ANY_ID`` in a declaration means "every target of this kind",
and empty ``fields`` mean "every field of this object".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

ANY_ID: Final = "*"


class TargetKind(StrEnum):
    """The complete set of things an authority question can be asked about."""

    ticket = "ticket"
    manager = "manager"
    plan = "plan"
    owner_only = "owner_only"


@dataclass(frozen=True, slots=True)
class Target:
    kind: TargetKind
    id: str
    fields: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.id or self.id != self.id.strip():
            raise ValueError("target id must be non-empty and must not contain outer whitespace")
        if self.fields and self.kind not in (TargetKind.plan, TargetKind.manager):
            raise ValueError("only a plan or manager target carries fields")

    def covers(self, other: Target) -> bool:
        """Whether this declaration reaches ``other``, the target of a real operation.

        A declaration that names no fields reaches the whole object. One that names fields
        reaches an operation on those fields and nothing else — including an operation that
        names no fields, which is an operation on the whole object. Deleting a Manager
        names no field, so a declaration over four of its fields must not reach it.
        """
        if self.kind is not other.kind:
            return False
        if self.id != ANY_ID and self.id != other.id:
            return False
        if not self.fields:
            return True
        if not other.fields:
            return False
        return other.fields <= self.fields


def ticket(ticket_id: str) -> Target:
    return Target(TargetKind.ticket, ticket_id)


def manager(manager_id: str, *fields: str) -> Target:
    return Target(TargetKind.manager, manager_id, frozenset(fields))


def plan(plan_object: str, *fields: str) -> Target:
    return Target(TargetKind.plan, plan_object, frozenset(fields))


def owner_only(what: str) -> Target:
    return Target(TargetKind.owner_only, what)


__all__ = [
    "ANY_ID",
    "Target",
    "TargetKind",
    "manager",
    "owner_only",
    "plan",
    "ticket",
]
