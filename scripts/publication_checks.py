"""Complete publication selection and checked, source-bound cross-runner evidence.

This module deliberately uses only the standard library: preparation and failed-job
collection run before the publication interpreter or candidate exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import cast

SUITES = ("unit", "integration", "e2e", "browser")
REPORTS = {suite: f"reports/{suite}.{'json' if suite == 'browser' else 'xml'}"
           for suite in SUITES}
IDENTITY_KEYS = ("requested_sha", "workflow_sha", "run_id", "run_attempt")


def full_selection(source: Path) -> dict[str, object]:
    """Use every current suite entry, without diff-based check selection."""

    def files(pattern: str) -> list[str]:
        return sorted(path.relative_to(source).as_posix() for path in source.glob(pattern))

    catalog = json.loads((source / "web/tests/browser/catalog.json").read_text())
    if catalog.get("schema") != 1:
        raise ValueError("unsupported browser catalog")
    return {
        "ruff_paths": ["."],
        "mypy": True,
        "web_check": True,
        "unit": files("tests/unit/**/test_*.py"),
        "integration": files("tests/integration/**/test_*.py"),
        "e2e": files("tests/e2e/**/test_*.py"),
        "vitest": files("web/tests/**/*.test.ts"),
        "browser": [entry["path"] for entry in catalog["entries"]],
        "browser_format": "shared-v1",
    }


def identity(environment: dict[str, str]) -> dict[str, str]:
    value = dict(zip(IDENTITY_KEYS, (environment["PANELS_REQUESTED_SHA"],
                                    environment["GITHUB_SHA"],
                                    environment["GITHUB_RUN_ID"],
                                    environment["GITHUB_RUN_ATTEMPT"]), strict=True))
    if (any(re.fullmatch(r"[0-9a-f]{40}", value[key]) is None
            for key in IDENTITY_KEYS[:2])
            or any(re.fullmatch(r"[1-9][0-9]*", value[key]) is None
                   for key in IDENTITY_KEYS[2:])):
        raise ValueError("invalid publication identity")
    return value


def read_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def write_object(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


def require_source(source: Path, expected: dict[str, str]) -> None:
    actual = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if actual != expected["requested_sha"]:
        raise ValueError("requested source differs from prepared revision")


def prepare(source: Path, output: Path, expected: dict[str, str]) -> None:
    require_source(source, expected)
    selection = full_selection(source)
    write_object(output / "selection.json", selection)
    write_object(output / "selection-record.json", {
        "schema": 1, "identity": expected, "selection": selection,
    })


def regular_files(root: Path) -> dict[str, Path]:
    """Walk without following links, including hidden evidence files."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"missing or unsafe artifact directory: {root}")
    files: dict[str, Path] = {}
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError(f"unsafe artifact entry: {path}")
        if stat.S_ISREG(mode):
            files[path.relative_to(root).as_posix()] = path
    return files


def prepared_selection(download: Path, expected: dict[str, str]) -> dict[str, object]:
    files = regular_files(download)
    if set(files) != {"selection.json", "selection-record.json"}:
        raise ValueError("prepared selection inventory differs")
    selection = read_object(files["selection.json"])
    record = read_object(files["selection-record.json"])
    if record != {"schema": 1, "identity": expected, "selection": selection}:
        raise ValueError("prepared selection identity differs")
    for suite in SUITES:
        selected = selection.get(suite)
        if (not isinstance(selected, list) or not selected
                or any(not isinstance(path, str) or not path for path in selected)):
            raise ValueError("prepared selection has empty or invalid suites")
    return selection


def accept_selection(download: Path, gate: Path, expected: dict[str, str],
                     source: Path | None = None) -> dict[str, object]:
    selection = prepared_selection(download, expected)
    if source is not None:
        require_source(source, expected)
        if full_selection(source) != selection:
            raise ValueError("complete requested-source selection differs")
    for name in ("selection.json", "selection-record.json"):
        destination = gate / name
        if destination.exists() or destination.is_symlink():
            raise ValueError(f"evidence destination collision: {destination}")
    gate.mkdir(parents=True, exist_ok=True)
    for name in ("selection.json", "selection-record.json"):
        shutil.copyfile(download / name, gate / name)
    return selection


def evidence_path(suite: str, relative: str, expected: dict[str, str]) -> bool:
    path = PurePosixPath(relative)
    if (path.is_absolute() or path.as_posix() != relative
            or any(part in {"", ".", ".."} for part in path.parts)):
        return False
    if relative in {REPORTS[suite], f"reports/{suite}.log"}:
        return True
    browser_root = ("reports/browser-evidence-" + expected["run_id"] + "-"
                    + expected["run_attempt"] + "-browser")
    return suite == "browser" and len(path.parts) > 2 and path.parts[:2] == (
        "reports", browser_root.removeprefix("reports/"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pack(suite: str, gate: Path, output: Path, expected: dict[str, str]) -> None:
    selection = read_object(gate / "selection.json")
    output.mkdir(parents=True, exist_ok=False)
    files: dict[str, str] = {}
    for relative, path in regular_files(gate / "reports").items():
        relative = "reports/" + relative
        if not evidence_path(suite, relative, expected):
            raise ValueError(f"unexpected suite evidence: {relative}")
        files[relative] = digest(path)
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    write_object(output / "suite-record.json", {
        "schema": 1, "identity": expected, "suite": suite,
        "selected": selection[suite], "files": files,
    })
    if REPORTS[suite] not in files:
        raise ValueError(f"missing required suite report: {REPORTS[suite]}")


def checked_bundle(suite: str, download: Path, selection: dict[str, object],
                   expected: dict[str, str]) -> dict[str, Path]:
    files = regular_files(download)
    record_path = files.pop("suite-record.json", None)
    if record_path is None:
        raise ValueError(f"missing suite record: {suite}")
    record = read_object(record_path)
    hashes = record.get("files")
    if (set(record) != {"schema", "identity", "suite", "selected", "files"}
            or record["schema"] != 1 or record["identity"] != expected
            or record["suite"] != suite or record["selected"] != selection[suite]):
        raise ValueError(f"suite identity or selection differs: {suite}")
    if not isinstance(hashes, dict) or set(hashes) != set(files):
        raise ValueError(f"suite file inventory differs: {suite}")
    for relative, path in files.items():
        if not evidence_path(suite, relative, expected):
            raise ValueError(f"unsafe or foreign evidence path: {relative}")
        if hashes[relative] != digest(path):
            raise ValueError(f"suite evidence hash differs: {relative}")
    return files | {f"suite-records/{suite}.json": record_path}


def collect(downloads: Path, gate: Path, expected: dict[str, str],
            prerequisites: dict[str, object]) -> None:
    """Retain valid available evidence, then fail closed on any missing/failed work."""
    errors: list[str] = []
    if downloads.is_dir() and any(path.name not in {"selection", *SUITES}
                                  for path in downloads.iterdir()):
        errors.append("unexpected downloaded artifact directory")
    try:
        selection = accept_selection(downloads / "selection", gate, expected)
    except (OSError, ValueError) as error:
        selection = None
        errors.append(str(error))
    if selection is not None:
        for suite in SUITES:
            try:
                files = checked_bundle(suite, downloads / suite, selection, expected)
                for relative in files:
                    destination = gate / relative
                    # Check parents as well as leaves; never write through a symlink.
                    if (destination.exists() or destination.is_symlink()
                            or any(parent.is_symlink() for parent in destination.parents)):
                        raise ValueError(f"evidence destination collision: {relative}")
                    if not destination.resolve().is_relative_to(gate.resolve()):
                        raise ValueError(f"evidence escapes gate: {relative}")
                for relative, path in files.items():
                    destination = gate / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
                # Completeness is a publication requirement, not a reason to discard
                # safe, source-bound diagnostics from a suite that failed early.
                if REPORTS[suite] not in files:
                    raise ValueError(f"missing required suite report: {REPORTS[suite]}")
                if suite == "browser":
                    evidence = (f"reports/browser-evidence-{expected['run_id']}-"
                                f"{expected['run_attempt']}-browser/")
                    if not any(name.startswith(evidence) for name in files):
                        raise ValueError("browser evidence directory is empty or absent")
            except (OSError, ValueError) as error:
                errors.append(f"{suite}: {error}")
    if prerequisites != {"prepare": "success", "suites": "success"}:
        errors.append(f"publication prerequisites did not succeed: {prerequisites}")
    if errors:
        raise ValueError("; ".join(errors))


def validate(gate: Path, source: Path, expected: dict[str, str]) -> None:
    require_source(source, expected)
    selection = read_object(gate / "selection.json")
    record = read_object(gate / "selection-record.json")
    if (selection != full_selection(source)
            or record != {"schema": 1, "identity": expected, "selection": selection}):
        raise ValueError("complete requested-source selection differs")
    # The trusted checkout supplies the validators even for an older requested source.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.deployment_check_report import check_browser, check_pytest

    for suite in SUITES:
        selected = cast(list[str], selection[suite])
        if suite == "browser":
            check_browser(gate / REPORTS[suite], selected, source,
                          expected["requested_sha"],
                          f"{expected['run_id']}-{expected['run_attempt']}-browser")
        else:
            check_pytest(gate / REPORTS[suite], selected)


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("operation", choices=("prepare", "accept-selection", "pack", "collect",
                                               "verify-selection", "validate"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--gate", type=Path)
    parser.add_argument("--download", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--suite", choices=SUITES)
    args = parser.parse_args()
    expected = identity(dict(os.environ))
    source = args.source or Path(os.environ.get("PANELS_CANDIDATE_SOURCE", "."))
    gate = args.gate or Path(os.environ.get("PANELS_GATE_DIR", "."))
    if args.operation == "prepare":
        prepare(source, args.output or gate, expected)
    elif args.operation == "accept-selection":
        if args.download is None:
            parser.error("--download is required")
        accept_selection(args.download, gate, expected, source)
    elif args.operation == "pack":
        if args.suite is None or args.output is None:
            parser.error("--suite and --output are required")
        pack(args.suite, gate, args.output, expected)
    elif args.operation == "collect":
        if args.download is None:
            parser.error("--download is required")
        prerequisites = json.loads(os.environ["PANELS_PREREQUISITES"])
        collect(args.download, gate, expected, prerequisites)
    elif args.operation == "verify-selection":
        require_source(source, expected)
        if full_selection(source) != read_object(gate / "selection.json"):
            raise ValueError("complete requested-source selection differs")
    else:
        validate(gate, source, expected)


if __name__ == "__main__":
    main()
