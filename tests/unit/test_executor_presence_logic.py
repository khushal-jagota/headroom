"""The Mac's presence class, its continuous presence, and when a lease announces.

Risk: a wrong class blocks Mac Tickets while the Mac leases ("Waiting for the Mac"),
admits work for a Mac that has gone, or makes every 10 s lease refetch every screen.
No test covers these rules; they are new. Layer: pure functions over PresenceRecord, VPS clock only.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from planner.execution_configuration import PROTOCOL_VERSION
from planner.execution_identity import MAC_NEEDS_UPDATE, ExecutorIdentity
from planner.executor.configuration import LEASE_CONTINUITY_SECONDS, MAC_AWAY_AFTER_SECONDS
from planner.executor.contracts import PresenceRecord
from planner.executor.logic.presence import (
    continuous_since_after_lease,
    judge_presence,
    lease_changes_presence_signal,
    next_presence_change_ms,
)
from planner.executor.protocol import ExecutorRefusal

AWAY_MS = int(MAC_AWAY_AFTER_SECONDS * 1000)
CONTINUITY_MS = int(LEASE_CONTINUITY_SECONDS * 1000)
IDENTITY = ExecutorIdentity(PROTOCOL_VERSION, "revision", 5, str(uuid4()))
LEASED = PresenceRecord(
    executor_incarnation=IDENTITY.executor_incarnation,
    identity=IDENTITY,
    last_contact_at_ms=100_000,
    last_lease_at_ms=100_000,
    continuous_since_ms=40_000,
    going_to_sleep_at_ms=None,
    lease=None,
    refusal=None,
)
REFUSAL = ExecutorRefusal(MAC_NEEDS_UPDATE, None, IDENTITY, 90_000)


@pytest.mark.parametrize(
    "record,now_ms,presence,since_ms",
    [
        (None, 0, "never", None),
        (replace(LEASED, last_lease_at_ms=None, continuous_since_ms=None), 1, "never", None),
        (LEASED, 100_000 + AWAY_MS - 1, "present", 40_000),
        (LEASED, 100_000 + AWAY_MS, "not_heard_from", 100_000),
        (replace(LEASED, going_to_sleep_at_ms=95_000), 100_001, "asleep", 95_000),
        (replace(LEASED, going_to_sleep_at_ms=95_000), 100_000 + AWAY_MS, "asleep", 95_000),
        (replace(LEASED, refusal=REFUSAL), 100_001, "refused", 90_000),
    ],
)
def test_presence_class_and_since(
    record: PresenceRecord | None, now_ms: int, presence: str, since_ms: int | None
) -> None:
    judgement = judge_presence(record, now_ms)
    assert (judgement.presence, judgement.since_ms) == (presence, since_ms)


def test_only_a_present_mac_changes_class_by_time_alone() -> None:
    assert next_presence_change_ms(LEASED) == 100_000 + AWAY_MS
    assert judge_presence(LEASED, 100_000 + AWAY_MS).presence == "not_heard_from"
    for record in (
        None,
        replace(LEASED, last_lease_at_ms=None),
        replace(LEASED, going_to_sleep_at_ms=95_000),
        replace(LEASED, refusal=REFUSAL),
    ):
        assert next_presence_change_ms(record) is None


def test_continuous_presence_survives_ordinary_lease_gaps_and_restarts_after_a_long_one() -> None:
    now = 100_000 + CONTINUITY_MS
    assert continuous_since_after_lease(LEASED, new_incarnation=False, now_ms=now) == 40_000
    assert continuous_since_after_lease(LEASED, new_incarnation=False, now_ms=now + 1) == now + 1
    assert continuous_since_after_lease(LEASED, new_incarnation=True, now_ms=now) == now
    assert continuous_since_after_lease(None, new_incarnation=True, now_ms=now) == now


def test_only_what_a_screen_shows_announces_a_lease() -> None:
    later = replace(LEASED, last_lease_at_ms=110_000)

    def announces(previous: PresenceRecord | None, current: PresenceRecord, *, new: bool) -> bool:
        return lease_changes_presence_signal(previous, current, new_incarnation=new, now_ms=110_000)

    assert not announces(LEASED, later, new=False)
    assert announces(LEASED, later, new=True)
    assert announces(None, later, new=True)
    assert announces(replace(LEASED, refusal=REFUSAL), later, new=False)
    assert announces(LEASED, replace(later, going_to_sleep_at_ms=110_000), new=False)
    assert announces(replace(LEASED, going_to_sleep_at_ms=105_000), later, new=False)
    returned = replace(LEASED, last_lease_at_ms=10_000)
    assert announces(returned, later, new=False)
