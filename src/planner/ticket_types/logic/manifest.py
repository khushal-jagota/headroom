"""The manifest serializer: serialize_definition turns a WorkflowDefinition into
the single ManifestDict shape both the CLI and web later consume (PLAN invariant
4).

``advance`` / ``ceiling_range`` / ``default_ceiling`` come from the derived views
in ``views.py``, so the manifest cannot drift from the definition. The output is
JSON-round-trippable (all values are str / bool / None / list / dict of those).
``dropped`` is surfaced under its own key, excluded from ``stages`` (mirrors the
code model). Worker-profile internals (model / effort / toolset) and
``supports_prefix_reconciliation`` are deliberately NOT serialized.

Pure: contracts + views only."""

from __future__ import annotations

from planner.ticket_types.contracts import (
    ManifestDict,
    ManifestField,
    ManifestStage,
    WorkflowDefinition,
)
from planner.ticket_types.logic.views import (
    advance_map,
    ceiling_range,
    default_ceiling,
)


def serialize_definition(defn: WorkflowDefinition) -> ManifestDict:
    stages: list[ManifestStage] = [
        {
            "id": s.id,
            "label": s.label,
            "gating_field": s.gating_field,
            "is_terminal": s.is_terminal,
        }
        for s in defn.stages
    ]
    dropped: ManifestStage = {
        "id": "dropped",
        "label": defn.dropped_stage.label,
        "gating_field": None,
        "is_terminal": True,
    }
    fields: list[ManifestField] = [{"id": f.id, "label": f.label} for f in defn.fields]
    return {
        "worker_type": defn.type_id,
        "label": defn.label,
        "stages": stages,
        "dropped": dropped,
        "advance": advance_map(defn),
        "fields": fields,
        "ceiling_range": list(ceiling_range(defn)),
        "default_ceiling": default_ceiling(defn),
        "worker_profile_id": defn.worker_profile.specialist_skill,
    }
