"""A Linux receipt must bind the executor role and exact archive, unlike a home receipt.

Reuse the publication validator without a native build or new connected host.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from scripts.update_publication import validate_linux_executor_publication

from planner.environments.update_artifacts import hash_archive
from planner.executor.update_configuration import MAC_RUNTIME_MANIFEST_NAME


def test_linux_receipt_binds_archive_role_and_native_proof(tmp_path: Path) -> None:
    revision = "a" * 40
    marker = dict(
        application_revision=revision,
        platform="linux",
        architecture="x86_64",
        role="executor",
        schema_version=1,
    )
    archive = tmp_path / "panels-linux-executor.tar.gz"
    with tarfile.open(archive, "w:gz") as packed:
        content = json.dumps(marker).encode()
        member = tarfile.TarInfo(MAC_RUNTIME_MANIFEST_NAME)
        member.size = len(content)
        packed.addfile(member, io.BytesIO(content))
    receipt = dict(
        marker,
        archive_sha256=hash_archive(archive),
        archive_size=archive.stat().st_size,
        proof={"native_startup": "passed", "relocated_archive": "passed", "executor_cli": "passed"},
    )
    path = tmp_path / "linux-executor-publication.json"
    path.write_text(json.dumps(receipt))
    result = validate_linux_executor_publication(
        tmp_path,
        requested=revision,
        artifact_id=41,
        build_result="success",
    )
    assert result.archive_sha256 == hash_archive(archive)
    receipt["role"] = "home"
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="differs"):
        validate_linux_executor_publication(
            tmp_path,
            requested=revision,
            artifact_id=41,
            build_result="success",
        )
    receipt["role"] = "executor"
    receipt["proof"] = {}
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="differs"):
        validate_linux_executor_publication(
            tmp_path,
            requested=revision,
            artifact_id=41,
            build_result="success",
        )
