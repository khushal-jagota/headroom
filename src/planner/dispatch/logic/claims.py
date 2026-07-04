"""Claim/TTL lease arithmetic — the single definition of lease time (§7.3). Pure
stdlib, no imports. Every dispatch path (candidate assembly, heartbeat guard, close
guard, reclaim sweep) computes expiry and 'is this lease still alive?' through these
three functions, so the boundary rule lives in exactly one place."""

from __future__ import annotations


def expiry_at(now: int, ttl_seconds: int) -> int:
    """Expiry for a claim taken or heartbeaten at `now`: exactly now + one TTL."""
    return now + ttl_seconds


def is_expired(claim_expires: int | None, now: int) -> bool:
    """Expired iff claim_expires <= now — the lease is the half-open interval
    [start, start + ttl); a NULL expiry counts as expired. Half-open means a
    heartbeat/close at exactly claim_expires fails while a reclaim at exactly
    claim_expires proceeds, so no instant lets a writer and the reclaimer both
    think the lease is live."""
    return claim_expires is None or claim_expires <= now


def has_active_claim(claim_lock: str | None, claim_expires: int | None, now: int) -> bool:
    """§7.2 'no active claim' / contract 'claim_lock set and unexpired'."""
    return claim_lock is not None and not is_expired(claim_expires, now)
