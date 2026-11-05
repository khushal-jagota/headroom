"""Publication-only helpers. All executable proofs consume the sealed archive.

The receipt runs in the final publication job, which has the proofs but not the
archive, and imports only the standard library and this checkout's source.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tarfile
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from planner.environments.app import AppManifest
from planner.environments.mac_runtime_configuration import (
    MAC_PYTHON_SHA256,
    MAC_PYTHON_VERSION,
    MAC_RUNTIME_RECEIPT_NAME,
)
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
    PublishedMacUpdate,
)
from planner.executor.update_configuration import (
    MAC_RUNTIME_ARCHIVE_NAME,
    MAC_RUNTIME_MANIFEST_NAME,
    MAX_MAC_RUNTIME_ARCHIVE_BYTES,
    MAX_MAC_RUNTIME_MANIFEST_BYTES,
)

# Keep the historical import available to archive callers and focused tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.publication_checks import full_selection as full_selection  # noqa: E402


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


def validate_mac_publication(
    directory: Path,
    *,
    requested: str,
    run_id: int,
    artifact_id: int,
    build_result: str,
) -> PublishedMacUpdate:
    """Bind the native build, sealed archive and current workflow artifact together."""
    if build_result != "success" or type(artifact_id) is not int or artifact_id < 1:
        raise ValueError("Mac publication requires a successful native build and artifact")
    value = json.loads((directory / MAC_RUNTIME_RECEIPT_NAME).read_text())
    archive = directory / MAC_RUNTIME_ARCHIVE_NAME
    digest = hash_archive(archive)
    size = archive.stat().st_size
    proof = value.get("proof", {})
    operations = proof.get("native_operations", {}) if isinstance(proof, dict) else {}
    if (
        value.get("schema_version") != 1
        or value.get("application_revision") != requested
        or re.fullmatch(r"[0-9a-f]{40}", requested) is None
        or value.get("publication_run_id") != run_id
        or value.get("archive_sha256") != digest
        or value.get("archive_size") != size
        or not 0 < size <= MAX_MAC_RUNTIME_ARCHIVE_BYTES
        or value.get("python_archive_sha256") != MAC_PYTHON_SHA256
        or value.get("python_version") != MAC_PYTHON_VERSION
        or not isinstance(proof, dict)
        or proof.get("passed") is not True
        or proof.get("application_revision") != requested
        or proof.get("relocation") is not True
        or proof.get("cli_help") is not True
        or proof.get("backend_entrypoints") is not True
        or proof.get("forbidden_dependencies") != []
        or not proof.get("native")
        or not isinstance(operations, dict)
        or operations.get("native") != "passed"
        or operations.get("python") != MAC_PYTHON_VERSION
        or operations.get("platform") != "darwin"
        or operations.get("architecture") != "arm64"
        or any(
            operations.get(name) is not True
            for name in (
                "cli_import",
                "mac_service_import",
                "sqlite",
                "aiohttp",
                "cryptography",
                "icu",
            )
        )
    ):
        raise ValueError("Mac publication differs from the source or lacks native execution proof")
    with tarfile.open(archive, "r:gz") as packed:
        member = packed.getmember(MAC_RUNTIME_MANIFEST_NAME)
        if not member.isfile() or member.size > MAX_MAC_RUNTIME_MANIFEST_BYTES:
            raise ValueError("Mac runtime manifest is invalid")
        stream = packed.extractfile(member)
        if stream is None or json.load(stream) != {
            "schema_version": 1,
            "application_revision": requested,
            "platform": "darwin",
            "architecture": "arm64",
        }:
            raise ValueError("Mac runtime manifest differs from publication source")
    return PublishedMacUpdate(artifact_id, digest, size)


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument(
        "operation",
        choices=("selection", "seal", "smoke", "rehearsal", "unchanged", "receipt"),
    )
    args = parser.parse_args()
    gate = Path(os.environ["PANELS_GATE_DIR"])
    archive = gate / APP_ARCHIVE_NAME
    requested = os.environ["PANELS_REQUESTED_SHA"]
    if args.operation == "selection":
        source = Path(os.environ["PANELS_CANDIDATE_SOURCE"])
        (gate / "selection.json").write_text(json.dumps(full_selection(source)) + "\n")
    elif args.operation == "seal":
        candidate = Path(os.environ["PANELS_CANDIDATE_APP"])
        digest = create_app_archive(candidate, archive)
        (gate / "archive-sha256.txt").write_text(digest + "\n")
        (gate / "manifest.json").write_bytes((candidate / "manifest.json").read_bytes())
    elif args.operation in {"smoke", "rehearsal"}:
        digest = (gate / "archive-sha256.txt").read_text().strip()
        candidate = Path(os.environ["PANELS_CANDIDATE_APP"])
        destination = candidate.with_name(args.operation + "-candidate")
        manifest = extract_app_archive(archive, destination, digest, requested)
        if args.operation == "smoke":
            from planner.environments.deployment_gate import smoke_candidate

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
    elif args.operation == "unchanged":
        # The build job runs this after every proof, just before it uploads the archive.
        if hash_archive(archive) != (gate / "archive-sha256.txt").read_text().strip():
            raise ValueError("sealed archive changed before publication")
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
        digest = (gate / "archive-sha256.txt").read_text().strip()
        smoke = json.loads((gate / "smoke.json").read_text())
        if smoke != {
            "requested_sha": requested,
            "artifact_digest": manifest.artifact_digest,
            "archive_sha256": digest,
            "passed": True,
        }:
            raise ValueError("smoke proof differs from publication identity")
        target = validate_target_proof(gate, manifest, requested, digest)
        mac = validate_mac_publication(
            gate / "mac",
            requested=requested,
            run_id=int(os.environ["GITHUB_RUN_ID"]),
            artifact_id=int(os.environ["PANELS_MAC_ARTIFACT_ID"]),
            build_result=os.environ["PANELS_MAC_BUILD_RESULT"],
        )
        receipt = {
            "mac": asdict(mac),
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
