"""Tailnet association and bounded local-process boundary; no live tailnet."""

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from planner.executor import tailscale
from planner.executor.contracts import TailnetPeer, TailnetStatus, TailnetValidationError
from planner.executor.logic.tailnet import select_tailnet_destination

NOW = datetime(2026, 10, 25, tzinfo=UTC)
PEER = TailnetPeer(
    "node1", "mac.example.ts.net.", ("fd7a:115c:a1e0::2", "100.100.1.2"), True, False, None
)
STATUS = TailnetStatus(True, True, (PEER,))


def status_bytes(**overrides: object) -> bytes:
    peer: dict[str, object] = {
        "ID": "node1",
        "DNSName": "mac.example.ts.net.",
        "TailscaleIPs": ["100.100.1.2"],
        "InNetworkMap": True,
    }
    peer.update(overrides)
    return json.dumps({"BackendState": "Running", "TUN": True, "Peer": {"key": peer}}).encode()


def executable(tmp_path: Path, body: str) -> Path:
    binary = tmp_path / "tailscale"
    binary.write_text("#!/usr/bin/env python3\n" + body)
    binary.chmod(0o700)
    return binary


@pytest.mark.parametrize("host", ["mac.example.ts.net", "MAC.EXAMPLE.TS.NET.", "100.100.1.2"])
def test_exact_association_selects_numeric_v4(host: str) -> None:
    selected = select_tailnet_destination(host, STATUS, NOW)
    assert (selected.node_id, selected.address) == ("node1", "100.100.1.2")


def test_literal_v6_retains_that_address() -> None:
    assert select_tailnet_destination("fd7a:115c:a1e0::2", STATUS, NOW).address == PEER.addresses[0]


@pytest.mark.parametrize("host", ["mac", "example.ts.net", "mac.other.ts.net", "192.168.1.1"])
def test_no_alias_dns_or_subnet_fallback(host: str) -> None:
    with pytest.raises(TailnetValidationError):
        select_tailnet_destination(host, STATUS, NOW)


@pytest.mark.parametrize(
    "status",
    [
        replace(STATUS, running=False),
        replace(STATUS, kernel_networking=False),
        replace(STATUS, peers=(PEER, PEER)),
        replace(STATUS, peers=(replace(PEER, expired=True),)),
        replace(STATUS, peers=(replace(PEER, in_network_map=False),)),
        replace(STATUS, peers=(replace(PEER, node_id=""),)),
        replace(STATUS, peers=(replace(PEER, addresses=()),)),
        replace(STATUS, peers=(replace(PEER, key_expiry=NOW),)),
        replace(STATUS, peers=(replace(PEER, key_expiry=NOW - timedelta(seconds=1)),)),
        replace(STATUS, peers=(replace(PEER, addresses=("1.2.3.4",)),)),
        replace(STATUS, peers=(replace(PEER, addresses=("fd7a:115c:a1e0::2%en0",)),)),
    ],
)
def test_missing_current_unique_evidence_refuses(status: TailnetStatus) -> None:
    with pytest.raises(TailnetValidationError):
        select_tailnet_destination("mac.example.ts.net", status, NOW)


@pytest.mark.parametrize(
    "fields",
    [
        {"ID": 1},
        {"ID": ""},
        {"DNSName": None},
        {"TailscaleIPs": "100.100.1.2"},
        {"TailscaleIPs": [1]},
        {"InNetworkMap": 1},
        {"Expired": None},
        {"KeyExpiry": "tomorrow"},
        {"KeyExpiry": "2027-01-01"},
        {"KeyExpiry": "2027-99-01T00:00:00Z"},
    ],
)
def test_parser_checks_documented_field_types(fields: dict[str, object]) -> None:
    with pytest.raises(TailnetValidationError):
        tailscale.parse_tailnet_status(status_bytes(**fields))


@pytest.mark.parametrize("output", [b"secret", b"[]", b"{}", b'{"Peer":[]}'])
def test_parser_does_not_echo_invalid_output(output: bytes) -> None:
    with pytest.raises(TailnetValidationError) as caught:
        tailscale.parse_tailnet_status(output)
    assert "secret" not in str(caught.value)


def test_parser_optional_expiry_and_unknown_fields() -> None:
    status = tailscale.parse_tailnet_status(
        status_bytes(Unrelated="ignored", AllowedIPs=["1.2.3.4/32"])
    )
    assert not status.peers[0].expired
    assert status.peers[0].key_expiry is None
    status = tailscale.parse_tailnet_status(status_bytes(KeyExpiry="2027-01-01T00:00:00.123Z"))
    assert status.peers[0].key_expiry == datetime(2027, 1, 1, microsecond=123000, tzinfo=UTC)
    assert select_tailnet_destination("mac.example.ts.net", status, NOW).address == "100.100.1.2"


def test_real_child_argv_stdin_and_stderr(tmp_path: Path) -> None:
    binary = executable(
        tmp_path,
        f"""import sys
assert sys.argv[1:] == ["status", "--json"]
assert sys.stdin.read() == ""
sys.stderr.write("secret stderr" * 100000)
sys.stdout.buffer.write({status_bytes()!r})
""",
    )
    result = asyncio.run(
        tailscale.TailscaleDestinationValidator().validate("mac.example.ts.net", binary)
    )
    assert result.address == "100.100.1.2"


def test_output_cap_terminates_exact_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tailscale, "TAILSCALE_STATUS_MAX_BYTES", 32)
    binary = executable(
        tmp_path,
        'import sys,time\nsys.stdout.write("x"*100000)\nsys.stdout.flush()\ntime.sleep(30)\n',
    )
    validator = tailscale.TailscaleDestinationValidator()
    with pytest.raises(TailnetValidationError, match="limit"):
        asyncio.run(validator.validate("mac.example.ts.net", binary))
    assert validator._process is None


def test_timeout_kills_child_ignoring_termination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tailscale, "TAILSCALE_STATUS_TIMEOUT_SECONDS", 0.15)
    monkeypatch.setattr(tailscale, "TAILSCALE_TERMINATE_TIMEOUT_SECONDS", 0.03)
    pid_file = tmp_path / "child.pid"
    binary = executable(
        tmp_path,
        f"""import signal,time,os
signal.signal(signal.SIGTERM, signal.SIG_IGN)
open({str(pid_file)!r},"w").write(str(os.getpid()))
time.sleep(30)
""",
    )
    validator = tailscale.TailscaleDestinationValidator()
    with pytest.raises(TailnetValidationError, match="unavailable"):
        asyncio.run(validator.validate("mac.example.ts.net", binary))
    assert validator._process is None
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def test_cancellation_cleanup_and_concurrent_probe_refusal(tmp_path: Path) -> None:
    binary = executable(tmp_path, "import time\ntime.sleep(30)\n")
    validator = tailscale.TailscaleDestinationValidator()

    async def exercise() -> None:
        task = asyncio.create_task(validator.validate("mac.example.ts.net", binary))
        while validator._process is None:
            await asyncio.sleep(0.001)
        process = validator._process
        with pytest.raises(TailnetValidationError, match="previous"):
            await validator.validate("mac.example.ts.net", binary)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.returncode is not None
        assert validator._process is None

    asyncio.run(exercise())


def test_unreaped_child_retained_and_replacement_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from typing import cast
    from unittest.mock import MagicMock

    monkeypatch.setattr(tailscale, "TAILSCALE_TERMINATE_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(tailscale, "TAILSCALE_KILL_TIMEOUT_SECONDS", 0.001)
    child = MagicMock(spec=asyncio.subprocess.Process)
    child.stdout = None
    child.returncode = None

    async def never_reaped() -> int:
        await asyncio.sleep(10)
        return 0

    child.wait.side_effect = never_reaped
    validator = tailscale.TailscaleDestinationValidator()
    process = cast(asyncio.subprocess.Process, child)
    validator._process = process

    async def exercise() -> None:
        await validator._cleanup(process)
        assert validator._process is process
        with pytest.raises(TailnetValidationError, match="previous"):
            await validator.validate("mac.example.ts.net", Path("/unused"))

    asyncio.run(exercise())
    child.terminate.assert_called_once_with()
    child.kill.assert_called_once_with()
