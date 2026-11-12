import type { ReadSql } from '../core/database/contracts.ts';

export const TRACKED_WAIT_KINDS = [
  'mac_items_not_moving',
  'mac_command_unfetched',
  'mac_stop_unproven',
  'mac_replacement_pending',
  'mac_turn_silent',
  'mac_lease_failing',
  'mac_item_still_saving',
  'vps_save_retrying',
  'mac_cancel_retirement',
  'vps_replacement_retiring',
] as const;
export type TrackedWaitKind = (typeof TRACKED_WAIT_KINDS)[number];
export const PAUSED_WHILE_MAC_AWAY: ReadonlySet<TrackedWaitKind> = new Set([
  'mac_items_not_moving',
  'mac_command_unfetched',
  'mac_stop_unproven',
  'mac_replacement_pending',
  'mac_turn_silent',
  'mac_cancel_retirement',
]);
export interface TrackedWait {
  readonly kind: TrackedWaitKind;
  readonly wait_key: string;
  readonly conversation_id: string | null;
  readonly detail: string;
  readonly started_at_ms: number;
}
export function finding_key(wait: TrackedWait): string {
  return `${wait.kind}:${wait.wait_key}`;
}
export interface StallFinding {
  finding_key: string;
  kind: TrackedWaitKind;
  conversation_id: string | null;
  ticket_id: string | null;
  ticket_title: string | null;
  detail: string;
  stalled_since_ms: number;
  last_seen_at_ms: number;
  cleared_at_ms: number | null;
}
export interface StallSource {
  tracked_waits(sql: ReadSql, now_ms: number): readonly TrackedWait[];
}
export interface TrackedWaitHandle {
  set_detail(detail: string): void;
  close(): void;
}
export interface WaitRegistry {
  track(
    kind: TrackedWaitKind,
    wait_key: string,
    options: { detail: string; conversation_id?: string | null },
  ): TrackedWaitHandle;
  snapshot(): readonly TrackedWait[];
}
