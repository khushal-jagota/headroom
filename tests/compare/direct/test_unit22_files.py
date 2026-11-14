"""Linux file custody plus source-fresh staging and command input, without transport.

The Mac proof does not exercise Linux procfs or VPS authority. The source path
reopen and shortened source stall are explicit differences, not normalized parity.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import pty
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.support.executor_presence import mac_identity, make_current
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)
from planner.conversation.lifecycle_storage import apply_facts
from planner.conversation.message_files import ConversationMessageFiles
from planner.core.clock import RealClock
from planner.core.contracts import PlannerError
from planner.core.db import connect, create_schema
from planner.executor import files as low
from planner.executor import protocol as wire
from planner.executor import vps_files
from planner.executor.configuration import FILE_CHUNK_BYTES, FILE_TRANSFER_STALL_SECONDS
from planner.executor.store import ExecutorStore
from planner.executor.vps_inbox import MacInbox
from planner.executor.vps_mailbox import SqliteCommandMailbox
from planner.executor.vps_presence import MacPresenceService
from planner.executor.vps_runs import MacRuns

ROOT = Path(__file__).resolve().parents[3]
CONTENT = b"result bytes"
IDENTITY = mac_identity("11111111-1111-4111-8111-111111111111")


def _typed(name: str, value: Any) -> Any:
    return wire.type_adapter(getattr(wire, name)).validate_json(json.dumps(value))


async def _error(work: Any) -> Any:
    try:
        await work
        return None
    except PlannerError as error:
        return {"code": error.code.value, "message": error.message}
    except Exception as error:
        return {"type": type(error).__name__, "message": str(error)}


def _safety(root: Path, manifest: wire.FileManifest) -> dict[str, Any]:
    source, target, outside = root / "source", root / "target", root / "outside"
    source.mkdir(parents=True)
    outside.mkdir()
    (source / "bytes").write_bytes(CONTENT)
    (outside / "sentinel").write_text("untouched")
    with (
        low.directory_descriptor(source) as incoming,
        low.directory_descriptor(target, create=True) as parent,
    ):
        source.rename(root / "source-held")
        source.symlink_to(outside, target_is_directory=True)
        target.rename(root / "target-held")
        target.symlink_to(outside, target_is_directory=True)

        held_source, held = root / "source-held", root / "target-held"
        rewalk = []
        for operation in (low.copy_verified, low.install_verified):
            try:
                operation(source / "bytes", target / "rewalk", manifest)
            except OSError as error:
                rewalk.append({"type": type(error).__name__, "message": str(error)})
            else:
                raise AssertionError("Python unexpectedly followed substituted namespace")
        low.copy_verified(held_source / "bytes", held / "copy", manifest)
        with pytest.raises(ValueError, match="incomplete or corrupt file"):
            low.copy_verified(
                held_source / "bytes", held / "bad", replace(manifest, sha256="0" * 64)
            )
        low.install_verified(held_source / "bytes", held / "installed", manifest)
        for leaf in ("hardlink", "device"):
            destination = held / leaf
            os.link(
                outside / "sentinel", destination
            ) if leaf == "hardlink" else destination.symlink_to("/dev/null")
            with pytest.raises(FileExistsError):
                low.copy_verified(held_source / "bytes", destination, manifest)
            low.install_verified(held_source / "bytes", destination, manifest)
            assert destination.read_bytes() == CONTENT and not destination.is_symlink()
            destination.unlink()
        descriptor = os.open(
            "created", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        os.close(descriptor)
        (root / "target-held" / "link").symlink_to(outside / "sentinel")
        refusals = []
        for flags in (
            os.O_RDONLY | os.O_NOFOLLOW,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        ):
            try:
                descriptor = os.open("link", flags, dir_fd=parent)
            except OSError:
                refusals.append(True)
            else:
                os.close(descriptor)
                refusals.append(False)
        os.unlink("link", dir_fd=parent)
        os.unlink("created", dir_fd=parent)
        os.replace("bytes", "moved", src_dir_fd=incoming, dst_dir_fd=parent)
        os.unlink("moved", dir_fd=parent)
        os.fsync(parent)
        verified = low.verified_version(held / "installed", manifest)
        valid = verified is not None and verified == low.file_version(held / "installed")
        (held / "installed").write_bytes(b"changed")
        namespace_refused = False
        try:
            low.file_digest(source / "sentinel")
        except OSError:
            namespace_refused = True
        return {
            "copied": (held / "copy").read_bytes() == CONTENT,
            "corrupt": True,
            "cleaned": not (held / "bad").exists(),
            "leaf_refused": refusals[0],
            "exclusive": refusals[1],
            "verified": valid,
            "changed": low.verified_version(held / "installed", manifest) is None,
            "private_mode": (held / "copy").stat().st_mode & 0o777,
            "directory_mode": held.stat().st_mode & 0o777,
            "namespace_refused": namespace_refused,
            "outside": [[p.name, p.read_text()] for p in sorted(outside.iterdir())],
            "rewalk": rewalk,
        }


def test_unit22_vps_files(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    master, slave = pty.openpty()

    def close_device() -> None:
        try:
            os.close(slave)
        finally:
            os.close(master)

    request.addfinalizer(close_device)
    device = Path(os.ttyname(slave))

    async def reference() -> tuple[dict[str, Any], dict[str, Any]]:
        source = tmp_path / "python"
        source.mkdir()
        path = source / "vps.sqlite"
        with closing(connect(str(path))) as connection:
            create_schema(connection)
            connection.execute(
                "INSERT INTO conversations(conversation_id,backend_key,model,"
                "workspace_folder,access,created_at) "
                "VALUES ('c','codex','fixture','/tmp','full',1760000000)"
            )
            apply_facts(
                connection,
                "c",
                (
                    LifecycleFact(
                        LifecycleKind.turn, "1", 1, LifecycleOperation.turn, LifecycleStatus.running
                    ),
                ),
            )
            connection.commit()
        make_current(str(path), IDENTITY, 1_760_000_000_000)
        child = _typed(
            "ChildReference",
            {
                "executor_incarnation": IDENTITY.executor_incarnation,
                "child_id": "child",
                "conversation_id": "c",
            },
        )
        ExecutorStore(str(path)).register_child(child, "ticket", "ticket-1", 1_760_000_000_000)
        message_files = ConversationMessageFiles(path)
        large = b"".join(bytes([index]) * FILE_CHUNK_BYTES for index in range(32))
        kept = await message_files.keep(
            "c", large, media_type="text/plain", sender_message_id="fixture"
        )
        other = await message_files.keep(
            "c", b"other", media_type="image/png", sender_message_id="other"
        )
        candidate = tmp_path / "node"
        shutil.copytree(source, candidate)
        (candidate / "safety").mkdir()
        os.mkfifo(candidate / "safety" / "fifo")
        aliases = [tmp_path / "python-alias", tmp_path / "node-alias"]
        aliases[0].symlink_to(source, target_is_directory=True)
        aliases[1].symlink_to(candidate, target_is_directory=True)
        mailbox = SqliteCommandMailbox(str(aliases[0] / "vps.sqlite"))
        presence = MacPresenceService(str(path), mailbox)
        runs = MacRuns(str(path), presence, mailbox)
        inbox = MacInbox(str(aliases[0] / "vps.sqlite"), RealClock(), runs)
        service = vps_files.VpsFiles(str(aliases[0] / "vps.sqlite"), inbox, mailbox, message_files)
        manifest = wire.FileManifest(
            wire.FileScope.ticket,
            "ticket-1",
            "out/résult.bin",
            hashlib.sha256(CONTENT).hexdigest(),
            len(CONTENT),
        )
        upload = wire.FileUploadRequest(manifest, child)

        def case(
            name: str, data: list[bytes], value: Any = upload, **changes: Any
        ) -> dict[str, Any]:
            return {
                "name": name,
                "upload": wire.encode_value(value),
                "sha": manifest.sha256,
                "length": len(CONTENT),
                "chunks": [base64.b64encode(piece).decode() for piece in data],
                **changes,
            }

        cases = [
            case("good", [CONTENT[:5], CONTENT[5:]]),
            case("hash-mismatch", [], sha="0" * 64),
            case("length-mismatch", [], length=11),
            case("short", [CONTENT[:-1]]),
            case("overlong", [CONTENT + b"!"]),
            case("corrupt", [b"X" * len(CONTENT)]),
            case(
                "foreign",
                [CONTENT],
                replace(upload, manifest=replace(manifest, owner_id="ticket-2")),
            ),
            case(
                "unassigned", [CONTENT], replace(upload, child=replace(child, child_id="stranger"))
            ),
            case("drop", [CONTENT[:5]], drop=True),
        ]
        main = [
            {
                "stored_file_id": other.stored_file_id,
                "media_type": "image/png",
                "file_name": "other.png",
            }
        ]
        sender = [
            {
                "stored_file_id": kept.stored_file_id,
                "media_type": "text/plain",
                "file_name": "notes.txt",
                "byte_count": kept.byte_count,
            }
        ]

        def command(name: str, operation: str) -> dict[str, Any]:
            args: dict[str, Any] = {
                "content": main,
                "sender_content": sender,
                "sender_label": "owner",
            }
            if operation == "write_prompt":
                args["mode"] = "queue"
            args["kind"] = operation
            return {
                "command_id": name,
                "child": wire.encode_value(child),
                "operation": operation,
                "turn": {"conversation_id": "c", "turn_number": 1},
                "arguments": args,
            }

        commands = [
            command("prompt", "write_prompt"),
            command("withdrawn", "write_prompt"),
            command("steer", "steer"),
        ]

        async def chunks(entry: dict[str, Any]) -> AsyncIterator[bytes]:
            for piece in entry["chunks"]:
                yield base64.b64decode(piece)
            if entry.get("drop"):
                raise TypeError("fixture disconnect")

        expected: dict[str, Any] = {
            "safety": _safety(source / "safety", manifest),
            "constants": [FILE_CHUNK_BYTES, FILE_TRANSFER_STALL_SECONDS],
            "constructor_root": service.staging == source / "executor-uploads",
            "staging_keys": [
                inbox.store._staged_file(value).name
                for value in (manifest, replace(manifest, byte_count=123456789))
            ],
            "uploads": [],
        }
        peer_paths = [
            (name, text, "")
            for name, text in (
                ("absolute", "/outside"),
                ("parent", "../file"),
                ("dot", "."),
                ("empty", ""),
                ("backslash", "a\\b"),
                ("nul", "a\0b"),
                ("percent-dot", "%2e/file"),
                ("percent-slash", "%5c/file"),
                ("long", "x" * 300),
                ("middle", "middle/file"),
            )
        ] + [("ancestor-loop", "file", "loop/child")]
        path_root = source / "paths"
        path_root.mkdir()
        (path_root / "middle").symlink_to(".", target_is_directory=True)
        (path_root / "loop").symlink_to("loop")
        expected["paths"] = [
            dict(
                name=name,
                error=await _error(asyncio.to_thread(low.contained_path, path_root / prefix, text)),
            )
            for name, text, prefix in peer_paths
        ]
        before_device = device.lstat()
        expected["device"] = dict(
            error=await _error(
                asyncio.to_thread(
                    low.copy_verified, source / "safety" / "target-held" / "copy", device, manifest
                )
            ),
            contained=low.contained_path(device.parent, device.name) == device,
        )
        after_device = device.lstat()
        expected["device"]["unchanged"] = (before_device.st_dev, before_device.st_ino) == (
            after_device.st_dev,
            after_device.st_ino,
        ) and stat.S_ISCHR(after_device.st_mode)
        try:
            for entry in cases:
                error = await _error(
                    service.upload(
                        IDENTITY,
                        entry["sha"],
                        _typed("FileUploadRequest", entry["upload"]),
                        entry["length"],
                        chunks(entry),
                    )
                )
                expected["uploads"].append(
                    {
                        "name": entry["name"],
                        "error": error,
                        "incoming": len(list(service.staging.glob(".incoming-*"))),
                    }
                )
            expected["staged"] = hashlib.sha256(
                inbox.store._staged_file(manifest).read_bytes()
            ).hexdigest()
            expected["served"] = inbox.store.file_target(manifest).exists()
            for value in commands:
                await mailbox.put_child_command(_typed("ChildCommand", value))
            physical = source / "database-target"
            physical.mkdir()
            (physical / "state.sqlite").touch()
            filename_alias = source / "filename.sqlite"
            filename_alias.symlink_to(physical / "state.sqlite")
            alternate = vps_files.VpsFiles(
                str(filename_alias), inbox, mailbox, ConversationMessageFiles(filename_alias)
            )
            alternate_input = await alternate.input_file(IDENTITY, "prompt", kept.stored_file_id)
            expected["filename_roots"] = [
                alternate.staging == physical / "executor-uploads",
                alternate_input.sha256 == hashlib.sha256(large).hexdigest(),
            ]
            served = await service.input_file(IDENTITY, "prompt", kept.stored_file_id)
            expected["input"] = [served.sha256, served.byte_count]
            held_path = served.path.with_name(served.path.name + "-held")
            served.path.rename(held_path)
            try:
                served.path.symlink_to(held_path.name)
                linked = await service.input_file(IDENTITY, "prompt", kept.stored_file_id)
                expected["leaf_symlinks"] = [[linked.sha256, linked.byte_count]]
                served.path.unlink()
                served.path.symlink_to(source / "safety" / "outside" / "sentinel")
                expected["leaf_symlinks"].append(
                    await _error(service.input_file(IDENTITY, "prompt", kept.stored_file_id))
                )
            finally:
                served.path.unlink(missing_ok=True)
                held_path.rename(served.path)
            await mailbox.poll(IDENTITY, 0, 0)
            assert await service.input_file(IDENTITY, "prompt", kept.stored_file_id) == served
            expected["missing"] = [
                await _error(service.input_file(IDENTITY, "prompt", other.stored_file_id)),
                await _error(service.input_file(mac_identity(), "prompt", kept.stored_file_id)),
            ]
            await mailbox.record_results(
                IDENTITY,
                _typed(
                    "CommandOutcomeReport",
                    {
                        "outcomes": [
                            {
                                "command_id": "prompt",
                                "child_outcome": {
                                    "command_id": "prompt",
                                    "child": wire.encode_value(child),
                                    "disposition": "succeeded",
                                    "result": {},
                                },
                            }
                        ]
                    },
                ),
            )
            expected["missing"].append(
                await _error(service.input_file(IDENTITY, "prompt", kept.stored_file_id))
            )
            # Poll delivered all three: restore just this fixture row before source withdrawal.
            with closing(connect(str(path))) as connection:
                connection.execute(
                    "UPDATE executor_commands SET state='waiting' WHERE command_id='withdrawn'"
                )
                connection.commit()
            await mailbox.withdraw_waiting("withdrawn", _typed_reason())
            expected["missing"].append(
                await _error(service.input_file(IDENTITY, "withdrawn", kept.stored_file_id))
            )
            assert (
                await service.input_file(IDENTITY, "steer", kept.stored_file_id)
            ).sha256 == served.sha256
            monkeypatch.setattr(vps_files, "FILE_TRANSFER_STALL_SECONDS", 0.2)

            async def stalled() -> AsyncIterator[bytes]:
                yield CONTENT[:5]
                await asyncio.Future()

            before = time.perf_counter()
            stall = await _error(
                service.upload(IDENTITY, manifest.sha256, upload, manifest.byte_count, stalled())
            )
            expected["source_stall"] = {
                "error": stall,
                "seconds": time.perf_counter() - before,
                "incoming": len(list(service.staging.glob(".incoming-*"))),
            }
            # Source returns a pathname. A later reopen demonstrably selects replacement bytes.
            served.path.rename(served.path.with_name(served.path.name + "-held"))
            served.path.write_bytes(b"replacement")
            expected["source_reopen"] = hashlib.sha256(served.path.read_bytes()).hexdigest()
            spec = {
                "device": str(device),
                "paths": peer_paths,
                "root": str(candidate),
                "path": str(aliases[1] / "vps.sqlite"),
                "identity": wire.encode_value(IDENTITY),
                "child": wire.encode_value(child),
                "manifest": wire.encode_value(manifest),
                "big": wire.encode_value(replace(manifest, byte_count=123456789)),
                "ids": [kept.stored_file_id, other.stored_file_id],
                "commands": commands,
                "uploads": cases,
            }
            return expected, spec
        finally:
            await runs.close()
            await presence.close()
            await mailbox.close()

    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    with synthetic_home_environment(homes):
        expected, spec = asyncio.run(asyncio.wait_for(reference(), 15))
    environment = {
        **homes,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path),
        "PLAN_TEST_MODE": "1",
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
    }
    # subprocess.run owns/joins the exact companion, including TimeoutExpired cleanup.
    run = subprocess.run(
        ["node", str(ROOT / "server/executor/testing/files.ts")],
        input=json.dumps(spec),
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    actual = json.loads(run.stdout)
    def validation_meaning(value: Any) -> Any:
        if isinstance(value, list):
            return [validation_meaning(item) for item in value]
        if isinstance(value, dict):
            result = {key: validation_meaning(item) for key, item in value.items()}
            if result.get("type") in ("ValueError", "ExecutorValidationError"):
                result["type"] = "validation"
            return result
        return value

    actual, expected = validation_meaning(actual), validation_meaning(expected)
    assert expected["device"]["contained"] and actual["device"]["contained"]
    assert expected["device"]["unchanged"] and actual["device"]["unchanged"]
    assert expected["device"]["error"]["type"] == "FileExistsError"
    assert "EEXIST" in actual["device"]["error"]["message"]
    assert actual["safety"] == {
        key: value for key, value in expected["safety"].items() if key != "rewalk"
    }
    assert len(expected["safety"]["rewalk"]) == 2
    for source_case, candidate_path in zip(expected["paths"], actual["paths"], strict=True):
        name = source_case["name"]
        if name not in ("long", "ancestor-loop"):
            assert candidate_path == source_case
        elif name == "long":
            assert "File name too long" in source_case["error"]["message"]
            assert "ENAMETOOLONG" in candidate_path["error"]["message"]
        else:
            assert candidate_path["error"] == {
                "type": "validation",
                "message": "artifact path contains symlink",
            }
            if source_case["error"] is None:
                # Python 3.13's non-strict Path.resolve retains this cyclic suffix.
                # Native descriptor admission still refuses the substituted ancestor.
                assert sys.version_info >= (3, 13)
            else:
                assert source_case["error"]["type"] == "RuntimeError"
                assert source_case["error"]["message"].startswith("Symlink loop from ")
    for key in (
        "constants",
        "constructor_root",
        "filename_roots",
        "staging_keys",
        "uploads",
        "staged",
        "served",
        "input",
        "leaf_symlinks",
        "missing",
    ):
        assert actual[key] == expected[key], key
    assert actual["large"]["sha256"] == expected["input"][0]
    assert actual["large"]["count"] == expected["input"][1] == 8 * 1024 * 1024
    assert actual["upload_swap"] and actual["zero_budget"] and actual["sealed"]
    assert expected["source_stall"]["error"] == {
        "code": "retry_later",
        "message": "the upload stopped moving; send it again",
    }
    assert expected["source_stall"]["incoming"] == 0
    directory = request.config.getoption("--compare-report-dir")
    if directory:
        report = Path(directory) / "unit-22-files.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {
                    "elapsed_seconds": time.perf_counter() - started,
                    "source": expected,
                    "candidate": actual,
                },
                indent=2,
            )
            + "\n"
        )


def _typed_reason() -> Any:
    from planner.executor.contracts import CommandSettlementReason

    return CommandSettlementReason.delivery_withdrawn
