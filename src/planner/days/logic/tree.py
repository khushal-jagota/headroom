"""Plan-tree transforms (§6.3) as pure functions ``PlanTree → (new_tree, effects)``,
plus JSON serialization of the tree for the ``days.plan`` column and for event
payloads that carry ``old_tree``. No side effects; the data layer persists the
returned tree and applies the effects. Stdlib + planner contract modules only."""

from __future__ import annotations

from dataclasses import replace

from planner.core.contracts import EventKind, JsonDict
from planner.days.contracts import NodeStatus, PlanNode, PlanRoot, PlanTree
from planner.days.logic.effects import (
    AddTicketToDay,
    Effect,
    EmitEvent,
    ReplanChild,
    ReplanRoot,
)

# A node address in the plan tree: the literal ``"root"`` or a child ``position``.
# Mirrors the plan_node_accepted / plan_node_invalidated payload contract, whose
# ``{node}`` value is ``"root" | <child position>``.
NodeRef = str | int


def tree_to_dict(tree: PlanTree) -> JsonDict:
    """{root:{focus,status}, children:[{ticket_id,note,status,position}, ...]}.

    NodeStatus serialized as its str value; child order preserved.
    """
    return {
        "root": {"focus": tree.root.focus, "status": tree.root.status.value},
        "children": [
            {
                "ticket_id": child.ticket_id,
                "note": child.note,
                "status": child.status.value,
                "position": child.position,
            }
            for child in tree.children
        ],
    }


def tree_from_dict(data: JsonDict) -> PlanTree:
    """Inverse of ``tree_to_dict``. status parsed via ``NodeStatus(...)``."""
    root = data["root"]
    children = [
        PlanNode(
            ticket_id=child["ticket_id"],
            note=child["note"],
            status=NodeStatus(child["status"]),
            position=child["position"],
        )
        for child in data["children"]
    ]
    return PlanTree(
        root=PlanRoot(focus=root["focus"], status=NodeStatus(root["status"])),
        children=children,
    )


def as_proposed(tree: PlanTree) -> PlanTree:
    """Return a copy with root and every child status=proposed. The boundary
    stores the judgment tree as a PROPOSED plan (§6.2)."""
    return PlanTree(
        root=replace(tree.root, status=NodeStatus.proposed),
        children=[replace(child, status=NodeStatus.proposed) for child in tree.children],
    )


def accept_node(tree: PlanTree, ref: NodeRef) -> tuple[PlanTree, list[Effect]]:
    """§6.3: accepting a node sets it accepted; accepting root does NOT cascade.

    A child with a ticket_id also yields an AddTicketToDay effect (appended at
    end if absent). Effect order: EmitEvent(plan_node_accepted) then (for a child
    with a ticket_id) AddTicketToDay.
    """
    if ref == "root":
        new_tree = PlanTree(
            root=replace(tree.root, status=NodeStatus.accepted),
            children=[replace(child) for child in tree.children],
        )
        return new_tree, [EmitEvent(EventKind.plan_node_accepted, {"node": "root"})]

    accepted: PlanNode | None = None
    children: list[PlanNode] = []
    for child in tree.children:
        if child.position == ref:
            accepted = replace(child, status=NodeStatus.accepted)
            children.append(accepted)
        else:
            children.append(replace(child))
    new_tree = PlanTree(root=replace(tree.root), children=children)
    effects: list[Effect] = [EmitEvent(EventKind.plan_node_accepted, {"node": ref})]
    if accepted is not None and accepted.ticket_id:
        effects.append(AddTicketToDay(accepted.ticket_id))
    return new_tree, effects


def accept_all(tree: PlanTree) -> tuple[PlanTree, list[Effect]]:
    """§6.3 accept-all: root + every child accepted. Effects =
    [EmitEvent(plan_accepted_all, {})] then AddTicketToDay(child.ticket_id) for
    each child with a ticket_id, in child position order. Idempotent add is the
    data layer's job (a17)."""
    new_tree = PlanTree(
        root=replace(tree.root, status=NodeStatus.accepted),
        children=[replace(child, status=NodeStatus.accepted) for child in tree.children],
    )
    effects: list[Effect] = [EmitEvent(EventKind.plan_accepted_all, {})]
    for child in tree.children:
        if child.ticket_id:
            effects.append(AddTicketToDay(child.ticket_id))
    return new_tree, effects


def invalidate_root(tree: PlanTree) -> tuple[PlanTree, list[Effect]]:
    """§6.3: root + EVERY child → invalidated; request exactly one root replan."""
    new_tree = PlanTree(
        root=replace(tree.root, status=NodeStatus.invalidated),
        children=[replace(child, status=NodeStatus.invalidated) for child in tree.children],
    )
    effects: list[Effect] = [
        EmitEvent(EventKind.plan_node_invalidated, {"node": "root"}),
        ReplanRoot(),
    ]
    return new_tree, effects


def invalidate_child(tree: PlanTree, position: int) -> tuple[PlanTree, list[Effect]]:
    """§6.3: ONLY the child at ``position`` → invalidated; root and every other
    child keep their prior status. Requests exactly one child replan."""
    children: list[PlanNode] = []
    for child in tree.children:
        if child.position == position:
            children.append(replace(child, status=NodeStatus.invalidated))
        else:
            children.append(replace(child))
    new_tree = PlanTree(root=replace(tree.root), children=children)
    effects: list[Effect] = [
        EmitEvent(EventKind.plan_node_invalidated, {"node": position}),
        ReplanChild(position),
    ]
    return new_tree, effects


def reject_all(tree: PlanTree) -> tuple[None, list[Effect]]:
    """§6.3 reject-all: plan cleared. New tree is None (the data layer writes
    days.plan = NULL). The old tree is preserved in the event payload."""
    effects: list[Effect] = [
        EmitEvent(EventKind.plan_rejected, {"old_tree": tree_to_dict(tree)})
    ]
    return None, effects
