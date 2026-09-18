"""The complete behavior declaration for the ``product_design`` Worker type."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)

PRODUCT_DESIGN_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="product_design",
    label="Product Design",
    stages=(
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False, StageOwnershipMode.worker),
        StageDefinition(
            "needs_direction",
            "Direction",
            "direction",
            False,
            StageOwnershipMode.worker,
        ),
        StageDefinition(
            "needs_wireframe",
            "Wireframe",
            "wireframe",
            False,
            StageOwnershipMode.user,
        ),
        StageDefinition(
            "needs_design",
            "Design",
            "design",
            False,
            StageOwnershipMode.user,
        ),
        StageDefinition("needs_closeout", "Closeout", "closeout", False, StageOwnershipMode.worker),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
        FieldDefinition("direction", "Direction"),
        FieldDefinition("wireframe", "Wireframe"),
        FieldDefinition("design", "Design"),
        FieldDefinition("closeout", "Closeout"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker-product-design",
        default_model="opus[1m]",
        default_reasoning_effort="high",
        toolset_profile="default",
        default_backend="claude",
    ),
    supports_prefix_reconciliation=True,
)


__all__ = ["PRODUCT_DESIGN_WORKER_TYPE_DEFINITION"]
