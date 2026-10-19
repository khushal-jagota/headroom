"""CLI reads preserve approval identity without exposing principal IDs in text."""

from typing import Any

import pytest

from planner.cli import main as cli
from planner.cli.record_projection import project_record, render_text


def test_ticket_approval_identity_survives_manifest_and_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approvals: dict[str, dict[str, Any]] = {
        "brief": {
            "method": "explicit_accept",
            "principal": {"kind": "owner", "id": "owner"},
            "display_label": "Approved by Khushal",
        },
        "plan": {
            "method": "explicit_accept",
            "principal": {"kind": "sprint_item", "id": "si_private_principal"},
            "display_label": "Approved by Release Operations",
        },
        "what_changes": {
            "method": "auto_accept",
            "principal": None,
            "display_label": "Auto-accepted",
        },
        "success_condition": {
            "method": "unknown",
            "principal": None,
            "display_label": "Approval unknown",
        },
    }
    fields = [*approvals, "implementation"]
    monkeypatch.setattr(
        cli, "_worker_type", lambda *_: {"fields": [{"id": field} for field in fields]}
    )
    header, parts = cli._ticket_record(
        {
            "worker_type": "coding",
            "field_values": {field: f"First {field}.\nSecond line." for field in approvals},
            "field_approvals": approvals,
        }
    )
    manifest = project_record(header, parts, None)
    expanded = project_record(header, parts, tuple(fields))
    for field, approval in approvals.items():
        assert manifest["manifest"][field]["approval"] == approval
        assert expanded["parts"][field]["approval"] == approval
        assert f"  {field}: # {approval['display_label']}" in render_text(manifest)
        assert f"value: | # {approval['display_label']}" in render_text(expanded)
    assert "si_private_principal" not in render_text(manifest)
    assert "si_private_principal" not in render_text(expanded)
    assert "approval" not in manifest["manifest"]["implementation"]
    assert "approval" not in expanded["parts"]["implementation"]
    assert "    value: null\n" in render_text(expanded)
    assert "approval" not in parts["proposal"]
    assert "approval" not in parts["recap"]
