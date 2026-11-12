"""Small valid app trees and publication receipts shared by the update tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from planner.environments.app import AppManifest, digest_app_artifact, digest_app_source
from planner.environments.runtime_contracts import (
    RuntimeContract,
    runtime_bytes_digest,
)
from planner.environments.update_contracts import PublishedMacUpdate, PublishedUpdate

SHA_A = "a" * 40
SHA_B = "b" * 40


def build_app(root: Path, sha: str, marker: str) -> Path:
    """A tree that passes manifest, runtime-tree and runtime-contract validation."""
    root.mkdir(parents=True)
    (root / marker).write_text(marker, encoding="utf-8")
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    planner_file = root / "src" / "planner" / "__init__.py"
    planner_file.parent.mkdir(parents=True)
    planner_file.write_text("", encoding="utf-8")
    python.write_text(
        "#!/bin/sh\n"
        'root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)\n'
        'printf "%s\\n" "$root/src/planner/__init__.py"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    (root / "bin").mkdir()
    for name in ("panels-launcher", "panels"):
        (root / "bin" / name).write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "bin" / name).chmod(0o755)
    (root / "web" / "dist").mkdir(parents=True)
    (root / "web" / "dist" / "index.html").write_text("ok", encoding="utf-8")
    for package in ("@anthropic-ai/claude-code", "@openai/codex"):
        metadata = root / "agent_backends/node_modules" / package / "package.json"
        metadata.parent.mkdir(parents=True)
        metadata.write_text('{"version": "test"}\n', encoding="utf-8")
    inventory = b'{"native_files": []}'
    contract = RuntimeContract("3.12.12", runtime_bytes_digest(inventory))
    (root / "runtime").mkdir()
    (root / "runtime/inventory.json").write_bytes(inventory)
    (root / "runtime/contract.json").write_text(json.dumps(contract.as_dict()))
    (root / "manifest.json").write_text(
        json.dumps({
            "format": "panels-app-v1",
            "app_sha": sha,
            "source_digest": digest_app_source(root),
            "artifact_digest": digest_app_artifact(root),
            "runtime_contract": contract.as_dict(),
        }),
        encoding="utf-8",
    )
    return root


def rewrite_artifact_digest(app: Path) -> None:
    manifest_path = app / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_digest"] = digest_app_artifact(app)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def release(
    manifest: AppManifest | None = None,
    *,
    archive_sha256: str = "e" * 64,
    mac: PublishedMacUpdate | None = None,
) -> PublishedUpdate:
    return PublishedUpdate(
        1,
        2,
        3,
        manifest or AppManifest(SHA_B, "c" * 64, "d" * 64, RuntimeContract("3.12.12", "f" * 64)),
        archive_sha256,
        "video_upload_ledger",
        (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        mac,
    )
