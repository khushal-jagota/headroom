"""Fresh Python proves executor copy/publication and Mac configuration intake.

Existing workspace proofs do not cover these boundaries. One synthetic file
transaction and one export-only Node process suffice, with no network host.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.executor import configuration, update_configuration
from planner.executor.configuration import executor_paths, validate_server_url
from planner.executor.files import (
    contained_path,
    copy_verified,
    file_digest,
    file_version,
    install_verified,
    safe_component,
    verified_version,
)
from planner.executor.protocol import FileManifest, FileScope

ROOT = Path(__file__).resolve().parents[3]


def test_unit22_mac_files_and_configuration(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    tmp_path = tmp_path.resolve(strict=True)
    homes = create_synthetic_homes(tmp_path)
    content = bytes(range(256)) * 1100 + "native input 🦉\n".encode()
    for name in ("py", "ts"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "source").write_bytes(content)
    # Python's raw regular_descriptor blocks on a FIFO before fstat. The TS worker
    # must refuse it without a writer; capture already excludes it in Python.
    os.mkfifo(tmp_path / "ts" / "fifo", 0o600)
    root = tmp_path / "py"
    sha, count = file_digest(root / "source")
    manifest = FileManifest(FileScope.ticket, "ticket", "target", sha, count)
    copy_verified(root / "source", root / "copy", manifest)
    target = root / "nested" / "target"
    install_verified(root / "copy", target, manifest)
    version = file_version(target)
    verified = verified_version(target, manifest)
    corrupt_refused = False
    try:
        copy_verified(root / "source", root / "bad-copy", replace(manifest, sha256="0" * 64))
    except ValueError:
        corrupt_refused = True
    old = target.read_bytes()
    try:
        install_verified(root / "source", target, replace(manifest, byte_count=count - 1))
    except ValueError:
        pass
    components = ["ticket_123-X", "", "../escape", "name\n", "name\r", "name\u2028", "é"]
    relatives = ["nested/target", "", "../escape", "a//b", "a/./b", "a/%252e", "a\\b", "a\x00b"]
    urls = [
        "https://fixture.invalid",
        "https://fixture.invalid/",
        "HTTPS://EXAMPLE.invalid:00443/",
        "https://[::1]:443",
        "https://fixture.invalid:80",
        "https://fixture.invalid?#",
        "http://fixture.invalid",
        "https://user@fixture.invalid",
        "https://fixture.invalid/path",
        "https://fixture.invalid:0",
        "https://fixture.invalid:65536",
        "https://fixture.invalid:bad",
        "https://fixture.invalid/\n",
        "https://fixture.invalid/\u0085",
        "https://fixture.invalid?q=1",
        "https://fixture.invalid#fragment",
    ]
    canonical = tmp_path / "canonical"
    (canonical / "child").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(canonical / "child", target_is_directory=True)
    roots = ["", "~/executor", str(alias) + "/../executor", str(tmp_path / "absent" / "state")]
    legacy = Path(homes["HOME"]) / "Library" / "Application Support" / "Panels" / "executor"
    legacy.mkdir(parents=True)
    legacy_alias = tmp_path / "legacy-alias"
    legacy_alias.symlink_to(legacy, target_is_directory=True)
    refused_roots = [str(legacy), str(legacy / "spool"), str(legacy_alias)]

    def outcome(action: Callable[..., Any], *args: Any) -> Any:
        try:
            return {"value": action(*args)}
        except ValueError as error:
            return {"error": {"kind": "validation", "message": str(error)}}

    def relative_path(value: str) -> str:
        return str(contained_path(root, value))[len(str(root)) :]

    expected: dict[str, Any] = {
        "digest": [sha, count],
        "copied": (root / "copy").read_bytes() == content,
        "installed": list(file_digest(target)),
        "verified": version is not None and version == verified,
        "corrupt_refused": corrupt_refused,
        "corrupt_cleaned": not (root / "bad-copy").exists(),
        "old_preserved": target.read_bytes() == old,
        "files": sorted(p.name for p in target.parent.iterdir()),
        "components": [outcome(safe_component, value) for value in components],
        "relatives": [outcome(relative_path, value) for value in relatives],
        "urls": [outcome(validate_server_url, value) for value in urls],
    }
    with synthetic_home_environment(homes):
        original = os.environ.get("PLAN_EXECUTOR_ROOT")
        try:
            paths = []
            for configured in roots:
                os.environ["PLAN_EXECUTOR_ROOT"] = configured
                resolved = executor_paths()
                paths.append(
                    [
                        str(getattr(resolved, name))
                        for name in (
                            "root",
                            "workspace",
                            "spool",
                            "artifacts",
                            "message_files_anchor",
                            "skills",
                            "run",
                            "ipc_socket",
                        )
                    ]
                )
            expected["paths"] = paths
        finally:
            if original is None:
                os.environ.pop("PLAN_EXECUTOR_ROOT", None)
            else:
                os.environ["PLAN_EXECUTOR_ROOT"] = original
    specification = tmp_path / "input.json"
    specification.write_text(
        json.dumps(
            {
                "root": str(tmp_path),
                "urls": urls,
                "components": components,
                "relatives": relatives,
                "roots": roots,
                "refused_roots": refused_roots,
            }
        )
    )
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    environment.update(homes)
    environment.update(
        {
            "PLAN_TEST_MODE": "1",
            "PLAN_EXECUTOR_ROOT": str(tmp_path / "executor"),
            "PLAN_SERVER_URL": "http://127.0.0.1:1",
        }
    )
    output = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; "
            "const {files_parity}=await import(process.argv[1]); "
            "const input=JSON.parse(readFileSync(process.argv[2],'utf8')); "
            "process.stdout.write(JSON.stringify(await files_parity(input)));",
            (ROOT / "server/executor/mac/testing/files_parity.ts").as_uri(),
            str(specification),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert output.returncode == 0, output.stderr
    actual = json.loads(output.stdout)
    assert actual.pop("fifo_refused") is True
    python_default_paths = expected["paths"].pop(0)
    typescript_default_paths = actual["paths"].pop(0)
    typescript_root = legacy.with_name("executor-typescript")
    assert typescript_default_paths == [
        str(path)
        for path in (
            typescript_root,
            typescript_root / "workspace",
            typescript_root / "spool",
            typescript_root / "artifacts" / "tickets",
            typescript_root / "artifacts" / "message-files.db",
            typescript_root / "skills",
            typescript_root / "run",
            typescript_root / "run" / "executor.sock",
        )
    ]
    assert actual.pop("refused_roots") == [
        {
            "error": {
                "kind": "validation",
                "message": (
                    "TypeScript executor root must be separate from the Python executor root"
                ),
            }
        }
    ] * len(refused_roots)
    assert (
        actual.pop("refused_homes")
        == [{"error": {"kind": "validation", "message": "executor HOME must be an absolute path"}}]
        * 2
    )
    settings = actual.pop("settings")
    for name, value in settings.items():
        reference = getattr(configuration, name, getattr(update_configuration, name, None))
        if is_dataclass(reference) and not isinstance(reference, type):
            reference = asdict(reference)
        assert value == reference, name
    nul_messages = []
    for observed in (actual, expected):
        error = observed["relatives"][relatives.index("a\x00b")]["error"]
        assert error["kind"] == "validation"
        assert isinstance(error["message"], str) and error["message"]
        nul_messages.append(error["message"])
        error["message"] = "<NUL path refused>"
    assert actual == expected
    report = request.config.getoption("--compare-report-dir")
    if report:
        destination = Path(report)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "unit-22-mac-files.json").write_text(
            json.dumps(
                {
                    "kind": "direct",
                    "equal": True,
                    "bytes": count,
                    "observations": actual,
                    "accepted_difference": {
                        "python_default_paths": python_default_paths,
                        "typescript_default_paths": typescript_default_paths,
                        "reason": (
                            "The TypeScript installation must stay separate from Python state."
                        ),
                        "fifo": "TS refuses FIFO intake without an external writer.",
                        "nul_messages": nul_messages,
                    },
                    "seconds": time.perf_counter() - started,
                },
                indent=2,
            )
            + "\n"
        )
