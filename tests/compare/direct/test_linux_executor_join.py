"""Python's lease gate and the native private entry share enrollment boundaries."""

from __future__ import annotations

import copy
import json
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.compare.direct.unit20_cli import ROOT, write_report

from planner.installation.join import wait_for_lease


def test_linux_join_fresh_lease_and_private_entry(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    began = time.perf_counter()
    revision, now = "a" * 40, 1_700_000_000_000
    status: dict[str, Any] = dict(
        identity=dict(application_revision=revision, protocol_version=26),
        last_lease_failure=None,
        going_to_sleep=False,
        admission_stopped=False,
        lease_accepted_at_mac_ms=now + 1,
        refusal=None,
    )
    statuses = []
    for key, value in [
        ("application_revision", "wrong"),
        ("protocol_version", 22),
        ("lease_accepted_at_mac_ms", now - 1),
        ("admission_stopped", True),
        ("last_lease_failure", "credential refused"),
        ("refusal", "protocol refused"),
        ("lease_accepted_at_mac_ms", now + 1),
    ]:
        item = copy.deepcopy(status)
        (item["identity"] if key in {"application_revision", "protocol_version"} else item)[key] = (
            value
        )
        statuses.append(item)
    expected = []
    for item in statuses:
        ticks = iter([0.0, 0.0, 2.0])
        with (
            patch("planner.installation.join.time.time_ns", return_value=now * 1_000_000),
            patch("planner.installation.join.time.monotonic", side_effect=ticks.__next__),
            patch("planner.installation.join.time.sleep"),
            patch(
                "planner.installation.join.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, json.dumps(item), ""),
            ),
        ):
            try:
                wait_for_lease(tmp_path / "unused-python", tmp_path, revision, timeout=1)
                expected.append(True)
            except ValueError:
                expected.append(False)
    script = """
import {accepted_lease} from './server/environments/linux_executor/logic/lease.ts';
const [statuses,revision,now]=JSON.parse(process.argv[1]);
console.log(JSON.stringify(statuses.map(status=>accepted_lease(status,revision,now))));
"""
    actual = json.loads(
        subprocess.check_output(
            ["node", "--input-type=module", "-e", script, json.dumps([statuses, revision, now])],
            cwd=ROOT,
            text=True,
        )
    )
    assert actual == expected
    secret = "x" * 43
    private = dict(
        archive=str(tmp_path / "unused"),
        archive_sha256="b" * 64,
        archive_size=1,
        revision=revision,
        home_url="https://home.example",
        machine_id="ad1f66de-bcaa-45b7-918a-8d0e7a018ae1",
        credential=secret,
        root=str(tmp_path),
        home=str(tmp_path),
        node_executable="/usr/bin/node",
        isolated_providers=False,
    )
    command = ["node", str(ROOT / "server/environments/linux_executor/join_main.ts")]
    result = subprocess.run(
        command, input=json.dumps(private), capture_output=True, text=True, cwd=ROOT, timeout=10
    )
    assert result.returncode == 1 and "installation root already exists" in result.stderr
    assert result.stdout == "" and secret not in result.stderr and secret not in " ".join(command)
    write_report(
        request,
        "linux-join",
        dict(
            equal=actual == expected,
            python=expected,
            typescript=actual,
            private_stdin_refusal=result.returncode,
            seconds=time.perf_counter() - began,
        ),
    )
