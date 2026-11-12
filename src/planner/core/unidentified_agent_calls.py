"""Refuse missing agent identities using the loopback client's kernel evidence."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio.to_thread

from planner.core.authctx import AUTHENTICATED_OWNER_SCOPE_KEY

_LOG = logging.getLogger(__name__)

_REFUSAL = "Unidentified caller refused. Restore PLAN_ACTOR and your Panels identity."


def _address(encoded: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    raw = bytes.fromhex(encoded)
    # /proc prints each native-endian 32-bit word of the network address.
    if sys.byteorder == "little":
        raw = b"".join(raw[offset : offset + 4][::-1] for offset in range(0, len(raw), 4))
    address = ipaddress.ip_address(raw)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _agent_cgroup(text: str) -> bool:
    lines = text.splitlines()
    if not lines:
        raise ValueError("missing cgroup evidence")
    for line in lines:
        _hierarchy, _controllers, path = line.split(":", 2)
        if not path.startswith("/"):
            raise ValueError("invalid cgroup evidence")
        parts = Path(path).parts
        if "panels-live.service" in parts or "panels-workers.slice" in parts:
            return True
    return False


@dataclass(frozen=True)
class PeerLookupResult:
    is_owner: bool
    skipped_unreadable_processes: int = 0


def resolve_loopback_peer(
    client: tuple[str, int] | None,
    server: tuple[str, int] | None,
    proc: Path = Path("/proc"),
) -> PeerLookupResult:
    """Require a same-user socket with holders outside both Panels agent cgroups.

    Disappeared unrelated processes or descriptors are normal during a scan. Once
    a descriptor identifies the peer, every piece of its evidence must remain valid.
    Unreadable descriptors outside the agent cgroups are skipped and counted.
    """
    skipped = 0
    try:
        if client is None or server is None:
            return PeerLookupResult(False, skipped)
        host, port = client
        if host not in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}:
            return PeerLookupResult(False, skipped)
        peer = ipaddress.ip_address(host)
        if isinstance(peer, ipaddress.IPv6Address) and peer.ipv4_mapped is not None:
            peer = peer.ipv4_mapped
        sockets: set[tuple[int, int]] = set()
        for name in ("tcp", "tcp6"):
            for line in (proc / "net" / name).read_text().splitlines()[1:]:
                fields = line.split()
                local_host, local_port = fields[1].split(":")
                _remote_host, remote_port = fields[2].split(":")
                if (
                    fields[3] == "01"
                    and int(local_port, 16) == port
                    and int(remote_port, 16) == server[1]
                    and _address(local_host) == peer
                ):
                    sockets.add((int(fields[9]), int(fields[7])))
        if len(sockets) != 1:
            return PeerLookupResult(False, skipped)
        inode, uid = sockets.pop()
        if uid != os.getuid() or inode <= 0:
            return PeerLookupResult(False, skipped)
        target = f"socket:[{inode}]"
        holders = 0
        for process in proc.iterdir():
            if not process.name.isdecimal():
                continue
            try:
                if process.stat().st_uid != uid:
                    continue
            except FileNotFoundError:
                continue
            try:
                descriptors = list((process / "fd").iterdir())
            except FileNotFoundError:
                continue
            except OSError:
                if _agent_cgroup((process / "cgroup").read_text()):
                    return PeerLookupResult(False, skipped)
                skipped += 1
                continue
            process_holders = 0
            for descriptor in descriptors:
                try:
                    link = os.readlink(descriptor)
                except FileNotFoundError:
                    continue
                except OSError:
                    if _agent_cgroup((process / "cgroup").read_text()):
                        return PeerLookupResult(False, skipped)
                    skipped += 1
                    break
                if link != target:
                    continue
                cgroup = (process / "cgroup").read_text()
                if os.readlink(descriptor) != target or _agent_cgroup(cgroup):
                    return PeerLookupResult(False, skipped)
                process_holders += 1
            else:
                holders += process_holders
        return PeerLookupResult(holders > 0, skipped)
    except (OSError, ValueError, IndexError):
        return PeerLookupResult(False, skipped)


class UnidentifiedAgentCallsMiddleware:
    def __init__(self, app: Any, enabled: bool) -> None:
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if (
            not self.enabled
            or scope["type"] not in {"http", "websocket"}
            or scope.get(AUTHENTICATED_OWNER_SCOPE_KEY) is True
            or next(
                (
                    value.decode("latin1").strip()
                    for name, value in scope.get("headers", ())
                    if name.lower() == b"x-plan-actor"
                ),
                "",
            )
        ):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        if client is not None:
            try:
                address = ipaddress.ip_address(client[0])
            except ValueError:
                pass  # Unknown peers still need evidence, as do missing peers.
            else:
                if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
                    address = address.ipv4_mapped
                if not address.is_loopback:
                    await self.app(scope, receive, send)
                    return
        result = await anyio.to_thread.run_sync(
            resolve_loopback_peer, scope.get("client"), scope.get("server")
        )
        _LOG.debug(
            "Unidentified loopback caller: is_owner=%s skipped_unreadable_processes=%s",
            result.is_owner,
            result.skipped_unreadable_processes,
            extra={"skipped_unreadable_processes": result.skipped_unreadable_processes},
        )
        if result.is_owner:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": _REFUSAL})
            return
        body = json.dumps(
            {
                "error": {
                    "code": "agent_forbidden",
                    "message": _REFUSAL,
                    "detail": {"skipped_unreadable_processes": result.skipped_unreadable_processes},
                }
            },
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
