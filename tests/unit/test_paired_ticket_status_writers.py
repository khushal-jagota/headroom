"""Closure contract for the code that puts a Ticket at `paired`.

A supervisor wake reads `paired` twice over. A Ticket at `paired` with no proposal
parked is a conversation to join, and the supervisor is told. A Ticket at `paired` with
a proposal still parked is the user replying to that proposal, and the supervisor is
not told. Nothing stores which one happened. The difference is derived, and it holds
only because of what the writers below do:

- a paired-owned Stage rests and departs at `paired`, and a Stage that never carried a
  proposal has none parked,
- a human reply moves a Ticket off approval status, and the proposal it replied to
  stays exactly where it was.

A third writer could produce `paired` some other way and take that reasoning with it,
silently. This test fails when one appears, so the author has to come and read the rule
in ``planner.runtime.sprint_item_supervisor_wake``.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "planner"

PAIRED_TICKET_STATUS_WRITERS = {
    "tickets/logic/machine.py:resting_ticket_status",
    "tickets/logic/machine.py:worker_step_departure_status",
    "tickets/data.py:enter_paired_on_human_reply",
}


def _yields_paired_status(node: ast.AST) -> bool:
    """Whether this expression is `TicketStatus.paired` itself.

    Only the value counts. Reading the status, or testing a Ticket against it, names it
    too, and neither one puts a Ticket there.
    """
    if isinstance(node, ast.Attribute):
        return (
            node.attr == "paired"
            and isinstance(node.value, ast.Name)
            and node.value.id == "TicketStatus"
        )
    if isinstance(node, ast.IfExp):
        return _yields_paired_status(node.body) or _yields_paired_status(node.orelse)
    if isinstance(node, ast.BoolOp):
        return any(_yields_paired_status(value) for value in node.values)
    return False


def _writes_paired_status(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether this function hands `paired` on, by returning it or by passing it."""
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Return)
            and node.value is not None
            and _yields_paired_status(node.value)
        ):
            return True
        if isinstance(node, ast.Call) and any(
            _yields_paired_status(argument)
            for argument in (*node.args, *(keyword.value for keyword in node.keywords))
        ):
            return True
    return False


def test_only_the_known_writers_put_a_ticket_at_paired() -> None:
    found: set[str] = set()
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef
            ) and _writes_paired_status(node):
                found.add(f"{path.relative_to(SOURCE_ROOT).as_posix()}:{node.name}")

    assert found == PAIRED_TICKET_STATUS_WRITERS, (
        "the writers of `paired` changed; read the paired rule in "
        "planner.runtime.sprint_item_supervisor_wake before updating this list"
    )
