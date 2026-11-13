"""Explicit hosted proof, not a pytest case or a release publisher."""

from __future__ import annotations

import gzip
import io
import json
import os
import resource
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any, cast

SOURCE = Path(__file__).resolve().parents[3]
CHUNK = 64 * 1024
active: subprocess.Popen[bytes] | None = None
active_graceful = False
force_at: float | None = None
stopping = False
phases: list[dict[str, Any]] = []


def stop(_signal: int, _frame: Any) -> None:
    global stopping, force_at
    if stopping:
        return
    stopping = True
    if active is not None and active.returncode is None:
        try:
            if active_graceful:
                os.kill(active.pid, signal.SIGTERM)
                force_at = time.monotonic() + 5
            else:
                os.killpg(active.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def command(
    arguments: list[str], log: Path, *, timeout_seconds: float, graceful: bool = False
) -> dict[str, Any]:
    """Publish the exact group before signals; withdraw authority before reap is visible."""
    global active, active_graceful, force_at
    if stopping:
        raise RuntimeError("hosted proof stopped")
    started = time.monotonic()
    deadline = started + timeout_seconds
    timed_out = False
    with log.open("xb") as output:
        previous = signal.pthread_sigmask(
            signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT}
        )
        try:
            if stopping:
                raise RuntimeError("hosted proof stopped before command admission")
            if time.monotonic() >= deadline:
                phases[-1]["owned_command"] = dict(
                    timeout_seconds=timeout_seconds,
                    deadline_monotonic=deadline,
                    timed_out=True,
                    spawned=False,
                )
                raise TimeoutError("hosted command deadline expired before admission")
            child = subprocess.Popen(
                arguments,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            active = child
            active_graceful, force_at = graceful, None
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        try:
            while True:
                previous = signal.pthread_sigmask(
                    signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT}
                )
                try:
                    now = time.monotonic()
                    if not timed_out and now >= deadline:
                        timed_out = True
                        try:
                            if graceful:
                                os.kill(child.pid, signal.SIGTERM)
                                force_at = min(force_at or deadline + 5, deadline + 5)
                            else:
                                os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    if force_at is not None and time.monotonic() >= force_at:
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        force_at = None
                    observed = os.waitid(
                        os.P_PID, child.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG
                    )
                    if observed is not None:
                        # Retain the zombie leader's identity until its group is
                        # forced, including on a nominally successful command.
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                            group_force = "SIGKILL delivered before leader reap"
                        except ProcessLookupError:
                            group_force = "group already empty before leader reap"
                        _, status, usage = os.wait4(child.pid, 0)
                        child.returncode = os.waitstatus_to_exitcode(status)
                        active = None
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous)
                if observed is not None:
                    break
                time.sleep(0.05)
        finally:
            if active is child:
                previous = signal.pthread_sigmask(
                    signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT}
                )
                try:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    _, status, _ = os.wait4(child.pid, 0)
                    child.returncode = os.waitstatus_to_exitcode(status)
                    active = None
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous)
    timed_out = timed_out or time.monotonic() >= deadline
    outcome = dict(
        exit_code=child.returncode,
        user_seconds=usage.ru_utime,
        system_seconds=usage.ru_stime,
        wait4_maxrss_kib=usage.ru_maxrss,
        group_force=group_force,
        timeout_seconds=timeout_seconds,
        deadline_monotonic=deadline,
        seconds=time.monotonic() - started,
        timed_out=timed_out,
    )
    phases[-1]["owned_command"] = outcome
    if stopping or timed_out or child.returncode:
        with log.open("rb") as output:
            output.seek(max(0, log.stat().st_size - 4000))
            raise RuntimeError(
                f"owned command exited {child.returncode}, timed_out={timed_out}: "
                + output.read().decode(errors="replace")
            )
    return outcome


def phase(name: str, work: Callable[..., Any], *arguments: Any, **keywords: Any) -> Any:
    before = resource.getrusage(resource.RUSAGE_SELF)
    started = time.monotonic()
    record: dict[str, Any] = dict(name=name, completed=False)
    phases.append(record)
    try:
        result = work(*arguments, **keywords)
        record["completed"] = True
        return result
    finally:
        after = resource.getrusage(resource.RUSAGE_SELF)
        record.update(
            seconds=time.monotonic() - started,
            driver_user_seconds=after.ru_utime - before.ru_utime,
            driver_system_seconds=after.ru_stime - before.ru_stime,
        )


def paths(root: Path) -> list[Path]:
    return sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())


def compare(left: Path, right: Path, record: dict[str, Any]) -> dict[str, Any]:
    def names(root: Path) -> list[str]:
        return [path.relative_to(root).as_posix() for path in paths(root)]

    left_names, right_names = names(left), names(right)
    if left_names != right_names:
        raise AssertionError("extracted path sets differ")
    inodes: list[dict[tuple[int, int], str]] = [{}, {}]
    mismatches: list[dict[str, Any]] = []
    categories: dict[str, int] = {}
    total = 0
    for name in left_names:
        if stopping:
            raise RuntimeError("hosted proof stopped")
        pair = [left / name, right / name]
        information = [path.lstat() for path in pair]
        if stat.S_IFMT(information[0].st_mode) != stat.S_IFMT(information[1].st_mode):
            raise AssertionError(f"file kinds differ: {name}")
        differences: dict[str, Any] = {}
        modes = [stat.S_IMODE(info.st_mode) for info in information]
        if modes[0] != modes[1]:
            differences["mode"] = modes
        if stat.S_ISLNK(information[0].st_mode):
            targets = [os.readlink(path) for path in pair]
            if targets[0] != targets[1]:
                differences["symlink_target"] = targets
        else:
            times = [str(info.st_mtime_ns) for info in information]
            if times[0] != times[1]:
                differences["mtime_ns"] = times
            if stat.S_ISREG(information[0].st_mode):
                lengths = [info.st_size for info in information]
                if lengths[0] != lengths[1]:
                    differences["length"] = lengths
                links = []
                for index, info in enumerate(information):
                    key = (info.st_dev, info.st_ino)
                    links.append(inodes[index].setdefault(key, name))
                if links[0] != links[1]:
                    differences["hardlink_identity"] = links
                bytes_equal = True
                with pair[0].open("rb") as first, pair[1].open("rb") as second:
                    while True:
                        if stopping:
                            raise RuntimeError("hosted proof stopped")
                        data, other = first.read(CHUNK), second.read(CHUNK)
                        bytes_equal &= data == other
                        if not data and not other:
                            break
                if not bytes_equal:
                    differences["bytes"] = "different; payload bytes omitted"
        if differences:
            total += 1
            for category in differences:
                categories[category] = categories.get(category, 0) + 1
            if len(mismatches) < 20:
                mismatches.append(dict(path=name, differences=differences))
    result = dict(
        equal=not total,
        paths=len(left_names),
        mtime="exact filesystem st_mtime_ns; no tolerance",
        mismatch_count=total,
        mismatch_categories=categories,
        first_mismatches=mismatches,
        value_order=["typescript", "python"],
    )
    record["tree"] = result
    if total:
        raise AssertionError(
            f"{total} trees differ in bytes/mode/link/mtime: {mismatches}"
        )
    return result


class Counted(io.BufferedIOBase):
    def __init__(self, stream: Any) -> None:
        self.stream, self.bytes = stream, 0

    def read(self, size: int | None = -1, /) -> bytes:
        data: bytes = self.stream.read(size)
        self.bytes += len(data)
        return data


def describe(archive: Path) -> dict[str, Any]:
    counts: dict[str, int] = {}
    maximum, depth = 0, 0
    pax: set[str] = set()
    with gzip.open(archive, "rb") as expanded:
        counted = Counted(expanded)
        with tarfile.open(fileobj=counted, mode="r|") as bundle:
            for member in bundle:
                kind = member.type.hex()
                counts[kind] = counts.get(kind, 0) + 1
                maximum = max(maximum, member.size)
                depth = max(
                    depth,
                    len(Path(member.name).parts),
                    len(Path(member.linkname).parts),
                )
                pax.update(member.pax_headers)
        while counted.read(CHUNK):
            pass
    return dict(
        effective_members=counts,
        expanded_bytes=counted.bytes,
        maximum_member_bytes=maximum,
        maximum_path_link_components=depth,
        pax_keys=sorted(pax),
        raw_headers_and_metadata_bytes_and_padding="unmeasured public tarfile interface",
    )


def remove(*roots: Path) -> None:
    for root in roots:
        if root.exists():
            root.chmod(0o700)
            for directory, names, _ in os.walk(root, followlinks=False):
                Path(directory).chmod(0o700)
                for name in names:
                    path = Path(directory) / name
                    if not path.is_symlink():
                        path.chmod(0o700)
            shutil.rmtree(root)


def main(root: Path, report: Path, sha: str) -> None:
    import planner
    from planner.environments.app import digest_app_artifact, validate_app_manifest
    from planner.environments.update_artifacts import (
        create_app_archive,
        extract_app_archive,
        hash_archive,
    )

    if os.environ.get("GITHUB_ACTIONS") != "true" or not report.is_relative_to(root):
        raise RuntimeError("hosted private proof only")
    if Path(planner.__file__).resolve().parent != SOURCE / "src/planner":
        raise RuntimeError("proof imports a different planner checkout")
    corpus = root / "corpus"
    corpus.mkdir(mode=0o700)
    receipt: dict[str, Any] = dict(
        sha=sha,
        phases=phases,
        producers=[],
        completed=False,
        memory="raw Linux wait4 ru_maxrss per command, not aggregate process-tree peak",
        cleanup_boundary=(
            "Node TERM lets its owner join detached unpack. Hard outer-group fallback "
            "cannot confirm a separately detached child; any fallback remains a failed proof."
        ),
        tools={
            name: shutil.which(name)
            for name in ("python3", "node", "patchelf", "readelf")
        },
        open=[
            "Node v2 release",
            "sealed Node package licenses",
            "runtime admission/start",
            "cutover",
            "release-scale cancellation/responsiveness: this proof exercises "
            "successful full-tree extraction",
        ],
    )
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True, timeout=10
            ).strip()
            != sha
        ):
            raise RuntimeError("proof checkout SHA mismatch")
        app = corpus / "app"
        phase(
            "python-build",
            command,
            [
                sys.executable,
                "-I",
                "-B",
                str(Path(__file__).resolve()),
                "--build",
                str(app),
                sha,
            ],
            root / "build.log",
            timeout_seconds=1800,
        )
        manifest = validate_app_manifest(app / "manifest.json", require_runtime=True)
        receipt["manifest"] = manifest.as_dict()
        versions = [
            app / "agent_backends/node_modules" / name / "package.json"
            for name in ("@anthropic-ai/claude-code", "@openai/codex")
        ]
        receipt["agent_packages"] = {
            json.loads(path.read_text())["name"]: json.loads(path.read_text())[
                "version"
            ]
            for path in versions
        }
        launcher = root / "archive.mjs"
        launcher.write_text(
            "const {hosted_archive}=await import(process.argv[2]);"
            "await hosted_archive(JSON.parse(process.argv[3]));\n"
        )

        def node(
            operation: str,
            producer: str,
            archive: Path,
            node_tree: Path,
            record: dict[str, Any],
        ) -> dict[str, Any]:
            output = root / f"{producer}-{operation}.json"
            request = dict(
                operation=operation,
                app=str(app),
                archive=str(archive),
                destination=str(node_tree),
                sha=sha,
                checksum=record.get("checksum", ""),
                report=str(output),
            )
            command(
                [
                    shutil.which("node") or "node",
                    str(launcher),
                    (
                        SOURCE / "server/environments/release/testing/hosted_archive.ts"
                    ).as_uri(),
                    json.dumps(request),
                ],
                root / f"{producer}-{operation}.log",
                timeout_seconds=600,
                graceful=True,
            )
            return cast(dict[str, Any], json.loads(output.read_text()))

        # This factory boundary previously had recorded argv and syntax proof only.
        # Reuse the genuine built candidate, not host Python or the structural fixture.
        from planner.environments.update_install import migration_graph

        graph = migration_graph(app)
        heads = set(graph) - {
            parent for parents in graph.values() for parent in parents
        }
        nonheads = sorted(set(graph) - heads)
        if not heads or not nonheads:
            raise RuntimeError(
                "the real candidate needs heads and a known non-head revision"
            )
        schema_cases: list[dict[str, Any]] = []
        for name, revisions, expected in (
            ("heads", sorted(heads), False),
            ("nonhead", [nonheads[0]], True),
            (
                "unknown",
                ["hosted-unknown-revision"],
                "This release does not know the database's schema revision.",
            ),
        ):
            database = root / f"schema-{name}.sqlite"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE alembic_version(version_num TEXT)")
                connection.executemany(
                    "INSERT INTO alembic_version VALUES(?)",
                    [(value,) for value in revisions],
                )
            schema_cases.append(dict(database=str(database), expected=expected))
        schema_report = root / "v1-schema.json"
        phase(
            "candidate-v1-schema-wrapper",
            command,
            [
                shutil.which("node") or "node",
                str(launcher),
                (
                    SOURCE / "server/environments/release/testing/hosted_archive.ts"
                ).as_uri(),
                json.dumps(
                    dict(
                        operation="schema",
                        app=str(app),
                        sha=sha,
                        report=str(schema_report),
                        cases=schema_cases,
                    )
                ),
            ],
            root / "v1-schema.log",
            timeout_seconds=145,
            graceful=True,
        )
        receipt["candidate_v1_schema"] = json.loads(schema_report.read_text())

        for producer in ("python", "typescript"):
            archive, node_tree, oracle = [
                corpus / value for value in ("app.tar.gz", "node", "oracle")
            ]
            record: dict[str, Any] = dict(producer=producer)
            receipt["producers"].append(record)

            if producer == "python":
                record["checksum"] = phase(
                    producer + "-pack", create_app_archive, app, archive
                )
            else:
                record["checksum"] = phase(
                    producer + "-pack",
                    node,
                    "pack",
                    producer,
                    archive,
                    node_tree,
                    record,
                )["checksum"]
            record["archive_bytes"] = archive.stat().st_size
            record["corpus"] = phase(producer + "-scan", describe, archive)
            record["node"] = phase(
                producer + "-node-unpack",
                node,
                "unpack",
                producer,
                archive,
                node_tree,
                record,
            )
            if record["node"]["expanded_bytes"] != record["corpus"]["expanded_bytes"]:
                raise AssertionError(
                    "validated spool and public gzip byte counts differ"
                )
            phase(
                producer + "-python-unpack",
                extract_app_archive,
                archive,
                oracle,
                record["checksum"],
                sha,
            )
            record["tree"] = phase(
                producer + "-compare", compare, node_tree, oracle, record
            )
            if (
                hash_archive(archive) != record["checksum"]
                or digest_app_artifact(app) != manifest.artifact_digest
            ):
                raise AssertionError("archive or source payload changed")
            if list(corpus.glob(".app-unpack-*")):
                raise AssertionError("owned unpack spool remains")
            phase(producer + "-cleanup", remove, node_tree, oracle)
            archive.unlink()
        receipt["completed"] = True
    except BaseException as error:
        receipt["failure"] = str(error)[:4000]
        raise
    finally:
        try:
            phase("final-cleanup", remove, corpus)
        except BaseException as error:
            receipt.update(
                completed=False, failure="final cleanup: " + str(error)[:4000]
            )
            raise
        finally:
            report.write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("hosted archive proof only")
    if sys.argv[1] == "--build":
        from planner.environments.app import build_exported_app

        build_exported_app(
            SOURCE,
            requested_sha=sys.argv[3],
            candidate_app=Path(sys.argv[2]),
            install_dependencies=True,
        )
    else:
        main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), sys.argv[3])
