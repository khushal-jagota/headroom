"""Bounded, read-only local CLI adapter. Peer association is not authentication."""

import asyncio
import contextlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from planner.executor.configuration import (
    TAILSCALE_KILL_TIMEOUT_SECONDS,
    TAILSCALE_STATUS_MAX_BYTES,
    TAILSCALE_STATUS_TIMEOUT_SECONDS,
    TAILSCALE_TERMINATE_TIMEOUT_SECONDS,
)
from planner.executor.contracts import (
    TailnetPeer,
    TailnetStatus,
    TailnetValidationError,
    ValidatedTailnetDestination,
)
from planner.executor.logic.tailnet import select_tailnet_destination

_RFC3339 = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TailnetValidationError("invalid status object")
    return cast(dict[str, object], value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise TailnetValidationError("invalid status string")
    return value


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise TailnetValidationError("invalid status boolean")
    return value


def parse_tailnet_status(output: bytes) -> TailnetStatus:
    """Translate only the documented fields, without exposing daemon output."""
    try:
        data = _mapping(json.loads(output))
        backend_state = _string(data.get("BackendState"))
        kernel = _boolean(data.get("TUN"))
        peers = []
        for raw in _mapping(data.get("Peer")).values():
            peer = _mapping(raw)
            node_id = _string(peer.get("ID"))
            if not node_id:
                raise TailnetValidationError("missing peer identity")
            dns_name = _string(peer.get("DNSName"))
            raw_addresses = peer.get("TailscaleIPs")
            if not isinstance(raw_addresses, list):
                raise TailnetValidationError("missing peer addresses")
            addresses = tuple(_string(value) for value in raw_addresses)
            expiry = peer.get("KeyExpiry")
            key_expiry = None
            if expiry is not None:
                expiry_text = _string(expiry)
                if _RFC3339.fullmatch(expiry_text) is None:
                    raise TailnetValidationError("invalid key expiry")
                key_expiry = datetime.fromisoformat(expiry_text.replace("Z", "+00:00"))
            peers.append(
                TailnetPeer(
                    node_id,
                    dns_name,
                    addresses,
                    _boolean(peer.get("InNetworkMap")),
                    _boolean(peer.get("Expired", False)),
                    key_expiry,
                )
            )
        return TailnetStatus(backend_state == "Running", kernel, tuple(peers))
    except (ValueError, TypeError, RecursionError):
        raise TailnetValidationError("invalid Tailscale status") from None


class TailscaleDestinationValidator:
    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._active = False

    async def _cleanup(self, process: asyncio.subprocess.Process) -> None:
        # Closing the read pipe is necessary if a descendant retained stdout.
        if process.stdout is not None:
            transport = getattr(process.stdout, "_transport", None)
            if transport is not None:
                transport.close()
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), TAILSCALE_TERMINATE_TIMEOUT_SECONDS)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), TAILSCALE_KILL_TIMEOUT_SECONDS)
        if process.returncode is not None:
            self._process = None

    async def validate(self, host: str, binary: Path) -> ValidatedTailnetDestination:
        if self._active or (self._process is not None and self._process.returncode is None):
            raise TailnetValidationError("previous status child has not exited")
        self._process = None
        self._active = True
        try:
            async with asyncio.timeout(TAILSCALE_STATUS_TIMEOUT_SECONDS):
                self._process = await asyncio.create_subprocess_exec(
                    str(binary),
                    "status",
                    "--json",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                process = self._process
                assert process.stdout is not None
                output = bytearray()
                while chunk := await process.stdout.read(
                    min(64 * 1024, TAILSCALE_STATUS_MAX_BYTES + 1 - len(output))
                ):
                    output.extend(chunk)
                    if len(output) > TAILSCALE_STATUS_MAX_BYTES:
                        raise TailnetValidationError("status output limit exceeded")
                if await process.wait() != 0:
                    raise TailnetValidationError("status child failed")
                status = parse_tailnet_status(bytes(output))
                return select_tailnet_destination(host, status, datetime.now(UTC))
        except TailnetValidationError:
            raise
        except (OSError, TimeoutError):
            raise TailnetValidationError("Tailscale status unavailable") from None
        finally:
            if self._process is not None:
                cleanup = asyncio.create_task(self._cleanup(self._process))
                # Even repeated cancellation cannot abandon child cleanup.
                cancelled = False
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        cancelled = True
                cleanup.result()
                self._active = False
                if cancelled:
                    raise asyncio.CancelledError
            else:
                self._active = False


_validator = TailscaleDestinationValidator()


async def validate_tailnet_destination(host: str, binary: Path) -> ValidatedTailnetDestination:
    return await _validator.validate(host, binary)
