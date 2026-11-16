"""Synthetic Mac target fixture for the hosted cutover, never a Mac executor.

The tiny archive satisfies the Mac archive inspection contract. Its executable
entries are inert fixture bytes: this proof serves a target, not a runnable Mac.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import tarfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

MACHINE_ID = "a751d573-9750-4453-91ee-619d7e19451c"
CREDENTIAL = "synthetic-cutover-mac-credential-not-an-owner-secret"
LOGIN = "cutover@example.invalid"


def _current(current: Path) -> Path:
    root = Path(os.environ["CUTOVER_ROOT"]).resolve()
    resolved = current.resolve()
    if resolved != root / "current":
        raise ValueError("Mac fixture requires the disposable cutover current root")
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ValueError("Mac fixture requires a hosted rehearsal")
    return resolved


def _base(health_url: str) -> str:
    url = urllib.parse.urlsplit(health_url)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port:
        raise ValueError("Mac fixture accepts only an explicit loopback HTTP port")
    return urllib.parse.urlunsplit((url.scheme, url.netloc, "", "", ""))


def prepare(current: Path, health_url: str, v2_sha: str) -> dict[str, Any]:
    """Register disposable Darwin authority and cache its inspection-valid archive."""
    current = _current(current)
    _base(health_url)
    if len(v2_sha) != 40 or any(c not in "0123456789abcdef" for c in v2_sha):
        raise ValueError("Mac fixture requires a full source revision")
    with sqlite3.connect(current / "data/planner.db") as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO machines(machine_id,name,icon,color,is_home) "
            "VALUES(?,?,'laptop','orchid',0)",
            (MACHINE_ID, "Synthetic cutover Mac"),
        )
        conn.execute(
            "INSERT INTO machine_executor_credentials"
            "(machine_id,credential_sha256,issued_at_ms,legacy_closed_at_ms,platform) "
            "VALUES(?,?,?,?, 'darwin')",
            (MACHINE_ID, hashlib.sha256(CREDENTIAL.encode()).hexdigest(), 1, 1),
        )
    buffer = io.BytesIO()
    manifest = {
        "schema_version": 1,
        "application_revision": v2_sha,
        "platform": "darwin",
        "architecture": "arm64",
    }
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content, mode in (
            ("mac-runtime.json", json.dumps(manifest).encode(), 0o644),
            ("node/bin/node", b"Synthetic fixture: never execute.\n", 0o755),
            ("server/executor/mac/entrypoint.ts", b"// Synthetic target only.\n", 0o644),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = mode
            archive.addfile(member, io.BytesIO(content))
    content = buffer.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    directory = current / "data/mac-updates/archives"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{digest}.tar.gz").write_bytes(content)
    metadata = {"artifact_id": 991, "archive_sha256": digest, "archive_size": len(content)}
    (current.parent / "synthetic-mac.json").write_text(json.dumps(metadata))
    return metadata


def check(current: Path, health_url: str, v2_sha: str) -> dict[str, Any]:
    """Read a real authenticated target and download it from the TypeScript server."""
    current = _current(current)
    base = _base(health_url)
    metadata = json.loads((current.parent / "synthetic-mac.json").read_text())
    headers = {
        "Tailscale-User-Login": LOGIN,
        "X-Panels-Executor-ID": MACHINE_ID,
        "X-Panels-Executor-Credential": CREDENTIAL,
        "X-Panels-Executor-Platform": "darwin",
    }
    deadline = time.monotonic() + 30
    while True:
        request = urllib.request.Request(base + "/api/executor/update", headers=headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            answer = json.load(response)
        target = answer.get("target")
        if target is not None:
            break
        if time.monotonic() >= deadline:
            raise AssertionError("TypeScript did not expose the synthetic Mac target")
        time.sleep(0.25)
    assert target["application_revision"] == v2_sha, target
    assert target["activate"] is True, target
    assert target["archive_sha256"] == metadata["archive_sha256"], target
    assert int(target["archive_size"]) == metadata["archive_size"], target
    assert target["request_id"] == "cutover-v2.mac." + MACHINE_ID, target
    path = target["archive_path"]
    assert path == "/api/executor/update/" + target["request_id"] + "/archive", target
    with urllib.request.urlopen(
        urllib.request.Request(base + path, headers=headers), timeout=5
    ) as response:
        content = response.read(metadata["archive_size"] + 1)
    assert len(content) == metadata["archive_size"]
    assert hashlib.sha256(content).hexdigest() == metadata["archive_sha256"]
    return {
        "check": answer,
        "served_archive_sha256": metadata["archive_sha256"],
        "machine_id": MACHINE_ID,
        "boundary": "Synthetic Darwin target and archive HTTP handoff; no Mac runtime executes.",
    }
