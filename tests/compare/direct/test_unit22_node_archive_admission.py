"""Node package admission uses the existing private-file Mac service fixture."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
import time
import zipfile
from dataclasses import asdict, replace
from io import BytesIO
from pathlib import Path

import pytest
from tests.compare.direct.test_unit21_backups import _report
from tests.compare.direct.test_unit21_mac_updates import COMPANION, ROOT
from tests.compare.direct.test_unit21_updates import _environment
from tests.unit.test_mac_updates import MacProvider


def test_unit22_node_archive_admission(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """Gap: Python archives cannot prove complete Node payload admission or false markers.

    Reuse the owned inspection child and service fixture, without a listener or provider.
    """
    began = time.perf_counter()
    publication = MacProvider().publish()
    assert publication.mac is not None
    inspections = {}
    for name in ("complete", "no-node", "no-entry", "not-executable", "linked-entry"):
        archive = BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as packed:
            members = [
                (
                    "mac-runtime.json",
                    json.dumps(
                        {
                            "schema_version": 1,
                            "application_revision": "b" * 40,
                            "platform": "darwin",
                            "architecture": "arm64",
                        }
                    ).encode(),
                )
            ]
            if name != "no-node":
                members.append(("node/bin/node", b"synthetic executable"))
            if name != "no-entry":
                members.append(("server/executor/mac/entrypoint.ts", b"synthetic source"))
            for path, content in members:
                item = tarfile.TarInfo(path)
                item.size, item.mode = len(content), 0o755
                if path == "node/bin/node" and name == "not-executable":
                    item.mode = 0o644
                if path.endswith("entrypoint.ts") and name == "linked-entry":
                    item.type, item.linkname, item.size = tarfile.SYMTYPE, "/outside", 0
                packed.addfile(item, BytesIO(content))
        payload = archive.getvalue()
        artifact = tmp_path / (name + ".zip")
        with zipfile.ZipFile(artifact, "w") as zipped:
            zipped.writestr("panels-mac-runtime.tar.gz", payload)
        release = replace(
            publication,
            mac=replace(
                publication.mac,
                archive_sha256=hashlib.sha256(payload).hexdigest(),
                archive_size=len(payload),
            ),
        )
        inspections[name] = {"release": asdict(release), "artifact": str(artifact)}
    spec = tmp_path / "request.json"
    spec.write_text(
        json.dumps(
            {
                "root": str(tmp_path),
                "releases": {},
                "attempts": {},
                "artifacts": {},
                "operations": [],
                "inspections": inspections,
            }
        )
    )
    script = (
        "import{readFileSync}from'node:fs';const m=await import(process.argv[1]);"
        "console.log(JSON.stringify(await m.mac_update_sequence("
        "JSON.parse(readFileSync(process.argv[2],'utf8')))));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, COMPANION.as_uri(), str(spec)],
        cwd=ROOT,
        env=_environment(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual["inspections"]["complete"]["outcome"] == {"result": None}
    for name in inspections.keys() - {"complete"}:
        assert actual["inspections"][name]["outcome"] == {
            "error": "UpdateConflict",
            "message": "The Mac runtime does not match its source and platform.",
        }, name
    assert actual["children"]["spawned"] == actual["children"]["closed"] == len(inspections)
    _report(
        request,
        "unit22-node-admission.json",
        began,
        outcomes=actual,
        risk="Node payload markers must not admit incomplete/nonexecutable/link packages",
        reused_host="unit21 mac_update_sequence",
        actual_children=actual["children"],
    )
