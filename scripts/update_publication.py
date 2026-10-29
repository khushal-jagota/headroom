"""Publication-only helpers. All executable proofs consume the sealed archive."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from planner.environments.app import AppManifest
from planner.environments.deployment_gate import smoke_candidate
from planner.environments.runtime_contracts import parse_optional_runtime_contract
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


def validate_target_proof(
    gate: Path,
    manifest: AppManifest,
    requested: str,
    archive_digest: str,
) -> dict[str, object]:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.runtime_target_root import (
        TARGET_IMAGE_DIGEST,
        TARGET_ROOT_INVENTORY_SHA256,
        inventory_digest,
    )

    contract = manifest.runtime_contract
    if contract is None:
        raise ValueError("publication requires a runtime contract")
    proof = json.loads((gate / "target/target-proof.json").read_text())
    root = json.loads((gate / "target/target-root-inventory.json").read_text())
    native_operations = proof.get("native_operations") if isinstance(proof, dict) else None
    expected = {
        "passed": True,
        "requested_sha": requested,
        "artifact_digest": manifest.artifact_digest,
        "archive_sha256": archive_digest,
        "contract_sha256": contract.digest,
        "target_image_digest": TARGET_IMAGE_DIGEST,
        "root_inventory_sha256": TARGET_ROOT_INVENTORY_SHA256,
        "relocation": True,
        "health_assets_api": True,
        "forbidden_dependencies": [],
    }
    if (
        not isinstance(proof, dict)
        or any(proof.get(key) != value for key, value in expected.items())
        or root.get("image_digest") != TARGET_IMAGE_DIGEST
        or root.get("root_inventory_sha256") != expected["root_inventory_sha256"]
        or inventory_digest(root["inventory"]) != TARGET_ROOT_INVENTORY_SHA256
        or not isinstance(native_operations, dict)
        or native_operations.get("native") != "passed"
        or native_operations.get("python") != contract.python_version
        or not native_operations.get("libraries")
        or not proof.get("admitted_tool_commands")
        or not proof.get("native")
        or not proof.get("cpuinfo")
        or not proof.get("mountinfo")
        or not isinstance(proof.get("duration_seconds"), (int, float))
        or proof["duration_seconds"] <= 0
    ):
        raise ValueError(
            "target proof differs from publication identity or lacks execution evidence"
        )
    return proof


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
            (gate / "smoke.json").write_text(
                json.dumps(
                    {
                        "requested_sha": requested,
                        "artifact_digest": manifest.artifact_digest,
                        "archive_sha256": digest,
                        "passed": True,
                    }
                )
                + "\n"
            )
        else:
            (gate / "evidence.json").write_text(
                json.dumps(
                    {
                        "requested_sha": requested,
                        "artifact_digest": manifest.artifact_digest,
                        "publication_run_id": int(os.environ["GITHUB_RUN_ID"]),
                        "archive_sha256": digest,
                    }
                )
                + "\n"
            )
            with Path(os.environ["GITHUB_ENV"]).open("a") as stream:
                stream.write(f"PANELS_CANDIDATE_APP={destination}\n")
    else:
        # manifest.json is a copy beside the archive, not an executable tree.
        value = json.loads((gate / "manifest.json").read_text())
        manifest = AppManifest(
            value["app_sha"],
            value["source_digest"],
            value["artifact_digest"],
            parse_optional_runtime_contract(value.get("runtime_contract")),
        )
        for name in ("smoke.json", "replacement-proof.json"):
            if not (gate / name).is_file():
                raise ValueError(f"publication proof is missing: {name}")
        digest = hash_archive(archive)
        if digest != (gate / "archive-sha256.txt").read_text().strip():
            raise ValueError("sealed archive changed before publication")
        smoke = json.loads((gate / "smoke.json").read_text())
        if smoke != {
            "requested_sha": requested,
            "artifact_digest": manifest.artifact_digest,
            "archive_sha256": digest,
            "passed": True,
        }:
            raise ValueError("smoke proof differs from publication identity")
        target = validate_target_proof(gate, manifest, requested, digest)
        receipt = {
            "publication_run_id": int(os.environ["GITHUB_RUN_ID"]),
            "artifact_id": int(os.environ["PANELS_ARTIFACT_ID"]),
            "manifest": asdict(manifest),
            "archive_sha256": digest,
            "target_proof": {
                key: target[key]
                for key in (
                    "contract_sha256",
                    "target_image_digest",
                    "root_inventory_sha256",
                )
            },
            "schema_floor": SUPPORTED_UPDATE_SCHEMA_FLOOR,
            "expires_at": (
                datetime.now(UTC) + timedelta(days=UPDATE_ARTIFACT_RETENTION_DAYS)
            ).isoformat(),
        }
        (gate / PUBLICATION_RECEIPT_NAME).write_text(json.dumps(receipt, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
