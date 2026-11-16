"""Darwin selection reuses Unit21's scripted GitHub transport and real ZIP/archive files.

No service, provider CLI, candidate script, or Mac native call executes. Python's
Darwin v1 and Node's v2 contracts are the sole projected provider-answer difference.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import time
import zipfile
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.compare.direct.test_unit21_updates import _attempt, _node, _report
from tests.support.update_apps import build_app, rewrite_artifact_digest
from tests.unit.test_update_prefetch import ZipProvider
from tests.unit.test_update_prefetch import published as published  # noqa: F401

from planner.environments.app import (
    NODE_RUNTIME_FILES,
    AppManifest,
    digest_app_artifact,
    digest_app_source,
    validate_app_manifest,
)
from planner.environments.update_artifacts import create_app_archive
from planner.environments.update_contracts import PublishedUpdate
from planner.environments.update_prefetch import stage_release
from planner.environments.update_provider import GitHubUpdateProvider
from planner.platform_runtime.darwin.runtime_contracts import DarwinRuntimeContract
from planner.platform_runtime.targets import LINUX_HOME, MAC_HOME

SHA = "b" * 40
NOW = "2026-11-05T12:00:00+00:00"


def _zip(path: Path, name: str, payload: bytes) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, payload)


def _stage_fixture(root: Path, node: bool) -> tuple[Path, dict[str, Any]]:
    app = build_app(root / "app", SHA, "new")
    inventory = (app / "runtime/inventory.json").read_bytes()
    contract = DarwinRuntimeContract("3.12.12", hashlib.sha256(inventory).hexdigest()).as_dict()
    if node:
        contract = {**contract, "version": 2, "minimum_node_version": "22.18.0"}
        del contract["python_version"]
        shutil.rmtree(app / ".venv")
        # Reuse the frozen Python fixture list plus the Node-only runtime assets.
        for name in (
            *NODE_RUNTIME_FILES,
            "runtime/installer-files.json",
            "server/stalls/watchdog_main.ts",
            "server/conversation/usage/usageWorker.ts",
            "src/planner/conversation/backends/hermes_acp_extension.py",
            "src/planner/conversation/backends/hermes_model_catalog.py",
            "server/environments/release/archive_unpack_main.ts",
            "server/environments/updates/interruption_capture_main.ts",
            "server/environments/updates/restore_main.ts",
            "server/environments/updates/installer/main.ts",
        ):
            path = app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("inert structural fixture, never executed\n")
    (app / "runtime/contract.json").write_text(json.dumps(contract))
    (app / "mac-runtime.json").write_text(
        json.dumps(dict(platform="darwin", architecture="arm64", role="home"))
    )

    document = json.loads((app / "manifest.json").read_text())
    document["runtime_contract"] = contract
    document["source_digest"] = digest_app_source(app)
    document["artifact_digest"] = digest_app_artifact(app)
    (app / "manifest.json").write_text(json.dumps(document))
    archive = root / "panels-app.tar.gz"
    if node:
        with tarfile.open(archive, "w:gz") as packed:
            for path in sorted(app.rglob("*")):
                packed.add(path, arcname=path.relative_to(app).as_posix(), recursive=False)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    else:
        rewrite_artifact_digest(app)
        digest = create_app_archive(app, archive, expected_target=MAC_HOME)
        document = json.loads((app / "manifest.json").read_text())
    artifact = root / "artifact.zip"
    _zip(artifact, "panels-app.tar.gz", archive.read_bytes())
    receipt = {
        **_attempt()["release"],
        "artifact_id": 504,
        "archive_sha256": digest,
        "manifest": {key: value for key, value in document.items() if key != "format"},
    }
    return artifact, receipt


def _provider_cases(root: Path, node: bool) -> list[dict[str, Any]]:
    root.mkdir()
    contract = DarwinRuntimeContract("3.12.12", "f" * 64).as_dict()
    if node:
        contract = {**contract, "version": 2, "minimum_node_version": "22.18.0"}
        del contract["python_version"]
    baseline = {
        **_attempt()["release"],
        "publication_run_id": 501,
        "artifact_id": 502,
        "proof_artifact_id": 999,
        "expires_at": "2099-01-01T00:00:00+00:00",
        "archive_sha256": "c" * 64,
        "mac": {"artifact_id": 505, "archive_sha256": "9" * 64, "archive_size": 7},
    }
    baseline["mac_home"] = {
        "artifact_id": 504,
        "archive_sha256": "e" * 64,
        "proof_artifact_id": 888,
        "manifest": {**baseline["manifest"], "runtime_contract": contract},
    }
    rows = [
        dict(name=name, id=identity, expired=False)
        for name, identity in (
            ("panels-publication-proof", 503),
            ("panels-app", 502),
            ("panels-mac-home", 504),
            ("panels-mac-runtime", 505),
        )
    ]
    cases = []
    for name in (
        "selected-home",
        "wrong-role",
        "wrong-architecture",
        "missing-home-artifact",
        "missing-home-receipt",
        "artifact-identity",
        "nested-run-identity",
        "linux-refuses-mac",
    ):
        payload, artifacts = deepcopy(baseline), deepcopy(rows)
        if name == "wrong-role":
            payload["mac_home"]["manifest"]["runtime_contract"]["role"] = "executor"
        if name == "wrong-architecture":
            payload["mac_home"]["manifest"]["runtime_contract"]["architecture"] = "x86_64"
        if name == "missing-home-artifact":
            artifacts = [row for row in artifacts if row["name"] != "panels-mac-home"]
        if name == "missing-home-receipt":
            del payload["mac_home"]
        if name == "artifact-identity":
            payload["mac_home"]["artifact_id"] = 777
        if name == "nested-run-identity":
            payload["mac_home"]["publication_run_id"] = 777
        if name == "linux-refuses-mac":
            payload["manifest"] = deepcopy(payload["mac_home"]["manifest"])
        proof = root / (name + ".zip")
        _zip(proof, "publication.json", json.dumps(payload).encode())
        cases.append(
            dict(
                name=name,
                action="publication",
                run_id=501,
                artifact_id=504,
                target=asdict(LINUX_HOME if name == "linux-refuses-mac" else MAC_HOME),
                responses={
                    "actions/runs/501": {
                        "json": dict(
                            id=501,
                            head_branch="main",
                            event="push",
                            conclusion="success",
                            head_repository={"full_name": "owner/repository"},
                            head_sha=SHA,
                            path=".github/workflows/publish-typescript-release.yml"
                            if node
                            else ".github/workflows/deploy.yml",
                            created_at=NOW,
                        )
                    },
                    "actions/runs/501/artifacts": {"json": {"artifacts": artifacts}},
                    "actions/artifacts/503/zip": {"file": str(proof)},
                },
            )
        )
        if name == "linux-refuses-mac":
            del cases[-1]["target"]
    return cases


def _provider_reference(
    cases: list[dict[str, Any]], token: Path, monkeypatch: pytest.MonkeyPatch
) -> list[Any]:
    results = []
    real_client = httpx.Client
    for case in cases:
        seen: list[dict[str, Any]] = []

        def handle(
            request: httpx.Request, case: dict[str, Any] = case, seen: list[dict[str, Any]] = seen
        ) -> httpx.Response:
            path = request.url.path.removeprefix("/repos/owner/repository/")
            seen.append(
                dict(
                    path=path,
                    query=dict(request.url.params),
                    authorized=request.headers.get("Authorization")
                    == f"Bearer {token.read_text().strip()}",
                )
            )
            reply = case["responses"][path]
            if "file" in reply:
                return httpx.Response(200, content=Path(reply["file"]).read_bytes())
            return httpx.Response(200, json=reply["json"])

        with monkeypatch.context() as patch:
            patch.setattr(
                httpx,
                "Client",
                lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)),
            )
            provider = GitHubUpdateProvider(
                "owner/repository",
                token,
                target=(LINUX_HOME if case["name"] == "linux-refuses-mac" else MAC_HOME),
            )
            try:
                offered = provider.publication(501)
                results.append(
                    dict(
                        name=case["name"],
                        result=asdict(offered) if offered else None,
                        error=None,
                        seen=seen,
                    )
                )
            except ValueError as error:
                results.append(dict(name=case["name"], result=None, error=str(error), seen=seen))
    decoded = json.loads(json.dumps(results))
    assert isinstance(decoded, list)
    return decoded


def _prepare_stage_roots(root: Path) -> None:
    (root / "outside").mkdir(parents=True)
    (root / "outside/sentinel").write_text("foreign state")
    for name in ("mac/current", "mac/checksum", "linux/current"):
        current = root / name
        for stale in (".app-next-old", ".app-download-old"):
            (current / stale).mkdir(parents=True)
        (current / ".app-next-alias").symlink_to(root / "outside")


def test_darwin_publication_and_target_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    published: tuple[ZipProvider, PublishedUpdate],
) -> None:
    started = time.perf_counter()
    token = tmp_path / "token"
    token.write_text("synthetic-provider-credential\n")
    expected = _provider_reference(
        _provider_cases(tmp_path / "python-provider", False), token, monkeypatch
    )
    actual = _node(
        tmp_path,
        dict(
            root=str(tmp_path),
            token_file=str(token),
            cases=_provider_cases(tmp_path / "node-provider", True),
        ),
        "probe_update_provider",
    )
    projected = []
    for values in (expected, actual):
        for row in values:
            if row["result"]:
                contract = row["result"]["manifest"]["runtime_contract"]
                projected.append(deepcopy(contract))
                contract.pop("version")
                contract.pop("python_version", None)
                contract.pop("minimum_node_version", None)
    assert actual == expected
    selected = actual[0]["result"]
    assert selected["artifact_id"] == 504 and selected["proof_artifact_id"] == 503
    assert selected["archive_sha256"] == "e" * 64 and selected["mac"] is None

    mac_archive, mac_receipt = _stage_fixture(tmp_path / "python-mac", False)
    native_archive, native_receipt = _stage_fixture(tmp_path / "node-mac", True)
    linux_provider, linux_receipt = published
    assert not (tmp_path / "built/mac-runtime.json").exists()
    expected_stage = []
    stage_roots = [tmp_path / "python-stage", tmp_path / "node-stage"]
    for root in stage_roots:
        _prepare_stage_roots(root)
    for system, archive, receipt in (
        (
            "mac",
            mac_archive,
            replace(
                linux_receipt,
                artifact_id=504,
                manifest=validate_app_manifest(tmp_path / "python-mac/app/manifest.json"),
                archive_sha256=mac_receipt["archive_sha256"],
            ),
        ),
        ("linux", linux_provider.artifact, linux_receipt),
    ):
        provider = ZipProvider(archive)
        admitted: list[AppManifest] = []
        for kind in ["fresh", "reuse", "checksum"] if system == "mac" else ["fresh"]:
            current = stage_roots[0] / system / ("checksum" if kind == "checksum" else "current")
            changed = replace(receipt, archive_sha256="0" * 64) if kind == "checksum" else receipt
            returned, error = None, None
            try:

                def admit(
                    _root: Path,
                    manifest: AppManifest,
                    expected: AppManifest = receipt.manifest,
                    admitted: list[AppManifest] = admitted,
                ) -> None:
                    assert manifest == expected
                    admitted.append(manifest)

                returned = stage_release(provider, changed, current, admit=admit).name
            except ValueError as cause:
                error = str(cause)
            expected_stage.append(
                dict(
                    system=system,
                    kind=kind,
                    returned=returned,
                    error=error,
                    files=sorted(path.name for path in current.iterdir()),
                    downloads=len(provider.downloads),
                    admissions=len(admitted),
                    sentinel=(stage_roots[0] / "outside/sentinel").read_text(),
                    marker=(current / returned / "new").read_text() if returned else None,
                )
            )
    staged = _node(
        tmp_path,
        dict(
            root=str(stage_roots[1]),
            mac=dict(artifact=str(native_archive), release=native_receipt),
            linux=dict(artifact=str(linux_provider.artifact), release=asdict(linux_receipt)),
        ),
        "probe_home_target_staging",
    )
    assert staged["rows"] == expected_stage
    assert staged["spawned"] == staged["closed"] == 3
    _report(
        request,
        "darwin-publication-selection.json",
        dict(
            complete=True,
            seconds=time.perf_counter() - started,
            provider=actual,
            staging=staged,
            projected_contracts=projected,
            intentional_difference=(
                "Python Darwin v1/deploy.yml and Node v2/TypeScript workflow use different "
                "interpreter contracts. Only version/python_version/minimum_node_version "
                "are projected from provider answers. Each staging archive binds its own "
                "real checksum and native manifest, with recorded admission and no candidate "
                "execution."
            ),
            boundary=(
                "Existing Unit21 scripted GitHub/ZIP transport and archive owner. "
                "No new backend or browser host. P5 retained provider case stays unchanged."
            ),
        ),
    )


def test_mac_home_receipt_binds_archive_and_native_evidence(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Gap: provider selection cannot prove its receipt producer binds evidence bytes.

    Reuse the inert Darwin archive and existing Node RPC/archive child. This proves
    static receipt admission, with no Mac execution or extra connected host.
    """
    started = time.perf_counter()
    artifact, receipt = _stage_fixture(tmp_path / "payload", True)
    archive = artifact.parent / "panels-app.tar.gz"
    manifest = receipt["manifest"]
    identity = dict(requested_sha=SHA, archive_sha256=receipt["archive_sha256"], manifest=manifest)
    admission = dict(**identity, status="passed", seconds=0.1)
    boot = dict(
        **identity,
        health=dict(app_sha=SHA, ready=True),
        supervisor_exit=dict(code=0, signal=None),
        seconds=0.2,
    )
    build = dict(
        version=1,
        manifest=manifest,
        archive=str(archive),
        archive_sha256=receipt["archive_sha256"],
        archive_size=archive.stat().st_size,
        inventory_sha256=manifest["runtime_contract"]["inventory_sha256"],
        node_version="22.18.0",
        build_run=dict(source_sha=SHA, run_id="501", run_attempt="2"),
    )
    cases = []
    for name in (
        "valid",
        "wrong-sha",
        "wrong-attempt",
        "archive-hash",
        "proof-hash",
        "unready",
        "unclean",
    ):
        current, candidate_boot = deepcopy(build), deepcopy(boot)
        if name == "wrong-sha":
            current["build_run"]["source_sha"] = "a" * 40
        elif name == "wrong-attempt":
            current["build_run"]["run_attempt"] = "1"
        elif name == "archive-hash":
            current["archive_sha256"] = "0" * 64
        elif name == "unready":
            candidate_boot["health"]["ready"] = False
        elif name == "unclean":
            candidate_boot["supervisor_exit"]["code"] = 1
        members = {
            "admission.json": json.dumps(admission).encode(),
            "boot-proof.json": json.dumps(candidate_boot).encode(),
        }
        for key, filename in (("admission", "admission.json"), ("boot", "boot-proof.json")):
            current[key] = dict(
                filename=filename, sha256=hashlib.sha256(members[filename]).hexdigest()
            )
        if name == "proof-hash":
            current["boot"]["sha256"] = "0" * 64
        members["mac-home-build.json"] = json.dumps(current).encode()
        evidence = tmp_path / (name + ".zip")
        with zipfile.ZipFile(evidence, "w", zipfile.ZIP_DEFLATED) as packed:
            for filename, content in members.items():
                packed.writestr(filename, content)
        cases.append(
            dict(
                name=name,
                options=dict(
                    mac_home_artifact_zip=str(artifact),
                    mac_home_build_artifact_zip=str(evidence),
                    mac_home_artifact_id="504",
                    mac_home_build_artifact_id="505",
                    mac_home_build_result="success",
                ),
            )
        )
    cases.extend(
        (
            dict(name="omitted", options={}),
            dict(name="partial", options=dict(mac_home_artifact_id="504")),
        )
    )
    actual = _node(
        tmp_path,
        dict(
            root=str(tmp_path),
            source_run=dict(
                repository="owner/repository",
                source_sha=SHA,
                event="push",
                ref="refs/heads/main",
                workflow_path=".github/workflows/publish-typescript-release.yml",
                workflow_ref="owner/repository/.github/workflows/publish-typescript-release.yml@refs/heads/main",
                run_id="501",
                run_attempt="2",
            ),
            cases=cases,
        ),
        "probe_home_receipt",
    )
    assert actual["spawned"] == actual["closed"] == 1
    assert actual["answers"][0] == dict(
        name="valid",
        result=dict(
            artifact_id=504,
            archive_sha256=receipt["archive_sha256"],
            manifest=manifest,
        ),
        error=None,
    )
    assert [row["error"] for row in actual["answers"][1:]] == [
        "Mac home build differs from the current publication attempt",
        "Mac home build differs from the current publication attempt",
        "Mac home archive bytes changed",
        "Mac home proof bytes changed",
        "Mac home lacks successful native admission and clean boot proof",
        "Mac home lacks successful native admission and clean boot proof",
        None,
        "Mac home receipt inputs must be supplied together",
    ]
    _report(
        request,
        "darwin-home-receipt.json",
        dict(complete=True, seconds=time.perf_counter() - started, cases=actual),
    )
