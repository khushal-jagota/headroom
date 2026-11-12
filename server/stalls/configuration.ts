import type { TrackedWaitKind } from './contracts.ts';
export const STALL_CHECK_INTERVAL_SECONDS = 15;
export const MAC_ITEM_NO_PROGRESS_LIMIT_SECONDS = 180;
export const COMMAND_UNFETCHED_LIMIT_SECONDS = 120;
export const VPS_SAVE_RETRY_LIMIT_SECONDS = 120;
export const MAC_STOP_PROOF_LIMIT_SECONDS = 180;
export const MAC_ITEM_SETTLING_LIMIT_SECONDS = 120;
export const MAC_REPLACEMENT_PENDING_LIMIT_SECONDS = 180;
export const MAC_TURN_SILENT_LIMIT_SECONDS = 180;
export const MAC_LEASE_FAILING_LIMIT_SECONDS = 180;
export const MAC_LEASE_FAILING_RECENT_CONTACT_SECONDS = 60;
export const MAC_LEASE_FAILING_UNANSWERED_CONTACT_SECONDS = 20;
export const MAC_CANCEL_RETIREMENT_LIMIT_SECONDS = 180;
export const VPS_REPLACEMENT_RETIRING_LIMIT_SECONDS = 180;
export const MAC_PRESENT_GAP_SECONDS = 60;
export const MAC_PRESENT_STRETCH_MIN_SECONDS = 60;
export const WATCHDOG_INTERVAL_SECONDS = 30;
export const CHECKER_HEARTBEAT_STALE_SECONDS = 90;
export function stall_limit_seconds(kind: TrackedWaitKind): number {
  return {
    mac_items_not_moving: MAC_ITEM_NO_PROGRESS_LIMIT_SECONDS,
    mac_command_unfetched: COMMAND_UNFETCHED_LIMIT_SECONDS,
    mac_stop_unproven: MAC_STOP_PROOF_LIMIT_SECONDS,
    mac_replacement_pending: MAC_REPLACEMENT_PENDING_LIMIT_SECONDS,
    mac_turn_silent: MAC_TURN_SILENT_LIMIT_SECONDS,
    mac_lease_failing: MAC_LEASE_FAILING_LIMIT_SECONDS,
    mac_item_still_saving: MAC_ITEM_SETTLING_LIMIT_SECONDS,
    vps_save_retrying: VPS_SAVE_RETRY_LIMIT_SECONDS,
    mac_cancel_retirement: MAC_CANCEL_RETIREMENT_LIMIT_SECONDS,
    vps_replacement_retiring: VPS_REPLACEMENT_RETIRING_LIMIT_SECONDS,
  }[kind];
}
