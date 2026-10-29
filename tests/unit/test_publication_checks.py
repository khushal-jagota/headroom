"""Cross-runner evidence must never turn absent or foreign proof into publication."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from scripts import publication_checks as checks

IDENTITY = {"requested_sha": "a" * 40, "workflow_sha": "b" * 40,
            "run_id": "123", "run_attempt": "2"}
SUCCESS: dict[str, object] = {"prepare": "success", "suites": "success"}
SOURCE = Path(__file__).resolve().parents[2]


@pytest.fixture
def evidence(tmp_path: Path) -> Path:
    """Reuse the real complete selection and producer for every consumer failure."""
    downloads = tmp_path / "downloads"
    selection = checks.full_selection(SOURCE)
    checks.write_object(downloads / "selection/selection.json", selection)
    checks.write_object(downloads / "selection/selection-record.json", {
        "schema": 1, "identity": IDENTITY, "selection": selection,
    })
    for suite in checks.SUITES:
        gate = tmp_path / f"producer-{suite}"
        checks.write_object(gate / "selection.json", selection)
        report = gate / checks.REPORTS[suite]
        report.parent.mkdir()
        report.write_text(f"{suite} report bytes")
        if suite == "browser":
            directory = report.parent / "browser-evidence-123-2-browser"
            directory.mkdir()
            (directory / ".hidden-companion").write_bytes(b"all companion bytes\x00")
            (directory / "rail.png").write_bytes(b"image evidence")
        checks.pack(suite, gate, downloads / suite, IDENTITY)
    return downloads


def test_collection_preserves_all_bytes_and_original_paths(evidence: Path, tmp_path: Path) -> None:
    gate = tmp_path / "publication"
    checks.collect(evidence, gate, IDENTITY, SUCCESS)
    for suite in checks.SUITES:
        bundle = evidence / suite
        record = checks.read_object(bundle / "suite-record.json")
        files = record["files"]
        assert isinstance(files, dict)
        for relative in files:
            assert (gate / relative).read_bytes() == (bundle / relative).read_bytes()
        assert checks.read_object(gate / f"suite-records/{suite}.json") == record
    assert (gate / "reports/browser-evidence-123-2-browser/.hidden-companion").is_file()


@pytest.mark.parametrize("location", ["selection/selection-record.json", "unit/suite-record.json"])
def test_collection_rejects_stale_source_workflow_run_or_attempt(
    evidence: Path, tmp_path: Path, location: str,
) -> None:
    path = evidence / location
    original = checks.read_object(path)
    for key in checks.IDENTITY_KEYS:
        value = dict(original)
        value["identity"] = IDENTITY | {key: "stale"}
        checks.write_object(path, value)
        with pytest.raises(ValueError, match="identity"):
            checks.collect(evidence, tmp_path / key, IDENTITY, SUCCESS)


@pytest.mark.parametrize("damage", ["report-absent", "extra-file", "hash", "record-absent",
                                    "duplicate-record", "selection", "suite", "browser-evidence"])
def test_collection_rejects_incomplete_or_modified_bundle(
    evidence: Path, tmp_path: Path, damage: str,
) -> None:
    bundle = evidence / "unit"
    record_path = bundle / "suite-record.json"
    record = checks.read_object(record_path)
    if damage == "report-absent":
        (bundle / checks.REPORTS["unit"]).unlink()
    elif damage == "extra-file":
        (bundle / "unlisted").write_text("unlisted artifact")
    elif damage == "hash":
        (bundle / checks.REPORTS["unit"]).write_text("damaged report")
    elif damage == "record-absent":
        record_path.unlink()
    elif damage == "duplicate-record":
        shutil.copyfile(record_path, bundle / "duplicate-suite-record.json")
    elif damage in {"selection", "suite"}:
        record["selected" if damage == "selection" else "suite"] = "foreign"
        checks.write_object(record_path, record)
    else:
        bundle = evidence / "browser"
        shutil.rmtree(bundle / "reports/browser-evidence-123-2-browser")
        record = checks.read_object(bundle / "suite-record.json")
        record["files"] = {checks.REPORTS["browser"]:
                           checks.digest(bundle / checks.REPORTS["browser"])}
        checks.write_object(bundle / "suite-record.json", record)
    with pytest.raises(ValueError):
        checks.collect(evidence, tmp_path / "publication", IDENTITY, SUCCESS)


@pytest.mark.parametrize("unsafe", ["/absolute.xml", "reports/../unit.xml", "../escape",
                                    "reports//unit.xml", "reports/e2e.xml"])
def test_collection_rejects_escaping_and_foreign_inventory_paths(
    evidence: Path, tmp_path: Path, unsafe: str,
) -> None:
    record_path = evidence / "unit/suite-record.json"
    record = checks.read_object(record_path)
    # The inventory is itself untrusted, including when it names an existing report.
    record["files"] = {unsafe: checks.digest(evidence / "unit/reports/unit.xml")}
    checks.write_object(record_path, record)
    with pytest.raises(ValueError):
        checks.collect(evidence, tmp_path / "publication", IDENTITY, SUCCESS)
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("link_directory", [False, True])
def test_collection_rejects_symlinks_even_when_their_bytes_match(
    evidence: Path, tmp_path: Path, link_directory: bool,
) -> None:
    bundle = evidence / "unit"
    target = tmp_path / "target"
    if link_directory:
        (bundle / "reports").rename(target)
        (bundle / "reports").symlink_to(target, target_is_directory=True)
    else:
        report = bundle / "reports/unit.xml"
        report.rename(target)
        report.symlink_to(target)
    with pytest.raises(ValueError, match="unsafe"):
        checks.collect(evidence, tmp_path / "publication", IDENTITY, SUCCESS)


def test_collection_refuses_destination_collision_without_overwriting(
    evidence: Path, tmp_path: Path,
) -> None:
    gate = tmp_path / "publication"
    existing = gate / "reports/unit.xml"
    existing.parent.mkdir(parents=True)
    existing.write_text("prior evidence")
    with pytest.raises(ValueError, match="collision"):
        checks.collect(evidence, gate, IDENTITY, SUCCESS)
    assert existing.read_text() == "prior evidence"
    # Other independently checked diagnostics survive a failed collection gate.
    assert (gate / "reports/integration.xml").is_file()


@pytest.mark.parametrize("result", ["failure", "skipped", "cancelled", ""])
def test_failed_or_skipped_prerequisites_fail_after_diagnostics_are_collected(
    evidence: Path, tmp_path: Path, result: str,
) -> None:
    gate = tmp_path / "publication"
    with pytest.raises(ValueError, match="prerequisites did not succeed"):
        checks.collect(evidence, gate, IDENTITY, SUCCESS | {"suites": result})
    assert (gate / "reports/unit.xml").is_file()
    assert (gate / "reports/browser.json").is_file()


def test_producer_retains_diagnostics_but_refuses_missing_required_report(tmp_path: Path) -> None:
    gate = tmp_path / "producer"
    checks.write_object(gate / "selection.json", checks.full_selection(SOURCE))
    (gate / "reports").mkdir()
    (gate / "reports/unit.log").write_text("pytest failed before report creation")
    bundle = tmp_path / "bundle"
    with pytest.raises(ValueError, match="missing required suite report"):
        checks.pack("unit", gate, bundle, IDENTITY)
    assert (bundle / "reports/unit.log").is_file()
    assert json.loads((bundle / "suite-record.json").read_text())["files"]


def test_missing_report_retains_verified_failure_log_and_record_in_publication_proof(
    evidence: Path, tmp_path: Path,
) -> None:
    producer = tmp_path / "failed-producer"
    checks.write_object(producer / "selection.json", checks.full_selection(SOURCE))
    (producer / "reports").mkdir()
    diagnostic = b"dependency setup failed before pytest could create its report\n"
    (producer / "reports/unit.log").write_bytes(diagnostic)
    shutil.rmtree(evidence / "unit")
    with pytest.raises(ValueError, match="missing required suite report"):
        checks.pack("unit", producer, evidence / "unit", IDENTITY)
    gate = tmp_path / "publication"
    with pytest.raises(ValueError, match="missing required suite report"):
        checks.collect(evidence, gate, IDENTITY, SUCCESS | {"suites": "failure"})
    assert (gate / "reports/unit.log").read_bytes() == diagnostic
    assert (gate / "suite-records/unit.json").read_bytes() == (
        evidence / "unit/suite-record.json"
    ).read_bytes()
    assert not (gate / "reports/unit.xml").exists()
