"""Pure association rules; the local peer map does not authenticate SSH."""

from datetime import datetime
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

from planner.executor.contracts import (
    TailnetStatus,
    TailnetValidationError,
    ValidatedTailnetDestination,
)

_TAILNET_V4 = ip_network("100.64.0.0/10")
_TAILNET_V6 = ip_network("fd7a:115c:a1e0::/48")


def _address(value: str) -> IPv4Address | IPv6Address:
    try:
        # Scoped IPv6 addresses are not peer-map identities.
        if "%" in value:
            raise ValueError
        return ip_address(value)
    except ValueError:
        raise TailnetValidationError("invalid peer address") from None


def select_tailnet_destination(
    host: str, status: TailnetStatus, now: datetime
) -> ValidatedTailnetDestination:
    if not status.running or not status.kernel_networking:
        raise TailnetValidationError("kernel Tailscale networking required")
    try:
        literal = ip_address(host)
    except ValueError:
        literal = None
    name = host.removesuffix(".").lower()
    if literal is None and (not name or "." not in name or "%" in name):
        raise TailnetValidationError("full peer DNS name required")
    matches = []
    for peer in status.peers:
        addresses = tuple(_address(value) for value in peer.addresses)
        matched = (
            literal in addresses
            if literal is not None
            else name == peer.dns_name.removesuffix(".").lower()
        )
        if matched:
            matches.append((peer, addresses))
    if len(matches) != 1:
        raise TailnetValidationError("destination must identify one peer")
    peer, addresses = matches[0]
    if (
        not peer.node_id
        or not peer.in_network_map
        or peer.expired
        or (peer.key_expiry is not None and peer.key_expiry <= now)
        or not addresses
    ):
        raise TailnetValidationError("current peer evidence required")
    selected = literal if literal is not None else min(addresses, key=lambda a: (a.version, int(a)))
    if selected not in (_TAILNET_V4 if selected.version == 4 else _TAILNET_V6):
        raise TailnetValidationError("destination is outside Tailscale ranges")
    return ValidatedTailnetDestination(peer.node_id, str(selected))
