"""Compose and prove a sealed Mac home from the separately proved native runtime.

Only disposable hosted Macs run this builder. Installation never builds dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_mac_runtime import audit_native_dependencies, run  # noqa: E402

from planner.environments.app import build_exported_app, digest_app_artifact  # noqa: E402
from planner.environments.mac_runtime_configuration import MAC_PYTHON_VERSION  # noqa: E402
from planner.environments.runtime_admission import admit_runtime, probe_environment  # noqa: E402
from planner.environments.update_artifacts import (  # noqa: E402
    APP_ARCHIVE_NAME,
    create_app_archive,
    extract_app_archive,
    hash_archive,
)
from planner.executor.update_configuration import MAC_RUNTIME_ARCHIVE_NAME  # noqa: E402
from planner.platform_runtime.darwin.runtime_contracts import DarwinRuntimeContract  # noqa: E402
from planner.platform_runtime.linux.runtime_contracts import (  # noqa: E402
    canonical_json_bytes,
    runtime_bytes_digest,
)

RECEIPT_NAME = "mac-home-publication.json"


def prove_home(archive: Path, work: Path, revision: str, digest: str) -> dict[str, object]:
    """Prove the sealed bytes after relocation and without a build tree."""
    root = work / "relocated home with spaces"
    manifest = extract_app_archive(archive, root, digest, revision)
    admit_runtime(root, manifest)
    started = time.monotonic()
    state = work / "home-state"
    state.mkdir()
    environment = probe_environment(state)
    run(
        [
            str(root / ".venv/bin/python"),
            "-I",
            "-B",
            "-c",
            "import sys; from pathlib import Path; "
            "from planner.environments.candidate_smoke import smoke_candidate; "
            "smoke_candidate(Path(sys.argv[1]), sys.argv[2], timeout=90)",
            str(root),
            revision,
        ],
        cwd=state,
        environment=environment,
    )
    contract = manifest.runtime_contract
    if contract is None:
        raise ValueError("Mac home proof requires a runtime contract")
    return {
        "passed": True,
        "application_revision": revision,
        "relocation": True,
        "health_assets_api": True,
        "contract_sha256": contract.digest,
        "archive_sha256": digest,
        "duration_seconds": time.monotonic() - started,
    }


def build(source: Path, executor: Path, output: Path, revision: str, run_id: int) -> None:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise ValueError("Mac home publication requires a native arm64 Mac")
    output.mkdir(parents=True, exist_ok=True)
    receipt = json.loads((executor / "mac-runtime-publication.json").read_text())
    seed = executor / MAC_RUNTIME_ARCHIVE_NAME
    if (
        receipt.get("application_revision") != revision
        or receipt.get("publication_run_id") != run_id
        or receipt.get("archive_sha256") != hash_archive(seed)
        or receipt.get("proof", {}).get("passed") is not True
    ):
        raise ValueError("Mac home seed differs from the proved native executor runtime")
    with tempfile.TemporaryDirectory(prefix="panels-mac-home-build-") as temporary:
        work = Path(temporary)
        app = work / "app"
        manifest = build_exported_app(source, requested_sha=revision, candidate_app=app)
        payload = work / "seed"
        with tarfile.open(seed, "r:gz") as packed:
            packed.extractall(payload, filter="data")
        shutil.move(payload / "python", app / ".venv")
        if not (app / ".venv/bin/python").exists():
            (app / ".venv/bin/python").symlink_to("python3")
        shutil.move(payload / "agent_backends", app / "agent_backends-runtime")
        shutil.rmtree(app / "agent_backends")
        (app / "agent_backends-runtime").rename(app / "agent_backends")
        environment = {"PATH": os.environ["PATH"], "HOME": str(work), "TMPDIR": str(work)}
        run(
            [str(source / "scripts/dev"), "npm", "--prefix", "web", "ci"],
            cwd=source,
            environment=environment,
        )
        run(
            [str(source / "scripts/dev"), "npm", "--prefix", "web", "run", "build"],
            cwd=source,
            environment=environment,
        )
        shutil.rmtree(app / "web/dist")
        shutil.copytree(source / "web/dist", app / "web/dist")
        native = audit_native_dependencies(app, environment, python_relative=".venv/bin/python3")
        runtime = app / "runtime"
        runtime.mkdir()
        inventory = canonical_json_bytes(native)
        (runtime / "inventory.json").write_bytes(inventory)
        contract = DarwinRuntimeContract(MAC_PYTHON_VERSION, runtime_bytes_digest(inventory))
        (runtime / "contract.json").write_bytes(canonical_json_bytes(contract.as_dict()))
        manifest = replace(
            manifest, runtime_contract=contract, artifact_digest=digest_app_artifact(app)
        )
        (app / "manifest.json").write_text(json.dumps(manifest.as_dict(), sort_keys=True) + "\n")
        archive = output / APP_ARCHIVE_NAME
        digest = create_app_archive(app, archive)
        # The build app disappears before any executable proof uses the sealed bytes.
        app.rename(work / "inaccessible-build-app")
        proof = prove_home(archive, work, revision, digest)
        if hash_archive(archive) != digest:
            raise ValueError("Sealed Mac home changed during proof")
        (output / RECEIPT_NAME).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "publication_run_id": run_id,
                    "application_revision": revision,
                    "manifest": manifest.as_dict(),
                    "archive_sha256": digest,
                    "archive_size": archive.stat().st_size,
                    "native": native,
                    "proof": proof,
                },
                sort_keys=True,
            )
            + "\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--executor-publication", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    arguments = parser.parse_args()
    build(
        arguments.source.resolve(),
        arguments.executor_publication.resolve(),
        arguments.output.resolve(),
        arguments.revision,
        arguments.run_id,
    )


if __name__ == "__main__":
    main()
