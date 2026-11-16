"""Unreadable deploy evidence has the same factual account as Python at startup.

Reuse the existing recovery companion and write door. No server, provider,
large file, or connected host is needed for the manifest-reader boundary.
"""

from __future__ import annotations

import json
import subprocess
import time
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.support.synthetic_homes import (
    create_synthetic_homes,
    synthetic_home_environment,
)

from planner.conversation.deploy_interruptions import data
from planner.conversation.deploy_interruptions.files import (
    manifest_root,
    read_manifests,
)
from planner.conversation.usage.composition import makeUsageService
from planner.conversation.video_uploads.service import VideoUploadService
from planner.core.config import load_config
from planner.core.db import connect, create_schema

ROOT = Path(__file__).resolve().parents[3]


def test_unit19_unreadable_capture_evidence(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    paths = [tmp_path / side / "data.sqlite" for side in ("python", "typescript")]
    links = []
    for path in paths:
        path.parent.mkdir()
        with synthetic_home_environment(homes), closing(connect(str(path))) as conn:
            create_schema(conn)
        root = path.parent / "deploy-interruptions" / "release"
        root.mkdir(parents=True)
        (root / "complete.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "deployment_id": "release",
                    "capture_id": "complete",
                    "captured_at": 99,
                    "state": "captured",
                    "detail": "Synthetic empty capture.",
                    "turns": [],
                }
            )
        )
        (root / "older.json").write_text('{"version":0}')
        (root / "partial.json").write_text('{"version":1,"deployment_id":')
        (root / "encoding.json").write_bytes(b"\xff")
        (root / "linked.json").symlink_to(root / "complete.json")
        (root / "directory.json").mkdir()
        (root / ".capture-temporary").write_text("{")
        link = path.parent / "db-link" / "alias.sqlite"
        link.parent.mkdir()
        link.symlink_to(path)
        links.append(link)
    with synthetic_home_environment(homes):
        manifests = read_manifests(links[0])
        usage = makeUsageService(
            load_config(
                None,
                env={
                    "PLAN_DB_PATH": str(links[0]),
                    "PLAN_TEST_MODE": "1",
                },
            ),
            now=lambda: datetime(2026, 11, 12, tzinfo=UTC),
        )
        video = VideoUploadService(links[0])
        folders = {
            "usage": str(usage.stateDir.relative_to(paths[0].parent)),
            "deploy": str(manifest_root(links[0]).relative_to(paths[0].parent)),
            "video": str(video.root.relative_to(paths[0].parent)),
        }
        with closing(connect(str(links[0]))) as conn:
            data.reconcile(conn, manifests, 100)
            expected: dict[str, Any] = {
                "folders": folders,
                "manifests": json.loads(
                    json.dumps([asdict(manifest) for manifest in manifests])
                ),
                "accounts": data.read_summary(conn),
                "rows": [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM deploy_interruption_accounts ORDER BY capture_id"
                    )
                ],
                "eligible": data.select_recoveries(conn),
            }
        actual = json.loads(
            subprocess.check_output(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    "import {pathToFileURL} from 'node:url'; "
                    "const {inspect_capture_evidence}=await "
                    "import(pathToFileURL(process.argv[2]).href); "
                    "process.stdout.write(JSON.stringify("
                    "inspect_capture_evidence(process.argv[1],process.argv[3])));",
                    str(links[1]),
                    str(
                        ROOT
                        / "server/conversation/deploy_interruptions/testing/direct.ts"
                    ),
                    str(paths[1].parent),
                ],
                cwd=ROOT,
                text=True,
            )
        )
    assert len(expected["manifests"]) == 6
    assert sum(value["state"] == "unavailable" for value in expected["manifests"]) == 5
    assert expected["eligible"] == {}
    assert folders == {
        "usage": "usage",
        "deploy": "deploy-interruptions",
        "video": "db-link/files/video-uploads",
    }
    report = {
        "kind": "direct",
        "case": "unit19-unreadable-capture",
        "equal": actual == expected,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "unit19-unreadable-capture.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert actual == expected
