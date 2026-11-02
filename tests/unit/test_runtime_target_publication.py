"""Target proof must bind publication to real execution and exact inputs."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from scripts import runtime_target_root
from scripts.runtime_target_root import TARGET_IMAGE_DIGEST, apply_layer, inventory_digest
from scripts.update_publication import validate_target_proof

from planner.environments.app import AppManifest
from planner.environments.runtime_contracts import RuntimeContract


@pytest.fixture(autouse=True)
def fixture_target_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_target_root, "TARGET_ROOT_INVENTORY_SHA256", inventory_digest([]))


def _proof(gate: Path) -> tuple[AppManifest, dict[str, object]]:
    contract = RuntimeContract(python_version="3.12.15", inventory_sha256="d" * 64)
    manifest = AppManifest("a" * 40, "b" * 64, "c" * 64, contract)
    root: dict[str, object] = {
        "image_digest": TARGET_IMAGE_DIGEST,
        "inventory": [],
        "root_inventory_sha256": inventory_digest([]),
    }
    proof: dict[str, object] = {
        "passed": True,
        "requested_sha": manifest.app_sha,
        "artifact_digest": manifest.artifact_digest,
        "archive_sha256": "e" * 64,
        "contract_sha256": contract.digest,
        "target_image_digest": TARGET_IMAGE_DIGEST,
        "root_inventory_sha256": root["root_inventory_sha256"],
        "relocation": True,
        "health_assets_api": True,
        "forbidden_dependencies": [],
        "native": [{"path": "python", "loader": "libc.so.6"}],
        "native_operations": {
            "native": "passed",
            "python": contract.python_version,
            "libraries": ["/work/app/.venv/lib/libicuuc.so.74"],
        },
        "admitted_tool_commands": [["/work/app/bin/claude", "--version"]],
        "cpuinfo": "flags: sse2",
        "mountinfo": "isolated",
        "duration_seconds": 1.2,
    }
    target = gate / "target"
    target.mkdir()
    (target / "target-root-inventory.json").write_text(json.dumps(root))
    (target / "target-proof.json").write_text(json.dumps(proof))
    return manifest, proof


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("passed", False),
        ("archive_sha256", "f" * 64),
        ("artifact_digest", "f" * 64),
        ("requested_sha", "f" * 40),
        ("contract_sha256", "f" * 64),
        ("target_image_digest", "sha256:" + "f" * 64),
        ("root_inventory_sha256", "f" * 64),
        ("relocation", False),
        ("health_assets_api", False),
        ("forbidden_dependencies", ["/usr/bin/python3.12"]),
        ("native", []),
        ("native_operations", None),
        ("native_operations", {"native": "failed"}),
        ("native_operations", {"native": "passed", "python": "3.12.15", "libraries": []}),
        ("admitted_tool_commands", []),
        ("duration_seconds", 0),
    ],
)
def test_publication_refuses_incomplete_or_foreign_target_proof(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    manifest, proof = _proof(tmp_path)
    assert validate_target_proof(tmp_path, manifest, manifest.app_sha, "e" * 64) == proof
    proof[field] = replacement
    (tmp_path / "target/target-proof.json").write_text(json.dumps(proof))
    with pytest.raises(ValueError, match="target proof"):
        validate_target_proof(tmp_path, manifest, manifest.app_sha, "e" * 64)


def test_publication_requires_target_receipt_and_final_root_inventory(tmp_path: Path) -> None:
    manifest, _ = _proof(tmp_path)
    root_path = tmp_path / "target/target-root-inventory.json"
    root = json.loads(root_path.read_text())
    root["inventory"] = [{"path": "unexpected-builder-library", "sha256": "f" * 64}]
    root_path.write_text(json.dumps(root))
    with pytest.raises(ValueError, match="target proof"):
        validate_target_proof(tmp_path, manifest, manifest.app_sha, "e" * 64)
    (tmp_path / "target/target-proof.json").unlink()
    with pytest.raises(FileNotFoundError):
        validate_target_proof(tmp_path, manifest, manifest.app_sha, "e" * 64)


def test_oci_extraction_contains_absolute_links_and_refuses_traversal(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    layer = tmp_path / "layer.tar"
    with tarfile.open(layer, "w") as archive:
        directory = tarfile.TarInfo(".")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        archive.addfile(directory)
        link = tarfile.TarInfo("bin")
        link.type = tarfile.SYMTYPE
        link.linkname = "/usr/bin"
        archive.addfile(link)
        executable = tarfile.TarInfo("usr/bin/example")
        executable.size = 2
        archive.addfile(executable, io.BytesIO(b"ok"))
    apply_layer(layer, root)
    assert (root / "bin/example").read_bytes() == b"ok"
    assert (root / "bin").resolve() == root / "usr/bin"
    with tarfile.open(layer, "w") as archive:
        bad = tarfile.TarInfo("../escape")
        bad.size = 2
        archive.addfile(bad, io.BytesIO(b"no"))
    with pytest.raises(ValueError, match="unsafe OCI"):
        apply_layer(layer, root)
    assert not (tmp_path / "escape").exists()
