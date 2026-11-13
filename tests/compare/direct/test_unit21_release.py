"""One private-file sequence proves application identity and archive interoperability."""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from tests.support.synthetic_homes import create_synthetic_homes
from tests.support.update_apps import build_app, rewrite_artifact_digest
from tests.unit.test_app import _legacy_cached_app

from planner.environments.app import digest_app_artifact, digest_app_source, validate_app_manifest
from planner.environments.runtime_contracts import RuntimeContractError
from planner.environments.runtime_elf import ElfDynamic, ElfObject, read_dynamic, resolve_provider
from planner.environments.update_artifacts import (
    create_app_archive,
    extract_app_archive,
    hash_archive,
)
from planner.environments.update_install import require_exact_schema_frontier, schema_change_pending

ROOT = Path(__file__).resolve().parents[3]
SHA = "a" * 40


def _native_loader_fixture(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    library = "libpanels_fixture_leaf.so"
    for name in ("bin", "extensions", "ancestor", "private"):
        (root / name).mkdir(parents=True)
    for name in ("ancestor", "private"):
        (root / name / library).write_text(name + " ABI")
    caller = ElfObject(root / "bin/node", ElfDynamic((), ("$ORIGIN/../ancestor",), None, None))
    extension = root / "extensions/addon.node"
    requester = ElfObject(extension, ElfDynamic((), (), None, None))
    private = ("$ORIGIN/../private",)
    scenarios = [
        (library, requester, (caller,)),
        (library, ElfObject(extension, ElfDynamic((), (), ("$ORIGIN/absent",), None)), (caller,)),
        (
            library,
            requester,
            (
                ElfObject(
                    caller.path, ElfDynamic((), caller.dynamic.rpath, caller.dynamic.rpath, None)
                ),
            ),
        ),
        (library, ElfObject(extension, ElfDynamic((), private, None, None)), (caller,)),
        (library, ElfObject(extension, ElfDynamic((), (), private, None)), (caller,)),
        ("../unsafe.so", requester, (caller,)),
    ]
    providers, requests = [], []

    def wire(value: ElfObject) -> dict[str, Any]:
        return dict(path=str(value.path), dynamic=asdict(value.dynamic))

    for name, owner, ancestors in scenarios:
        requests.append(
            dict(name=name, requester=wire(owner), ancestors=[wire(value) for value in ancestors])
        )
        try:
            providers.append(dict(provider=str(resolve_provider(name, owner, ancestors, {}))))
        except RuntimeContractError as error:
            providers.append(dict(error=str(error)))
    dynamic = (
        "(NEEDED) [libfirst.so]\n(NEEDED) [libsecond.so]\n"
        "(RPATH) [$ORIGIN/../private:${ORIGIN}/../ancestor]\n(RUNPATH) []\n"
    )
    header = "[Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]\n"

    def fake_read(arguments: list[str], **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(stdout=dynamic if arguments[1] == "-dW" else header)

    with patch("planner.environments.runtime_elf.subprocess.run", fake_read):
        parsed = asdict(read_dynamic(extension))
    expected: dict[str, Any] = json.loads(json.dumps(dict(dynamic=parsed, providers=providers)))
    return dict(dynamic=[dynamic, header], providers=requests), expected


def test_unit21_release_identity_archive_and_frontier(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    app = build_app(tmp_path / "app", SHA, "sentinel")
    (app / "bin/alias").symlink_to("panels")
    (app / "sentinel").chmod(0o640)
    for name in (
        "a/data",
        "a-z",
        "\ue000",
        "\U00010000",
        ".pytest_cache/state",
        "__pycache__/ignored.pyc",
    ):
        path = app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    # Python compares payload JSON values, not JSON number kinds or Boolean identity.
    payload = json.loads((app / "runtime/contract.json").read_text())
    payload.update(version=True, node_major=22.0)
    (app / "runtime/contract.json").write_text(json.dumps(payload))
    rewrite_artifact_digest(app)
    manifest = validate_app_manifest(app / "manifest.json", require_runtime=True)
    legacy, _ = _legacy_cached_app(tmp_path / "legacy")
    expected_legacy = validate_app_manifest(legacy).as_dict()
    checksum = create_app_archive(app, tmp_path / "python.tar.gz")
    refusals = []
    for name, members in (
        ("escape", [("../outside", tarfile.REGTYPE, 0o644, "")]),
        ("duplicate", [("same", tarfile.REGTYPE, 0o644, ""), ("same", tarfile.REGTYPE, 0o644, "")]),
        ("fifo", [("fifo", tarfile.FIFOTYPE, 0o644, "")]),
        ("special", [("special", tarfile.REGTYPE, 0o4755, "")]),
        ("link", [("dir/link", tarfile.SYMTYPE, 0o777, "../../outside")]),
    ):
        archive = tmp_path / (name + ".tar.gz")
        with tarfile.open(archive, "w:gz") as bundle:
            for member_name, kind, mode, target in members:
                member = tarfile.TarInfo(member_name)
                member.type, member.mode, member.linkname = kind, mode, target
                member.size = 1 if kind == tarfile.REGTYPE else 0
                bundle.addfile(member, io.BytesIO(b"x") if member.size else None)
        with pytest.raises((ValueError, tarfile.TarError)):
            extract_app_archive(archive, tmp_path / ("python-" + name), hash_archive(archive), SHA)
        refusals.append(name)
    versions = tmp_path / "graph-app/src/planner/core/migrations/versions"
    versions.mkdir(parents=True)
    for revision, parent in (("initial", None), ("data_only", "initial")):
        (versions / (revision + ".py")).write_text(
            f"revision = {revision!r}\ndown_revision = {parent!r}\n"
        )
    python_frontier: list[dict[str, Any]] = []
    for name, database_revision in (
        ("current", "data_only"),
        ("ancestor", "initial"),
        ("missing", None),
        ("unknown", "future"),
    ):
        with sqlite3.connect(tmp_path / (name + ".sqlite")) as connection:
            connection.execute("CREATE TABLE alembic_version(version_num TEXT)")
            if database_revision is not None:
                connection.execute("INSERT INTO alembic_version VALUES (?)", (database_revision,))
        pending: bool | None = None
        pending_error: str | None = None
        node_error: str | None = None
        try:
            pending = schema_change_pending(tmp_path / (name + ".sqlite"), tmp_path / "graph-app")
        except ValueError as error:
            pending_error = str(error)
        try:
            require_exact_schema_frontier(tmp_path / (name + ".sqlite"), tmp_path / "graph-app")
        except ValueError as error:
            node_error = str(error)
        python_frontier.append(
            dict(
                pending=pending,
                pending_error=pending_error,
                node_refused=node_error is not None,
                node_error=node_error,
            )
        )
    v2 = tmp_path / "v2"
    shutil.copytree(app, v2, symlinks=True)
    shutil.rmtree(v2 / ".venv")
    for name in (
        "server/lifecycle/foreground_main.ts",
        "server/lifecycle/managed_main.ts",
        "server/lifecycle/application.ts",
        "server/lifecycle/stop_main.ts",
        "server/conversation/usage/usageWorker.ts",
        "src/planner/conversation/backends/hermes_acp_extension.py",
        "src/planner/conversation/backends/hermes_model_catalog.py",
        "server/environments/release/launcher_main.ts",
        "server/package.json",
        "server/package-lock.json",
        "server/node_modules/fastify/package.json",
        "server/node_modules/koffi/package.json",
        "server/node_modules/better-sqlite3/package.json",
        "server/node_modules/@anthropic-ai/claude-agent-sdk/package.json",
    ):
        path = v2 / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    document = json.loads((v2 / "manifest.json").read_text())
    contract = document["runtime_contract"]
    contract.pop("python_version")
    contract.update(version=2, minimum_node_version="22.18.0")
    (v2 / "runtime/contract.json").write_text(json.dumps(contract))
    (v2 / "manifest.json").write_text(json.dumps(document))
    rewrite_artifact_digest(v2)
    for name in ("v2-missing", "v2-extra"):
        shutil.copytree(v2, tmp_path / name, symlinks=True)
    (tmp_path / "v2-missing/server/lifecycle/stop_main.ts").unlink()
    rewrite_artifact_digest(tmp_path / "v2-missing")
    extra = tmp_path / "v2-extra/manifest.json"
    raw = json.loads(extra.read_text())
    raw["runtime_contract"]["python_version"] = "3.12.12"
    extra.write_text(json.dumps(raw))
    native_sentinel = tmp_path / "native-outside-sentinel"
    native_sentinel.write_bytes(b"outside native write sentinel\n")
    native_paths: dict[str, list[str]] = {}
    for target, relative_target in (
        ("provider", "runtime/lib/libfixture.so"),
        ("inventory", "runtime/inventory.json"),
        ("contract", "runtime/contract.json"),
    ):
        path = tmp_path / ("native-link-" + target) / relative_target
        path.parent.mkdir(parents=True)
        path.symlink_to(native_sentinel)
        root = tmp_path / ("native-link-" + target)
        native_paths[target] = sorted(str(item.relative_to(root)) for item in root.rglob("*"))
    node = shutil.which("node")
    assert node is not None
    loader_input, expected_loader = _native_loader_fixture(tmp_path / "loader")
    launcher = tmp_path / "probe.mjs"
    launcher.write_text(
        "const{release_parity,native_loader_parity}=await import(process.argv[2]);"
        "const result=await release_parity(process.argv[3],JSON.parse(process.argv[4]));"
        "result.native_loader=await native_loader_parity(JSON.parse(process.argv[5]));"
        "console.log(JSON.stringify(result));"
    )
    env = {
        "PATH": str(Path(node).parent) + os.pathsep + os.defpath,
        "LANG": "C.UTF-8",
        **create_synthetic_homes(tmp_path),
        "PLAN_TEST_MODE": "1",
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
    }
    result = subprocess.run(
        [
            node,
            str(launcher),
            (ROOT / "server/environments/release/testing/release_parity.ts").as_uri(),
            str(tmp_path),
            json.dumps(refusals),
            json.dumps(loader_input),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    actual: dict[str, Any] = json.loads(result.stdout)
    assert actual["native_tree_admission"] == [
        dict(target=target, error="app contains a symlink outside its root")
        for target in ("provider", "inventory", "contract")
    ]
    assert native_sentinel.read_bytes() == b"outside native write sentinel\n"
    for target in ("provider", "inventory", "contract"):
        root = tmp_path / ("native-link-" + target)
        assert (
            sorted(str(item.relative_to(root)) for item in root.rglob("*")) == native_paths[target]
        )
    assert actual["native_loader"] == expected_loader
    assert actual["identity"] == {
        "source": digest_app_source(app),
        "artifact": digest_app_artifact(app),
        "manifest": manifest.as_dict(),
    }
    assert actual["legacy"] == expected_legacy
    assert actual["restored"] == manifest.as_dict()
    assert actual["restored_mode"] == 0o640 and actual["restored_link"] == "panels"
    assert actual["exclusive_refused"] and actual["hash_refused"]
    assert not (tmp_path / "bad-hash").exists()
    assert not (tmp_path / "outside").exists()
    assert actual["refusals"] == [
        dict(archive=name, refused=True, destination_absent=True) for name in refusals
    ]
    assert actual["node_protocol"] == dict(
        accepted=True, missing_refused=True, python_field_refused=True
    )
    assert actual["frontier"] == python_frontier
    restored = tmp_path / "python-from-node"
    assert (
        extract_app_archive(tmp_path / "node.tar.gz", restored, actual["node_archive_sha256"], SHA)
        == manifest
    )
    assert (restored / "sentinel").stat().st_mode & 0o7777 == 0o640
    assert os.readlink(restored / "bin/alias") == "panels"
    (restored / "sentinel").write_text("independent proof tree")
    assert hash_archive(tmp_path / "python.tar.gz") == checksum
    assert hash_archive(tmp_path / "node.tar.gz") == actual["node_archive_sha256"]
    report = request.config.getoption("compare_report_dir", default=None)
    if report:
        path = Path(report)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit-21-release.json").write_text(
            json.dumps(
                {
                    "seconds": time.perf_counter() - started,
                    "source_parity": {
                        "equal": True,
                        "scope": "Covered ordinary artifacts and identity/cache rules",
                    },
                    "metadata_preflight": actual["refusals"],
                    "native_loader": {
                        "equal": True,
                        "provider_graphs": len(expected_loader["providers"]),
                        "readelf_text": True,
                        "actual_native_closure": "Hosted proof pending",
                    },
                    "native_tree_admission": actual["native_tree_admission"],
                    "node_v2_protocol": actual["node_protocol"],
                    "frontier": actual["frontier"],
                    "proposed_input_limitations": {
                        "accepted": False,
                        "restrictions": [
                            "GNU sparse entries",
                            "Extraction through internal directory symlinks",
                        ],
                        "source_archive_producer_requires_either": False,
                    },
                    "unproven": ["Runtime execution"],
                },
                indent=2,
            )
            + "\n"
        )
