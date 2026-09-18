import json
from pathlib import Path

from planner.worker_settings import service
from planner.worker_types.configuration import configured_worker_type_registry


def _legacy_payload() -> dict[str, object]:
    return {
        "worker_type": "coding",
        "suggested_next_ceiling": "done",
        "stage_ownership_defaults": {"needs_success": "user"},
        "launch_defaults": {
            "employee_backend": "codex",
            "employee_launch_model": "gpt-5.6-sol",
            "employee_launch_reasoning_effort": "medium",
        },
    }


def test_read_reconciles_legacy_policy_out_of_managed_settings(tmp_path: Path) -> None:
    path = tmp_path / "worker-settings" / "coding" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_legacy_payload()), encoding="utf-8")

    settings = service.read_worker_settings(
        tmp_path, configured_worker_type_registry(), "coding"
    )

    assert settings.worker_type == "coding"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "launch_defaults": _legacy_payload()["launch_defaults"],
        "worker_type": "coding",
    }


def test_recovery_reconciles_legacy_policy_before_backing_up(tmp_path: Path) -> None:
    root = tmp_path / "worker-settings"
    recovery_path = root / ".last-known-good" / "coding" / "settings.json"
    recovery_path.parent.mkdir(parents=True)
    recovery_path.write_text(json.dumps(_legacy_payload()), encoding="utf-8")

    service.read_worker_settings(tmp_path, configured_worker_type_registry(), "coding")

    current_path = root / "coding" / "settings.json"
    expected = {
        "launch_defaults": _legacy_payload()["launch_defaults"],
        "worker_type": "coding",
    }
    assert json.loads(current_path.read_text(encoding="utf-8")) == expected
    assert json.loads(recovery_path.read_text(encoding="utf-8")) == expected
