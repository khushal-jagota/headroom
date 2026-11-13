"""One private-file sequence proves application identity and archive interoperability."""

from __future__ import annotations

import ctypes
import gzip
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from tests.compare.direct import unit21_schema_seed
from tests.support.synthetic_homes import create_synthetic_homes
from tests.support.update_apps import build_app, rewrite_artifact_digest
from tests.unit.test_app import _git_source, _legacy_cached_app
from tests.unit.test_runtime_admission import DEPENDENCIES as admission_dependencies
from tests.unit.test_runtime_admission import node_report

from planner.environments import runtime_admission as admission
from planner.environments.app import (
    AppValidationError,
    build_exported_app,
    digest_app_artifact,
    digest_app_source,
    validate_app_manifest,
)
from planner.environments.runtime_contracts import NodeRuntimeContract, RuntimeContractError
from planner.environments.runtime_elf import ElfDynamic, ElfObject, read_dynamic, resolve_provider
from planner.environments.update_artifacts import (
    create_app_archive,
    extract_app_archive,
    hash_archive,
)
from planner.environments.update_install import require_exact_schema_frontier, schema_change_pending

ROOT = Path(__file__).resolve().parents[3]
SHA = "a" * 40
FIXTURE_TIME_NS = 1_700_000_000_125_000_000


def _unit_boundaries(
    root: Path, observed: dict[str, Any], node: str, environment: dict[str, str], literal: str
) -> dict[str, Any]:
    """Inert layout markers prove routing only, never interpreter/CLI/runtime success."""
    began = time.perf_counter()
    layout = root / "unit-layout"
    for name in (
        "bin/panels",
        "bin/panels-launcher",
        ".venv/bin/python",
    ):
        path = layout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# inert structural marker, never executed\n")
        path.chmod(0o700)
    (layout / "runtime").mkdir()
    routes = []
    # Other classifier routes already belong to the prerequisite Vitest case.
    for contract, payload, missing, expected in (
        ({"version": 3}, {"version": 3}, False, "unsupported runtime contract version"),
        ({"version": 1}, {"version": 1}, True, ".venv/bin/python"),
    ):
        (layout / "manifest.json").write_text(
            json.dumps(
                dict(
                    format="panels-app-v1",
                    app_sha=SHA,
                    source_digest="b" * 64,
                    artifact_digest="c" * 64,
                    runtime_contract=contract,
                )
            )
        )
        (layout / "runtime/contract.json").write_text(json.dumps(payload))
        if missing:
            (layout / ".venv/bin/python").unlink()
        result = subprocess.run(
            [
                node,
                "--input-type=module",
                "-e",
                observed["classifier_source"],
                str(layout),
                "",
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 1 and result.stdout == ""
        assert result.stderr.startswith("Panels runtime classification failed: ")
        assert expected in result.stderr
        routes.append(dict(expected=expected, exit_code=result.returncode))
    assert all(observed["context"].values()), observed["context"]
    refusal = subprocess.run(
        [
            node,
            str(ROOT / "server/stalls/watchdog_main.ts"),
            str(root / "unit-empty.sqlite"),
            str(root / "unit-watchdog"),
        ],
        env={
            **environment,
            "PLAN_CONFIG_PATH": str(root / "unit-config.yaml"),
            "PLAN_TEST_MODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert (refusal.returncode, refusal.stdout, refusal.stderr) == (
        1,
        "",
        "watchdog provider command is unavailable in test mode\n",
    )
    managed = observed["managed"]
    assert "WorkingDirectory=" not in managed and "TimeoutStopSec=90\n" in managed
    assert "RuntimeDirectory=panels-proof\nRuntimeDirectoryMode=0700\n" in managed
    assert managed.count("PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS=1") == 2
    assert managed.count("unset NODE_OPTIONS NODE_PATH LD_PRELOAD LD_LIBRARY_PATH") == 2
    assert managed.count('"%t/panels-proof"') == 2
    assert "$$runtime" in managed and "$$1" in managed and "unit $$%% " in managed
    assert "OOMScoreAdjust=-500\n" in managed
    actions = [
        line.partition("=")[2]
        for line in managed.splitlines()
        if line.startswith(("ExecStart=", "ExecStop="))
    ]
    assert (
        len(actions) == 2 and actions[0].partition("case ")[0] == actions[1].partition("case ")[0]
    )
    escaped = (
        literal.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("%", "%%")
    )
    for output in (managed, observed["watchdog"]["service"]):
        assert 'Environment="UNIT_LITERAL=' + escaped + '"\n' in output
    watchdog = observed["watchdog"]
    assert "TimeoutStartSec=300\n" in watchdog["service"]
    assert "server/stalls/watchdog_main.ts" in watchdog["service"]
    for directive in (
        "OnBootSec=60s",
        "OnUnitActiveSec=30s",
        "AccuracySec=5s",
        "Unit=panels-typescript-stall-watchdog.service",
    ):
        assert directive + "\n" in watchdog["timer"]
    return dict(
        routes=routes,
        context=observed["context"],
        watchdog_refusal=refusal.stderr,
        rendered_sha256={
            name: hashlib.sha256(text.encode()).hexdigest()
            for name, text in {"managed": managed, **watchdog}.items()
        },
        producer_milliseconds=observed["milliseconds"],
        seconds=time.perf_counter() - began,
        scope="structural/local only",
    )


def _admission_group_proof(root: Path, environment: dict[str, str], node: str) -> dict[str, Any]:
    """Hosted only: independent pinned cleanup survives a regressed leader-only kill."""
    from tests.compare.direct.test_unit21_stop_coherence import (
        _native_dead,
        _pin_native,
        _reap_native,
    )

    root.mkdir()
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0
    assert libc.prctl(36, 1, 0, 0, 0) == 0
    handles: dict[int, int] = {}
    process: subprocess.Popen[str] | None = None
    started = time.perf_counter()
    try:
        entry = root / "group-proof.mjs"
        entry.write_text(
            "const{admission_group_parity}=await import(process.argv[2]);"
            "console.log(JSON.stringify(await admission_group_parity(process.argv[3])));"
        )
        process = subprocess.Popen(
            [
                node,
                str(entry),
                (ROOT / "server/environments/release/testing/release_parity.ts").as_uri(),
                str(root),
            ],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        marker = root / "group-identities.json"
        until = time.monotonic() + 15
        while not marker.exists():
            assert process.poll() is None, "Admission fixture exited before its identity handoff"
            assert time.monotonic() < until, (
                "Admission fixture did not publish its owned identities"
            )
            time.sleep(0.01)
        identities = json.loads(marker.read_text())
        for name, parent in (("leader", process.pid), ("descendant", identities["leader"]["pid"])):
            identity = identities[name]
            handles[identity["pid"]] = _pin_native(identity, parent)
        assert len(handles) == 2
        # No actual timeout callback can run before both validated pidfds exist.
        (root / "release-timeout").touch()
        output, errors = process.communicate(timeout=20)
        assert process.returncode == 0, errors
        result: dict[str, Any] = json.loads(output)
        assert result["timeout_callback_released_after_pin"] is True
        assert result["message"] == "runtime probe failed: command timed out"
        assert 0 < result["timer_delay"] <= 45_000
        _native_dead(handles)
        _reap_native(handles)
        return {
            **result,
            "seconds": time.perf_counter() - started,
            "independent_pidfd_cleanup": True,
            "both_actual_exits_reaped": True,
            "scope": (
                "Actual group timeout delivery and reap. Real timer duration is covered separately."
            ),
        }
    finally:
        try:
            if process is not None:
                # Pin a partial handoff before any fixture-parent cleanup or reparenting.
                try:
                    marker = root / "group-identities.json"
                    if marker.exists() and not (root / "release-timeout").exists():
                        identities = json.loads(marker.read_text())
                        for name, parent in (
                            ("leader", process.pid),
                            ("descendant", identities["leader"]["pid"]),
                        ):
                            identity = identities[name]
                            if identity["pid"] not in handles:
                                handles[identity["pid"]] = _pin_native(identity, parent)
                finally:
                    if len(handles) == 2:
                        if process.poll() is None:
                            process.kill()  # Both descendants have independent cleanup handles.
                        process.communicate(timeout=10)
                    else:
                        # Keep its own20s handoff guard, force and actual join alive first.
                        process.communicate(timeout=30)
        finally:
            try:
                for descriptor in handles.values():
                    try:
                        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                _reap_native(handles)
            finally:
                for descriptor in handles.values():
                    os.close(descriptor)
                assert libc.prctl(36, previous.value, 0, 0, 0) == 0


def _admission_rules(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    v1 = validate_app_manifest(root / "app/manifest.json").runtime_contract
    v2 = validate_app_manifest(root / "v2/manifest.json").runtime_contract
    assert v1 is not None and isinstance(v2, NodeRuntimeContract)
    facts: dict[str, Any] = dict(
        system="linux",
        architecture="x86_64",
        distribution="ubuntu",
        version="24.04",
        glibc="2.39",
        feature_sets=[list(v1.cpu_features)],
    )
    hosts: list[dict[str, Any]] = [
        dict(version=version, node=node, facts={**facts, "feature_sets": flags})
        for version, node, flags in [
            (1, "v22.0.0", facts["feature_sets"]),
            (2, "v22.0.0", facts["feature_sets"]),
            (2, "v22.18.0", facts["feature_sets"]),
            (2, "v22.18.0", [[]]),
        ]
    ]
    read_text = Path.read_text
    expected_hosts: list[str | None] = []
    for item in hosts:
        flags = tuple(item["facts"]["feature_sets"][0])

        def synthetic_text(
            path: Path, *args: Any, _flags: tuple[str, ...] = flags, **kwargs: Any
        ) -> str:
            if str(path) == "/proc/cpuinfo":
                return "flags: " + " ".join(_flags)
            return read_text(path, *args, **kwargs)

        with (
            patch("planner.environments.runtime_admission.platform.system", lambda: "Linux"),
            patch("planner.environments.runtime_admission.platform.machine", lambda: "x86_64"),
            patch(
                "planner.environments.runtime_admission.platform.freedesktop_os_release",
                lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"},
            ),
            patch(
                "planner.environments.runtime_admission.platform.libc_ver",
                lambda: ("glibc", "2.39"),
            ),
            patch("planner.environments.runtime_admission.ctypes.CDLL", lambda name: None),
            patch(
                "planner.environments.runtime_admission.shutil.which",
                lambda *args, **kwargs: "/synthetic/tool",
            ),
            patch.object(
                admission, "_run_probe", lambda *args, _node=item["node"], **kwargs: _node
            ),
            patch.object(Path, "read_text", synthetic_text),
        ):
            try:
                admission.verify_runtime_host(v1 if item["version"] == 1 else v2, state=root)
                expected_hosts.append(None)
            except AppValidationError as error:
                expected_hosts.append(str(error))
    reports = [
        node_report(root / "v2"),
        node_report(root / "v2", extra=True),
        node_report(root / "v2", loaded_libraries=[str(root / "outside.node")]),
        node_report(root / "v2", package_paths={}),
    ]
    expected_reports: list[str | None] = []
    for report in reports:
        with patch.object(
            admission, "_run_json_probe", lambda *args, _report=report, **kwargs: _report
        ):
            try:
                admission._run_node_probe(
                    root / "v2", v2, node="/synthetic/node", state=root, deadline=1
                )
                expected_reports.append(None)
            except AppValidationError as error:
                expected_reports.append(str(error))
    return dict(host_cases=hosts, reports=reports), dict(
        hosts=expected_hosts, reports=expected_reports
    )


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


def _tree(root: Path) -> list[dict[str, Any]]:
    """Compare actual bytes/modes/link topology, without following directory aliases."""
    rows: list[dict[str, Any]] = []
    inodes: dict[tuple[int, int], str] = {}
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        info = path.lstat()
        row: dict[str, Any] = dict(
            path=path.relative_to(root).as_posix(), mode=stat.S_IMODE(info.st_mode)
        )
        if path.is_symlink():
            row.update(kind="symlink", target=os.readlink(path))
        elif path.is_dir():
            row.update(kind="directory", mtime_ns=str(info.st_mtime_ns))
        else:
            inode = (info.st_dev, info.st_ino)
            row.update(
                kind="file",
                bytes=path.read_bytes().hex(),
                mtime_ns=str(info.st_mtime_ns),
                hardlink_to=inodes.get(inode),
            )
            inodes.setdefault(inode, row["path"])
        rows.append(row)
    return rows


def _pax(key: str, value: str, *, leading_zero: bool = False) -> bytes:
    body = f"{key}={value}\n".encode()
    prefix = b"0" if leading_zero else b""
    size = len(body) + 2 + len(prefix)
    while size != len(prefix) + len(str(size)) + 1 + len(body):
        size = len(prefix) + len(str(size)) + 1 + len(body)
    return prefix + str(size).encode() + b" " + body


def _record(
    name: str,
    kind: bytes = tarfile.REGTYPE,
    body: bytes = b"",
    *,
    target: str = "",
    mode: int = 0o644,
    size: int | None = None,
) -> bytes:
    member = tarfile.TarInfo(name)
    member.type, member.mode, member.linkname = kind, mode, target
    member.mtime = FIXTURE_TIME_NS // 1_000_000_000
    member.size = len(body) if size is None else size
    header = member.tobuf(format=tarfile.GNU_FORMAT)
    return header + body + bytes(-len(body) % 512)


def _checksum(header: bytes | bytearray, *, signed: bool = False) -> bytes:
    value = bytearray(header)
    value[148:156] = b" " * 8
    total = sum(byte if not signed or byte < 128 else byte - 256 for byte in value)
    value[148:156] = f"{total:06o}\0 ".encode()
    return bytes(value)


def _archive_vectors(root: Path, ordinary: Path) -> list[dict[str, str]]:
    raw = gzip.decompress(ordinary.read_bytes())
    with tarfile.open(ordinary, "r:gz") as bundle:
        end = max(
            member.offset_data + (member.size + 511) // 512 * 512 for member in bundle.getmembers()
        )
    prefix = raw[:end]
    eof = bytes(1024)

    def x(values: dict[str, str]) -> bytes:
        return _record(
            "PaxHeaders/fixture",
            tarfile.XHDTYPE,
            b"".join(_pax(key, value) for key, value in values.items()),
        )

    signed = _checksum(_record("é-signed-checksum")[:512], signed=True)
    negative = tarfile.TarInfo("negative-time")
    negative.mtime, negative.mode = -1, 0o640
    v7 = bytearray(_record("v7")[:512])
    v7[257:265] = bytes(8)
    positive = bytearray(_record("positive-base256")[:512])
    positive[124:136] = b"\x80" + bytes(11)
    octal_edge = bytearray(_record("octal-edge")[:512])
    octal_edge[100:108] = b" +00640\0"
    high_bit_length = bytearray(_pax("path", "pax-length"))
    high_bit_length[0] |= 0x80
    long_name = "25 path=gnu-name-not-pax\n"
    cases = [
        ("escape", "parity", _record("../outside")),
        (
            "outside-hardlink",
            "parity",
            _record("outside-hard", tarfile.LNKTYPE, target="../outside"),
        ),
        ("duplicate", "parity", _record("sentinel")),
        ("duplicate-slash", "parity", _record("a/", tarfile.DIRTYPE, mode=0o755)),
        ("fifo", "parity", _record("fifo", tarfile.FIFOTYPE)),
        ("special", "parity", _record("special", mode=0o4755)),
        ("outside-symlink", "parity", _record("dir/link", tarfile.SYMTYPE, target="../../outside")),
        (
            "link-parent",
            "accepted_restriction",
            _record("through", tarfile.SYMTYPE, target="a") + _record("through/child"),
        ),
        (
            "later-link-parent",
            "accepted_restriction",
            _record("late/child") + _record("late", tarfile.SYMTYPE, target="a"),
        ),
        ("gnu-sparse", "accepted_restriction", _record("sparse", tarfile.GNUTYPE_SPARSE)),
        (
            "pax-sparse-local",
            "accepted_restriction",
            x({"GNU.sparse.map": "0,0", "GNU.sparse.size": "0"}) + _record("pax-sparse"),
        ),
        (
            "pax-sparse-global",
            "accepted_restriction",
            _record(
                "global",
                tarfile.XGLTYPE,
                _pax("GNU.sparse.map", "0,0") + _pax("GNU.sparse.size", "0"),
            )
            + _record("global-sparse"),
        ),
        ("pax-escape", "parity", x({"path": "../outside"}) + _record("raw")),
        ("pax-duplicate", "parity", x({"path": "sentinel"}) + _record("raw")),
        (
            "pax-high-bit-length",
            "resource_admission",
            _record("PaxHeaders/high-bit", tarfile.XHDTYPE, bytes(high_bit_length))
            + _record("raw"),
        ),
        (
            "pax-leading-zero-length",
            "parity",
            _record(
                "PaxHeaders/leading-zero",
                tarfile.XHDTYPE,
                _pax("path", "pax-length", leading_zero=True) + _pax("mtime", "-1.25"),
            )
            + _record("raw"),
        ),
        ("pax-hex-mtime", "parity", x({"mtime": "0x10"}) + _record("hex-time")),
        (
            "pax-long-path",
            "parity",
            _record("pax", tarfile.DIRTYPE, mode=0o755)
            + x({"path": "pax/" + "p" * 140, "mtime": "1700000000.1234567"})
            + _record("raw", body=b"PAX bytes"),
        ),
        (
            "gnu-long-name",
            "parity",
            _record("././@LongLink", tarfile.GNUTYPE_LONGNAME, long_name.encode() + b"\0")
            + _record("raw"),
        ),
        (
            "gnu-long-link",
            "parity",
            _record("././@LongLink", tarfile.GNUTYPE_LONGLINK, b"a/data\0")
            + _record("gnu-link", tarfile.SYMTYPE),
        ),
        (
            "pax-gnu-unwind",
            "parity",
            x({"path": "outer-pax"})
            + _record("././@LongLink", tarfile.GNUTYPE_LONGNAME, b"inner-gnu\0")
            + _record("raw"),
        ),
        ("signed-checksum", "parity", signed),
        ("negative-base256-time", "parity", negative.tobuf(format=tarfile.GNU_FORMAT)),
        ("positive-base256", "parity", _checksum(positive)),
        ("octal-edge", "parity", _checksum(octal_edge)),
        ("v7", "parity", _checksum(v7)),
        (
            "oversized-metadata",
            "resource_admission",
            _record("large-pax", tarfile.XHDTYPE, size=1024 * 1024 + 1),
        ),
        (
            "unknown-pax-key",
            "accepted_restriction",
            x({"vendor.inert": "ignored-by-python"}) + _record("vendor"),
        ),
        ("empty-pax-path", "accepted_restriction", x({"path": ""}) + _record("empty-name")),
        (
            "directory-body",
            "accepted_restriction",
            _record("odd-directory", tarfile.DIRTYPE, body=b"x"),
        ),
    ]
    invalid_utf8 = bytearray(_record("raw-encoding")[:512])
    invalid_utf8[:2] = b"\xff\0"
    cases.append(("non-utf8-name", "accepted_restriction", _checksum(invalid_utf8)))
    vectors = []
    for name, policy, records in cases:
        archive = root / (name + ".tar.gz")
        archive.write_bytes(gzip.compress(prefix + records + eof, compresslevel=1, mtime=0))
        vectors.append(dict(name=name, policy=policy))
    extra = {
        "truncated-gzip": gzip.compress(prefix + eof, mtime=0)[:-8],
        "truncated-body": gzip.compress(prefix + _record("cut", size=513) + b"x", mtime=0),
        "partial-header": gzip.compress(prefix + b"partial", mtime=0),
        "post-end-bytes": gzip.compress(prefix + eof + b"not-zero" + bytes(504), mtime=0),
        "end-padding-bomb": gzip.compress(
            prefix + bytes(20 * 1024 + 512), compresslevel=1, mtime=0
        ),
    }
    for name, content in extra.items():
        (root / (name + ".tar.gz")).write_bytes(content)
        vectors.append(dict(name=name, policy="resource_admission"))
    (root / "partial-output-cleanup.tar.gz").write_bytes(
        gzip.compress(prefix + _record("inaccessible", tarfile.DIRTYPE, mode=0) + eof, mtime=0)
    )
    vectors.append(dict(name="partial-output-cleanup", policy="cleanup", expected_sha="b" * 40))
    risks = {
        "escape": "Raw member parent traversal",
        "outside-hardlink": "Archive-root-relative hard-link escape",
        "duplicate": "Exact duplicate member",
        "duplicate-slash": "Directory trailing slash normalization before duplicate check",
        "fifo": "Non-file member type",
        "special": "Special permission bits",
        "outside-symlink": "Parent-relative symbolic-link escape",
        "link-parent": "Member below a preceding link",
        "later-link-parent": "Member below a link declared later",
        "gnu-sparse": "Original GNU sparse type",
        "pax-sparse-local": "Local PAX sparse metadata",
        "pax-sparse-global": "Global PAX sparse metadata",
        "pax-escape": "PAX path overrides a safe raw header with traversal",
        "pax-duplicate": "Duplicate detection uses the effective PAX path",
        "pax-high-bit-length": "Length digits must retain their original ASCII bytes",
        "pax-leading-zero-length": (
            "Positive PAX length allows leading zero digits; negative fractional time normalizes"
        ),
        "pax-hex-mtime": (
            "Invalid decimal PAX time uses Python zero fallback, not JavaScript hexadecimal"
        ),
        "pax-long-path": (
            "PAX long path and non-dyadic current-epoch time retain exact nanoseconds"
        ),
        "gnu-long-name": "GNU long-name payload is not interpreted as PAX",
        "gnu-long-link": "GNU long-link target is applied to its member",
        "pax-gnu-unwind": "Nested PAX and GNU override precedence",
        "signed-checksum": "Historical signed header checksum",
        "negative-base256-time": "Signed base-256 numeric header",
        "positive-base256": "Unsigned base-256 numeric marker",
        "octal-edge": "Whitespace and plus sign in octal header",
        "v7": "Old header without ustar prefix fields",
        "oversized-metadata": "Declared metadata allocation limit before body read",
        "unknown-pax-key": "Accepted refusal of unknown inert PAX metadata",
        "empty-pax-path": "Accepted refusal of an empty effective PAX path",
        "directory-body": "Accepted refusal of nonempty directory or link bodies",
        "non-utf8-name": "Accepted refusal of non-UTF-8 archive names",
        "truncated-gzip": "Missing gzip trailer",
        "truncated-body": "Missing declared member bytes",
        "partial-header": "Incomplete trailing header",
        "post-end-bytes": "Nonzero bytes after TAR end",
        "end-padding-bomb": "Compressed all-zero stream exceeds finite end-padding limit",
        "partial-output-cleanup": (
            "Final identity refusal removes files and inaccessible owned directory"
        ),
    }
    for vector in vectors:
        vector["risk"] = risks[vector["name"]]
    return vectors


def _python_archive_result(archive: Path, destination: Path, expected_sha: str) -> dict[str, Any]:
    failed = False
    try:
        extract_app_archive(archive, destination, hash_archive(archive), expected_sha)
        return dict(status="restored", tree=_tree(destination))
    except Exception as error:
        failed = True
        return dict(
            status="refused",
            error_type=type(error).__name__,
            message=str(error)[:512],
            destination_exists=destination.exists(),
        )
    finally:
        if failed and destination.is_dir():
            destination.chmod(0o700)
            for directory, names, _ in os.walk(destination, followlinks=False):
                for name in names:
                    path = Path(directory) / name
                    if not path.is_symlink():
                        path.chmod(0o700)


def test_unit21_release_identity_archive_and_frontier(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    tmp_path = tmp_path.resolve()
    damaged = tmp_path / "damaged.tar.gz"
    damaged.write_bytes(b"not a gzip archive\n")
    with pytest.raises(ValueError) as hash_first:
        extract_app_archive(damaged, tmp_path / "python-bad-hash", "0" * 64, SHA)
    assert str(hash_first.value) == "app archive hash differs from publication receipt"
    assert not (tmp_path / "python-bad-hash").exists()
    app = build_app(tmp_path / "app", SHA, "sentinel")
    (app / "bin/alias").symlink_to("panels")
    (app / "sentinel").chmod(0o640)
    os.link(app / "sentinel", app / "hard-alias")
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
    (app / "directory-alias").symlink_to("a", target_is_directory=True)
    # Python compares payload JSON values, not JSON number kinds or Boolean identity.
    payload = json.loads((app / "runtime/contract.json").read_text())
    payload.update(version=True, node_major=22.0)
    (app / "runtime/contract.json").write_text(json.dumps(payload))
    rewrite_artifact_digest(app)
    for path in app.rglob("*"):
        if not path.is_symlink():
            os.utime(path, ns=(FIXTURE_TIME_NS, FIXTURE_TIME_NS))
    manifest = validate_app_manifest(app / "manifest.json", require_runtime=True)
    legacy, _ = _legacy_cached_app(tmp_path / "legacy")
    expected_legacy = validate_app_manifest(legacy).as_dict()
    checksum = create_app_archive(app, tmp_path / "python.tar.gz")
    vectors = _archive_vectors(tmp_path, tmp_path / "python.tar.gz")
    python_table = {
        vector["name"]: _python_archive_result(
            tmp_path / (vector["name"] + ".tar.gz"),
            tmp_path / ("python-" + vector["name"]),
            vector.get("expected_sha", SHA),
        )
        for vector in vectors
    }
    versions = tmp_path / "graph-app/src/planner/core/migrations/versions"
    versions.mkdir(parents=True)
    for revision, parent in (("initial", None), ("data_only", "initial")):
        (versions / (revision + ".py")).write_text(
            f"revision = {revision!r}\ndown_revision = {parent!r}\n"
        )
    with patch.object(unit21_schema_seed, "ROOT", tmp_path / "graph-app"):
        expected_graph = unit21_schema_seed.build_migration_graph()
    graph_path = tmp_path / "graph-app/server/environments/release/migration_graph.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text(json.dumps(expected_graph))
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
        "server/stalls/watchdog_main.ts",
        "server/conversation/usage/usageWorker.ts",
        "src/planner/conversation/backends/hermes_acp_extension.py",
        "src/planner/conversation/backends/hermes_model_catalog.py",
        "server/environments/release/launcher_main.ts",
        "server/environments/release/archive_unpack_main.ts",
        "server/environments/updates/interruption_capture_main.ts",
        "server/environments/updates/restore_main.ts",
        "runtime/installer-files.json",
        "server/environments/updates/installer/main.ts",
        "server/package.json",
        "server/package-lock.json",
        "server/node_modules/fastify/package.json",
        "server/node_modules/koffi/package.json",
        "server/node_modules/better-sqlite3/package.json",
        "server/node_modules/@anthropic-ai/claude-agent-sdk/package.json",
    ):
        path = v2 / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"dependencies": admission_dependencies})
            if name == "server/package.json"
            else "{}"
        )
    document = json.loads((v2 / "manifest.json").read_text())
    contract = document["runtime_contract"]
    contract.pop("python_version")
    contract.update(version=2, minimum_node_version="22.18.0")
    (v2 / "runtime/contract.json").write_text(json.dumps(contract))
    (v2 / "manifest.json").write_text(json.dumps(document))
    rewrite_artifact_digest(v2)
    admission_spec, expected_admission = _admission_rules(tmp_path)
    for name in ("v2-missing", "v2-extra"):
        shutil.copytree(v2, tmp_path / name, symlinks=True)
    (tmp_path / "v2-missing/server/lifecycle/stop_main.ts").unlink()
    rewrite_artifact_digest(tmp_path / "v2-missing")
    extra = tmp_path / "v2-extra/manifest.json"
    raw = json.loads(extra.read_text())
    raw["runtime_contract"]["python_version"] = "3.12.12"
    extra.write_text(json.dumps(raw))
    invalid_both = tmp_path / "v2-invalid-both"
    shutil.copytree(tmp_path / "v2-extra", invalid_both, symlinks=True)
    (invalid_both / "server/lifecycle/stop_main.ts").unlink()
    rewrite_artifact_digest(invalid_both)
    try:
        validate_app_manifest(invalid_both / "manifest.json", require_runtime=True)
    except ValueError as error:
        invalid_order = str(error)
    else:
        raise AssertionError("the combined invalid contract/tree must refuse")
    assert invalid_order.startswith("invalid runtime contract:")
    native_sentinel = tmp_path / "native-outside-sentinel"
    native_sentinel.write_bytes(b"outside native write sentinel\n")
    native_root = tmp_path / "native-link-provider"
    native_library = native_root / "runtime/lib/libfixture.so"
    native_library.parent.mkdir(parents=True)
    native_library.symlink_to(native_sentinel)
    native_paths = sorted(str(item.relative_to(native_root)) for item in native_root.rglob("*"))
    node = shutil.which("node")
    assert node is not None
    loader_input, expected_loader = _native_loader_fixture(tmp_path / "loader")
    launcher = tmp_path / "probe.mjs"
    launcher.write_text(
        "const{release_parity,native_loader_parity,admission_rules_parity,builder_parity,"
        "unit_boundaries_parity,hostile_native_parity}="
        "await import(process.argv[2]);"
        "const result=await release_parity(process.argv[3],JSON.parse(process.argv[4]));"
        "result.native_loader=await native_loader_parity(JSON.parse(process.argv[5]));"
        "result.admission=await admission_rules_parity("
        "process.argv[3],JSON.parse(process.argv[6]));"
        "result.builder=await builder_parity(process.argv[3],JSON.parse(process.argv[7]));"
        "result.units=await unit_boundaries_parity(process.argv[3],{...process.env},"
        "JSON.parse(process.argv[8]));"
        "result.hostile_native=await hostile_native_parity(process.argv[3]);"
        "console.log(JSON.stringify(result));"
    )
    env = {
        "PATH": str(Path(node).parent) + os.pathsep + os.defpath,
        "LANG": "C.UTF-8",
        **create_synthetic_homes(tmp_path),
        "PLAN_TEST_MODE": "1",
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
    }
    env["TMPDIR"] = str(tmp_path)
    for key, relative_path in {
        "PLAN_CONFIG_PATH": "private.yaml",
        "PLAN_DB_PATH": "private.sqlite",
        "PLAN_LOGS_DIR": "logs",
        "PLAN_BACKUP_DIR": "backups",
        "PLAN_APP_ROOT": "app",
        "PLAN_WORKSPACE_ROOT": "workspace",
        "PLAN_TICKET_SCRATCH_ROOT": "scratch",
        "PLAN_TICKET_SCRATCH_DATABASE": "private.sqlite",
        "PLAN_CREDENTIAL_KEY_PATH": "key",
        "PLAN_DISPATCHER_LOCK_PATH": "dispatcher.lock",
        "PLAN_SERVER_CONTROL_SOCKET": "control.sock",
        "PLAN_SERVER_LIFECYCLE_LEASE_PATH": "lifecycle.lock",
    }.items():
        env[key] = str(tmp_path / relative_path)
    # loadConfig requires these configured directory roots to exist before resolution.
    for key in ("PLAN_WORKSPACE_ROOT", "PLAN_TICKET_SCRATCH_ROOT"):
        Path(env[key]).mkdir()
    cli_relative = "server/cli/main.ts"
    # These real bytes prove layout only; this deliberately small source cannot execute the CLI.
    with patch.dict(os.environ, env, clear=True):
        source, _ = _git_source(tmp_path)
        cli = source / cli_relative
        cli.parent.mkdir(parents=True)
        cli.write_bytes((ROOT / cli_relative).read_bytes())
        cli.chmod(0o755)
        (source / "ignored.txt").write_text("not exported\n")
        (source / ".gitattributes").write_text("ignored.txt export-ignore\n")
        (source / "readme-link").symlink_to("README.md")
        subprocess.run(["git", "-C", str(source), "add", "."], check=True, env=env)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-qm",
                "official CLI bytes, no execution",
            ],
            check=True,
            env=env,
        )
        build_sha = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True, env=env
        ).strip()
        caller_umask = os.umask(0o027)
        try:
            python_build = build_exported_app(
                source,
                requested_sha=build_sha,
                candidate_app=tmp_path / "built-python",
                install_dependencies=False,
            )
        finally:
            os.umask(caller_umask)
    source_paths = [
        ".gitattributes",
        "README.md",
        "readme-link",
        "server",
        "server/cli",
        cli_relative,
    ]
    expected_source_metadata = [
        {key: row[key] for key in ("path", "kind", "mode", "target") if key in row}
        for row in _tree(tmp_path / "built-python")
        if row["path"] in source_paths
    ]
    assert (
        next(row for row in expected_source_metadata if row["path"] == "server/cli")["mode"]
        == 0o750
    )
    builder_spec = dict(sha=build_sha, cli=cli_relative, source_paths=source_paths)
    unit_literal = "space ' \" \\ $HOME %t\nlast"
    node_started = time.perf_counter()
    process = subprocess.Popen(
        [
            node,
            str(launcher),
            (ROOT / "server/environments/release/testing/release_parity.ts").as_uri(),
            str(tmp_path),
            json.dumps(vectors),
            json.dumps(loader_input),
            json.dumps(admission_spec),
            json.dumps(builder_spec),
            json.dumps(unit_literal),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=30)
    except subprocess.TimeoutExpired as error:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        raise AssertionError(
            f"archive comparison exceeded 30 seconds; cleanup stderr: {stderr}"
        ) from error
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    node_seconds = time.perf_counter() - node_started
    assert process.returncode == 0, stderr
    actual: dict[str, Any] = json.loads(stdout)
    actual["units"] = _unit_boundaries(tmp_path, actual["units"], node, env, unit_literal)
    assert actual["native_tree_admission"] == "app contains a symlink outside its root"
    assert native_sentinel.read_bytes() == b"outside native write sentinel\n"
    assert (
        sorted(str(item.relative_to(native_root)) for item in native_root.rglob("*"))
        == native_paths
    )
    assert actual["native_loader"] == expected_loader
    assert actual["admission"] == expected_admission
    built = actual["builder"]
    assert built["manifest"]["app_sha"] == python_build.app_sha == build_sha
    assert built["manifest"]["source_digest"] == python_build.source_digest
    assert built["manifest"]["runtime_contract"] is None and built["reused"]
    assert built["readme"] == "source\n" and built["ignored_absent"]
    assert built["cli_bytes"] == cli.read_bytes().hex() and built["cli_executable"]
    assert built["source_metadata"] == expected_source_metadata
    assert cli_relative in built["wrappers"][0]
    assert "server/environments/release/launcher_main.ts" in built["wrappers"][1]
    assert all(
        "unset NODE_OPTIONS NODE_PATH LD_PRELOAD LD_LIBRARY_PATH" in wrapper
        for wrapper in built["wrappers"]
    )
    assert built["mismatch"] == "checkout HEAD does not match requested SHA"
    assert built["failed"] and built["failed_absent"] and built["cleanup"]
    assert built["graph"] == expected_graph and built["graph_drift_refused"]
    assert built["source_abort"] == dict(
        validated=True, actual_child_closed=True, destination_absent=True, unrelated_live=True
    )
    assert built["children"]["spawned"] == built["children"]["closed"] > 0
    assert built["launch_environment"] == dict(
        HTTP_PROXY="http://synthetic.invalid",
        http_proxy="",
        REQUEST_METHOD="GET",
        PLAN_APP_ROOT=str(v2),
        PLAN_APP_SHA=SHA,
    )
    hostile_native = actual["hostile_native"]
    if hostile_native["executed"]:
        assert hostile_native["refused"] is True
        assert hostile_native["outside_library"] == str(tmp_path / "outside-native/libm.so.6")
        assert hostile_native["native_operation"] == "cos(0) = 1"
    else:
        assert hostile_native["reason"] == "Native candidate admission requires Linux x64 Node 22"
    group_proof = (
        _admission_group_proof(tmp_path / "hosted-admission-group", env, node)
        if os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform == "linux"
        else {"executed": False, "reason": "Hosted Linux only"}
    )
    assert actual["identity"] == {
        "source": digest_app_source(app),
        "artifact": digest_app_artifact(app),
        "manifest": manifest.as_dict(),
    }
    assert actual["legacy"] == expected_legacy
    assert actual["producer_trees"]["python"]["status"] == "restored", actual["producer_trees"][
        "python"
    ]
    assert actual["restored"] == manifest.as_dict()
    assert actual["exclusive_refused"]
    bad_hash = actual["hash_refusal"]
    assert bad_hash["status"] == "refused" and bad_hash["error_type"] == "AppValidationError"
    assert bad_hash["message"] == str(hash_first.value)
    assert bad_hash["spools_absent"] and not bad_hash["destination_exists"]
    assert not (tmp_path / "outside").exists()
    assert actual["node_protocol"] == dict(
        accepted=True,
        missing_refused=True,
        python_field_refused=True,
        invalid_contract_before_tree=invalid_order,
    )
    assert actual["frontier"] == python_frontier
    restored = tmp_path / "python-from-node"
    assert (
        extract_app_archive(tmp_path / "node.tar.gz", restored, actual["node_archive_sha256"], SHA)
        == manifest
    )
    python_from_python = tmp_path / "python-from-python"
    extract_app_archive(tmp_path / "python.tar.gz", python_from_python, checksum, SHA)
    for producer, python_tree in (("python", python_from_python), ("typescript", restored)):
        result_tree = actual["producer_trees"][producer]
        assert result_tree["status"] == "restored"
        assert result_tree["tree"] == _tree(python_tree)
        assert result_tree["output_private"] and result_tree["spool_private"]
        assert result_tree["settled_at_return"] and result_tree["descriptor_held"]
        assert result_tree["spools_absent"] and result_tree["spawned"] == result_tree["closed"] == 1
    for restored_tree in (tmp_path / "work-python-producer/output", restored):
        assert (restored_tree / "directory-alias").is_symlink()
        assert os.readlink(restored_tree / "directory-alias") == "a"
        assert (restored_tree / "directory-alias/data").read_text() == "a/data"
    producer_timestamps = {}
    for producer, archive in (
        ("python", tmp_path / "python.tar.gz"),
        ("typescript", tmp_path / "node.tar.gz"),
    ):
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
        names = [member.name for member in members]
        assert "directory-alias" in names
        assert "directory-alias/data" not in names
        producer_timestamps[producer] = sum(
            member.mtime != int(member.mtime)
            for member in members
            if member.isfile() or member.isdir()
        )
        if producer == "typescript":
            by_name = {member.name: member for member in members}
            assert by_name["sentinel"].mtime == FIXTURE_TIME_NS // 1_000_000_000
            assert "mtime" not in by_name["sentinel"].pax_headers
            for name in ("\ue000", "\U00010000"):
                assert by_name[name].mtime == FIXTURE_TIME_NS / 1_000_000_000
                assert float(by_name[name].pax_headers["mtime"]) == FIXTURE_TIME_NS / 1_000_000_000
    table = []
    for item in actual["archive_table"]:
        name, policy, observed = item["name"], item["policy"], item["result"]
        python_result = python_table[name]
        assert observed["settled_at_return"] and observed["spools_absent"]
        assert observed["spawned"] == observed["closed"]
        if policy == "parity" and python_result["status"] == "restored":
            assert observed["status"] == "restored", (name, observed)
            assert observed["tree"] == python_result["tree"], name
        else:
            assert observed["status"] == "refused", (name, observed)
            assert observed["error_type"] == "AppValidationError", (name, observed)
            assert not observed["destination_exists"], name
        table.append(
            dict(
                name=name,
                risk=item["risk"],
                policy=policy,
                python=python_result,
                typescript=observed,
                accepted=True,
                equality_asserted=(policy == "parity"),
                comparison=(
                    "source parity"
                    if policy == "parity"
                    else "accepted TS admission; Python outcome recorded separately"
                ),
            )
        )
    limits = {item["name"]: item["result"] for item in actual["archive_table"]}
    assert python_table["pax-leading-zero-length"]["status"] == "restored"
    hex_time = python_table["pax-hex-mtime"]
    assert hex_time["status"] == "restored"
    assert next(row for row in hex_time["tree"] if row["path"] == "hex-time")["mtime_ns"] == "0"
    assert "metadata" in limits["oversized-metadata"]["message"]
    assert "end-padding" in limits["end-padding-bomb"]["message"]
    assert (
        limits["partial-output-cleanup"]["message"]
        == "app manifest SHA does not match expected SHA"
    )
    assert limits["partial-output-cleanup"]["validated"]
    owned = {item["mode"]: item["result"] for item in actual["owned"]}
    replaced = owned["replace"]
    assert replaced["status"] == "restored" and replaced["tree"] == _tree(python_from_python)
    assert replaced["replaced_before_read"] and replaced["replaced_after_hash"]
    assert replaced["replacement"] == "replaced after hash" and replaced["descriptor_held"]
    assert owned["appeared"]["status"] == "refused"
    assert owned["appeared"]["competitor"] == "competitor"
    assert owned["kill"]["status"] == "refused" and owned["kill"]["killed"]
    assert not owned["kill"]["destination_exists"]
    channel_loss = owned["channel-loss"]
    assert channel_loss["channel_closed"] and not channel_loss["channel_close_timed_out"]
    assert (
        channel_loss["status"] == "refused" and channel_loss["error_type"] == "AppValidationError"
    )
    assert not channel_loss["destination_exists"]
    for result_owned in owned.values():
        assert result_owned["spools_absent"] and result_owned["settled_at_return"]
        assert result_owned["spawned"] == result_owned["closed"]
    messages = {
        "initial": "app archive has insufficient free space before spooling",
        "spool": "app archive exhausted free-space reserve while spooling",
        "output": "app archive has insufficient free space for output",
    }
    for phase, message in messages.items():
        result_owned = owned["low-" + phase]
        assert (
            result_owned["error_type"] == "AppValidationError"
            and result_owned["message"] == message
        )
        assert not result_owned["destination_exists"]
        assert result_owned["capacity"][-1]["phase"] == phase
        assert result_owned["spawned"] == (0 if phase == "initial" else 1)
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
                        "scope": "Small real Python/TS producer trees and identity/cache rules. "
                        "Excludes accepted archive restrictions, writer time precision "
                        "and full release scale.",
                    },
                    "archive_table": table,
                    "owned_archive_operations": actual["owned"],
                    "node_seconds": node_seconds,
                    "producer_trees": actual["producer_trees"],
                    "node_v2_protocol": actual["node_protocol"],
                    "frontier": actual["frontier"],
                    "native_static": {
                        "loader_equal": True,
                        "outside_target": actual["native_tree_admission"],
                        "full_native_closure": "OPEN; static synthetic paths only",
                    },
                    "admission_rules": actual["admission"],
                    "admission_group": group_proof,
                    "source_only_builder": built,
                    "unit_boundaries": actual["units"],
                    "public_cli_executed": False,
                    "hostile_native": hostile_native,
                    "accepted_restrictions": [
                        "GNU sparse",
                        "PAX sparse",
                        "member behind link",
                        "unknown inert PAX",
                        "empty PAX path",
                        "directory/link body",
                        "non-UTF-8 names",
                    ],
                    "writer_timestamp_policy": {
                        "accepted": True,
                        "scope": (
                            "Release application producer only; "
                            "reader retains exact effective nanoseconds"
                        ),
                        "fractional_ordinary_members": producer_timestamps,
                        "typescript_policy": (
                            "Ordinary headers can lose fractions; "
                            "written fractional PAX mtime stays intact"
                        ),
                    },
                    "limits": {
                        "oversized_header": "metadata entry bytes",
                        "compressed_bomb": "end-padding bytes",
                        "expanded_8GiB_bomb": "HOSTED OPEN; not run locally; no cap override",
                    },
                    "unproven": [
                        "Representative full release tree/memory/disk/cost on hosted root",
                        "Sealed bundled-package license inventory",
                        "Runtime execution",
                    ],
                },
                indent=2,
            )
            + "\n"
        )
