"""Publication-only helpers. All executable proofs consume the sealed archive."""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from planner.environments.app import AppManifest
from planner.environments.deployment_gate import smoke_candidate
from planner.environments.update_artifacts import (
    APP_ARCHIVE_NAME,
    PUBLICATION_RECEIPT_NAME,
    create_app_archive,
    extract_app_archive,
    hash_archive,
)
from planner.environments.update_contracts import (
    SUPPORTED_UPDATE_SCHEMA_FLOOR,
    UPDATE_ARTIFACT_RETENTION_DAYS,
)


def full_selection(source: Path) -> dict[str, object]:
    """Use every current suite entry, without diff-based check selection."""
    def files(pattern: str) -> list[str]:
        return sorted(path.relative_to(source).as_posix() for path in source.glob(pattern))

    catalog = json.loads((source / "web/tests/browser/catalog.json").read_text())
    if catalog.get("schema") != 1:
        raise ValueError("unsupported browser catalog")
    return {
        "ruff_paths": ["."], "mypy": True, "web_check": True,
        "unit": files("tests/unit/**/test_*.py"),
        "integration": files("tests/integration/**/test_*.py"),
        "e2e": files("tests/e2e/**/test_*.py"),
        "vitest": files("web/tests/**/*.test.ts"),
        "browser": [entry["path"] for entry in catalog["entries"]],
        "browser_format": "shared-v1",
    }


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("operation", choices=("selection", "seal", "smoke", "rehearsal", "receipt"))
    args = parser.parse_args()
    gate = Path(os.environ["PANELS_GATE_DIR"])
    archive = gate / APP_ARCHIVE_NAME
    requested = os.environ["PANELS_REQUESTED_SHA"]
    candidate = Path(os.environ["PANELS_CANDIDATE_APP"])
    if args.operation == "selection":
        source = Path(os.environ["PANELS_CANDIDATE_SOURCE"])
        (gate / "selection.json").write_text(json.dumps(full_selection(source)) + "\n")
    elif args.operation == "seal":
        digest = create_app_archive(candidate, archive)
        (gate / "archive-sha256.txt").write_text(digest + "\n")
        (gate / "manifest.json").write_bytes((candidate / "manifest.json").read_bytes())
    elif args.operation in {"smoke", "rehearsal"}:
        digest = (gate / "archive-sha256.txt").read_text().strip()
        destination = candidate.with_name(args.operation + "-candidate")
        manifest = extract_app_archive(archive, destination, digest, requested)
        if args.operation == "smoke":
            smoke_candidate(destination, requested)
            (gate / "smoke.json").write_text(json.dumps({
                "requested_sha": requested, "artifact_digest": manifest.artifact_digest,
                "archive_sha256": digest, "passed": True,
            }) + "\n")
        else:
            (gate / "evidence.json").write_text(json.dumps({
                "requested_sha": requested, "artifact_digest": manifest.artifact_digest,
                "publication_run_id": int(os.environ["GITHUB_RUN_ID"]),
                "archive_sha256": digest,
            }) + "\n")
            with Path(os.environ["GITHUB_ENV"]).open("a") as stream:
                stream.write(f"PANELS_CANDIDATE_APP={destination}\n")
    else:
        # manifest.json is a copy beside the archive, not an executable tree.
        value = json.loads((gate / "manifest.json").read_text())
        manifest = AppManifest(value["app_sha"], value["source_digest"], value["artifact_digest"])
        for name in ("smoke.json", "replacement-proof.json"):
            if not (gate / name).is_file():
                raise ValueError(f"publication proof is missing: {name}")
        digest = hash_archive(archive)
        if digest != (gate / "archive-sha256.txt").read_text().strip():
            raise ValueError("sealed archive changed before publication")
        smoke = json.loads((gate / "smoke.json").read_text())
        if smoke != {
            "requested_sha": requested, "artifact_digest": manifest.artifact_digest,
            "archive_sha256": digest, "passed": True,
        }:
            raise ValueError("smoke proof differs from publication identity")
        receipt = {
            "publication_run_id": int(os.environ["GITHUB_RUN_ID"]),
            "artifact_id": int(os.environ["PANELS_ARTIFACT_ID"]),
            "manifest": asdict(manifest), "archive_sha256": digest,
            "schema_floor": SUPPORTED_UPDATE_SCHEMA_FLOOR,
            "expires_at": (datetime.now(UTC) + timedelta(
                days=UPDATE_ARTIFACT_RETENTION_DAYS
            )).isoformat(),
        }
        (gate / PUBLICATION_RECEIPT_NAME).write_text(json.dumps(receipt, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
