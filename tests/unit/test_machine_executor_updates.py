"""One-machine update coverage cannot prove that another machine's report is inert."""

from pathlib import Path
from uuid import uuid4

import pytest
from tests.unit.test_mac_updates import (
    TARGET_SHA,
    MacProvider,
    attempt,
    mac_service,
    write,
)

from planner.environments.update_attempts import UpdateConflict
from planner.executor.update_contracts import MacUpdateReport


def test_update_targets_reports_and_archive_authority_are_machine_specific(tmp_path: Path) -> None:
    provider = MacProvider()
    published = provider.publish()
    original = mac_service(tmp_path, provider)
    original.prepare(published)
    write(original, attempt(published, "installing"))
    extra = original.for_machine(str(uuid4()))
    target = original.check().target
    other = extra.check().target
    assert target and other and target.request_id != other.request_id
    with pytest.raises(UpdateConflict):
        extra.report(MacUpdateReport(target.request_id, TARGET_SHA, "downloading"), None)
    with pytest.raises(UpdateConflict):
        extra.archive(target.request_id)
    extra.report(MacUpdateReport(other.request_id, TARGET_SHA, "downloading"), None)
    extra_status, original_status = extra.status(), original.status()
    assert extra_status is not None and extra_status.phase == "downloading"
    assert original_status is not None and original_status.phase == "waiting"
    assert extra.archive(other.request_id) == original.archive(target.request_id)
