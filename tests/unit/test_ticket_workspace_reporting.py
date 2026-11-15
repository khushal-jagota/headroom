"""Keep recurring cleanup inexpensive without hiding changed retention reasons."""

import json
import logging
from pathlib import Path

import pytest

from planner.ticket_workspaces import filesystem
from planner.ticket_workspaces.reporting import RetainedWorkspaceReporter


def test_periodic_inventory_only_sizes_folders_on_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "tickets"
    (root / "t_aaaaaaaa").mkdir(parents=True)
    measured: list[Path] = []
    def measure(path: Path) -> int:
        measured.append(path)
        return 512

    monkeypatch.setattr(filesystem, "_allocated_bytes", measure)

    filesystem.cleanup_local_workspaces(root, {}, 1, apply=False)
    manifest = json.loads((tmp_path / "ticket-workspaces-cleanup.json").read_text())
    assert measured == []
    assert manifest["folders"][0]["allocated_bytes"] is None

    filesystem.cleanup_local_workspaces(root, {}, 2, apply=False, measure_size=True)
    manifest = json.loads((tmp_path / "ticket-workspaces-cleanup.json").read_text())
    assert measured == [root / "t_aaaaaaaa"]
    assert manifest["folders"][0]["allocated_bytes"] == 512


def test_retained_workspace_warning_only_repeats_after_change_or_resolution(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reporter = RetainedWorkspaceReporter()
    logger = logging.getLogger("ticket-workspace-reporting-test")
    first = [{"path": "/ticket", "reason": "unpushed"}]
    with caplog.at_level(logging.WARNING, logger=logger.name):
        reporter.report(logger, first)
        reporter.report(logger, first)
        reporter.report(logger, [{"path": "/ticket", "reason": "process busy"}])
        reporter.report(logger, [])
        reporter.report(logger, first)
    assert [record.message for record in caplog.records] == [
        "Ticket workspace retained: /ticket (unpushed)",
        "Ticket workspace retained: /ticket (process busy)",
        "Ticket workspace retained: /ticket (unpushed)",
    ]
