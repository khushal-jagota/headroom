"""Stall limits and timings. Production limits stay at or under 300 s with the checker
interval and the watchdog timer added: 180 + 15 + 30 = 225 s."""

from __future__ import annotations

from typing import Final

from planner.stalls.contracts import TrackedWaitKind

STALL_CHECK_INTERVAL_SECONDS: Final = 15
MAC_ITEM_NO_PROGRESS_LIMIT_SECONDS: Final = 180
COMMAND_UNFETCHED_LIMIT_SECONDS: Final = 120
VPS_SAVE_RETRY_LIMIT_SECONDS: Final = 120
MAC_STOP_PROOF_LIMIT_SECONDS: Final = 180
MAC_ITEM_SETTLING_LIMIT_SECONDS: Final = 120
MAC_REPLACEMENT_PENDING_LIMIT_SECONDS: Final = 180
MAC_TURN_SILENT_LIMIT_SECONDS: Final = 180
MAC_LEASE_FAILING_LIMIT_SECONDS: Final = 180
# mac_lease_failing applies only while the Mac made contact this recently.
MAC_LEASE_FAILING_RECENT_CONTACT_SECONDS: Final = 30
MAC_CANCEL_RETIREMENT_LIMIT_SECONDS: Final = 180
WATCHDOG_INTERVAL_SECONDS: Final = 30
CHECKER_HEARTBEAT_STALE_SECONDS: Final = 90


def stall_limit_seconds(kind: TrackedWaitKind) -> int:
    """Read at call time, so a test that scales a limit down patches one constant."""
    return {
        TrackedWaitKind.mac_items_not_moving: MAC_ITEM_NO_PROGRESS_LIMIT_SECONDS,
        TrackedWaitKind.mac_command_unfetched: COMMAND_UNFETCHED_LIMIT_SECONDS,
        TrackedWaitKind.mac_stop_unproven: MAC_STOP_PROOF_LIMIT_SECONDS,
        TrackedWaitKind.mac_replacement_pending: MAC_REPLACEMENT_PENDING_LIMIT_SECONDS,
        TrackedWaitKind.mac_turn_silent: MAC_TURN_SILENT_LIMIT_SECONDS,
        TrackedWaitKind.mac_lease_failing: MAC_LEASE_FAILING_LIMIT_SECONDS,
        TrackedWaitKind.mac_item_still_saving: MAC_ITEM_SETTLING_LIMIT_SECONDS,
        TrackedWaitKind.vps_save_retrying: VPS_SAVE_RETRY_LIMIT_SECONDS,
        TrackedWaitKind.mac_cancel_retirement: MAC_CANCEL_RETIREMENT_LIMIT_SECONDS,
    }[kind]
