"""Linux offers reuse the existing private-file Mac update host and real archive fixtures."""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.compare.direct import test_unit21_mac_updates as mac_fixture
from tests.compare.direct import test_unit21_updates as update_fixture
from tests.unit.test_mac_updates import LinuxProvider, attempt

from planner.environments.update_attempts import (
    parse_release,
)

COMPANION = update_fixture.ROOT / "server/environments/updates/mac/testing/compare.ts"
FIRST = "11111111-1111-4111-8111-111111111111"
SECOND = "22222222-2222-4222-8222-222222222222"


def test_linux_executor_offers_share_preparation_and_retain_verified_bytes(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Gap: the selector proof cannot establish readiness, retry policy or raw-flight ownership."""
    began = time.perf_counter()
    provider = LinuxProvider()
    releases = {
        name: provider.publish_linux(sha * 40)
        for name, sha in (
            ("active", "a"),
            ("next", "b"),
            ("third", "c"),
            ("retry", "d"),
            ("zip", "e"),
            ("json", "f"),
            ("predecessor", "1"),
        )
    }

    def linux_id(name: str) -> int:
        metadata = releases[name].linux_executor
        assert metadata is not None
        return metadata.artifact_id

    corrupt = releases["json"].linux_executor
    assert corrupt is not None
    original = gzip.decompress(provider.linux_archives[corrupt.artifact_id])
    malformed = original.replace(b'{"schema_version":', b'!"schema_version":', 1)
    assert malformed != original  # Preserve TAR sizes while corrupting only the JSON payload.
    payload = gzip.compress(malformed, mtime=0)
    provider.linux_archives[corrupt.artifact_id] = payload
    releases["json"] = replace(
        releases["json"],
        linux_executor=replace(
            corrupt,
            archive_sha256=hashlib.sha256(payload).hexdigest(),
            archive_size=len(payload),
        ),
    )
    artifacts: dict[str, str] = {}
    for release in releases.values():
        for metadata in (release.mac, release.linux_executor):
            assert metadata is not None
            path = tmp_path / f"{metadata.artifact_id}.zip"
            provider.download_artifact(metadata.artifact_id, path)
            artifacts[str(metadata.artifact_id)] = str(path)
    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_bytes(b"not a ZIP archive")
    zip_id = linux_id("zip")
    repairs = {str(zip_id): artifacts[str(zip_id)]}
    artifacts[str(zip_id)] = str(bad_zip)
    operations: list[dict[str, Any]] = []
    locations: dict[str, int] = {}

    def add(action: str, *, tag: str = "", **values: Any) -> None:
        if tag:
            locations[tag] = len(operations)
        operations.append(dict(action=action, **values))

    def digest(name: str) -> str:
        metadata = releases[name].linux_executor
        assert metadata is not None
        return metadata.archive_sha256

    for machine in (FIRST, SECOND):
        add("platform", machine_id=machine, platform="linux")
    add("ready", key="active")
    add("prepare", key="active")
    add("hold", artifact_id=linux_id("active"))
    add("write", key="installing")
    add("check", machine_id=FIRST, tag="before_ready")
    add("wait_download", artifact_id=linux_id("active"))
    add("check", machine_id=SECOND)
    add("downloads", tag="shared")
    add("release", artifact_id=linux_id("active"))
    add("settled")
    add("check", machine_id=FIRST, tag="first")
    add("check", machine_id=SECOND, tag="second")
    add(
        "report",
        machine_id=FIRST,
        report=dict(
            request_id="mac_one." + FIRST,
            application_revision="a" * 40,
            phase="waiting",
            detail="first only",
        ),
    )
    add("status", machine_id=SECOND, tag="isolated")
    add("prefetch", machine_id=SECOND, id=digest("active"))
    add("write", key="done")
    add("registered", registered=True)
    add("hold", artifact_id=linux_id("next"))
    add("ready", key="next")
    add("prepare", key="next")
    add("wait_download", artifact_id=linux_id("next"))
    add("check", tag="mac_not_blocked")
    add("release", artifact_id=linux_id("next"))
    add("settled")
    add("retention", tag="active_retained")
    add("ready", key="third")
    add("prepare", key="third")
    add("settled")
    add("retention", tag="active_still_retained")
    add("archives", machine_id=FIRST)
    add("archives", tag="mac_storage")
    add("restart", sha="9" * 40, provider=True)
    add("ready", key="predecessor")
    add("prepare", key="predecessor")
    add("settled")
    add("retention", tag="predecessor_retained")
    add("fail", artifact_id=linux_id("retry"))
    add("ready", key="retry")
    add("prepare", key="retry")
    add("settled")
    add("recover", artifact_id=linux_id("retry"))
    add("check", machine_id=FIRST)
    add("downloads", tag="throttled")
    add("clock", seconds=61)
    add("check", machine_id=FIRST)
    add("settled")
    add("check", machine_id=FIRST, tag="retried")
    add("ready", key="zip")
    add("prepare", key="zip")
    add("settled")
    add("repair", artifact_id=linux_id("zip"))
    add("clock", seconds=61)
    add("check", machine_id=FIRST, tag="rejected")
    add("settled")
    add("downloads", tag="rejected_downloads")
    add("restart", sha="9" * 40, provider=True)
    add("check", machine_id=FIRST)
    add("settled")
    add("check", machine_id=FIRST, tag="restart_retry")
    add("ready", key="json")
    add("prepare", key="json")
    add("settled")
    add("clock", seconds=61)
    add("check", machine_id=SECOND)
    add("settled")
    add("downloads", tag="json_downloads")
    spec = dict(
        releases={name: asdict(value) for name, value in releases.items()},
        attempts={
            "installing": asdict(attempt(releases["active"], "installing")),
            "done": asdict(attempt(releases["active"], "done")),
        },
        artifacts=artifacts,
        repairs=repairs,
        operations=operations,
        inspections={},
        linux_offers=True,
        abort_release_key="active",
    )
    py, node = tmp_path / "python", tmp_path / "node"
    py.mkdir()
    node.mkdir()
    expected = mac_fixture._python({**json.loads(json.dumps(spec)), "root": str(py)})
    with patch.object(update_fixture, "COMPANION", COMPANION):
        actual = update_fixture._node(node, {**spec, "root": str(node)}, "mac_update_sequence")
    update_fixture._report(
        request,
        "linux-executor-offers.json",
        dict(
            seconds=time.perf_counter() - began,
            operations=len(operations),
            equal=actual["parity"] == expected,
            python=expected,
            typescript=actual,
        ),
    )
    assert actual["parity"] == expected

    def result(tag: str) -> Any:
        return expected["output"][locations[tag]]["result"]

    assert result("before_ready")["prefetch"] is None
    assert result("shared").count(linux_id("active")) == 1
    assert result("first")["target"]["request_id"] != result("second")["target"]["request_id"]
    assert result("isolated")["detail"] != "first only"
    next_mac = releases["next"].mac
    assert next_mac is not None
    assert result("mac_not_blocked")["prefetch"]["archive_sha256"] == next_mac.archive_sha256
    assert result("active_retained")["archives"] == [digest("next"), digest("active")]
    assert result("active_still_retained")["archives"] == [digest("third"), digest("active")]
    assert result("predecessor_retained")["archives"] == [digest("predecessor"), digest("third")]
    assert len(result("mac_storage")) == 3
    assert result("throttled").count(linux_id("retry")) == 1
    assert result("retried")["prefetch"] is not None
    assert result("rejected")["prefetch"] is None
    assert result("rejected_downloads").count(linux_id("zip")) == 1
    assert result("restart_retry")["prefetch"] is not None
    assert result("json_downloads").count(linux_id("json")) == 1
    assert actual["native_abort"] == dict(
        waited=True,
        temporary_removed=True,
        archives_unchanged=True,
        offered=None,
        emitted_after_close=0,
    )
    assert actual["children"]["spawned"] == actual["children"]["closed"]


def test_malformed_optional_linux_metadata_keeps_the_home_attempt(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Gap: optional executor metadata must not discard a valid persisted home update."""
    began = time.perf_counter()
    results = []
    provider = LinuxProvider()
    published = provider.publish_linux()
    linux_metadata = published.linux_executor
    assert linux_metadata is not None
    for index, linux in enumerate(("invalid", {"artifact_id": 7}, asdict(linux_metadata))):
        root = tmp_path / str(index)
        root.mkdir()
        record = json.loads(json.dumps(asdict(attempt(published, "installing"))))
        record["release"]["linux_executor"] = linux
        parsed = parse_release(record["release"])
        assert parsed.manifest.app_sha == published.manifest.app_sha
        paths = root / "python.json", root / "node.json"
        for path in paths:
            path.write_text(json.dumps(record))
        actual = update_fixture._compare_store(root, paths, [{"kind": "read"}])
        restored = actual["answers"][0]["result"]
        assert (
            restored is not None
            and restored["release"]["manifest"]["app_sha"] == published.manifest.app_sha
        )
        assert restored["release"]["linux_executor"] == (
            None if index < 2 else asdict(linux_metadata)
        )
        results.append(actual)
    update_fixture._report(
        request,
        "linux-optional-metadata.json",
        dict(
            seconds=time.perf_counter() - began,
            cases=results,
        ),
    )
