import { LEASE_CONTINUITY_SECONDS, MAC_AWAY_AFTER_SECONDS } from '../configuration.ts';
import type { PresenceJudgement, PresenceRecord } from '../contracts.ts';
export function judge_presence(record: PresenceRecord | null, now_ms: number): PresenceJudgement {
  if (record === null) return { presence: 'never', since_ms: null };
  if (record.refusal !== null)
    return { presence: 'refused', since_ms: record.refusal.refused_at_ms };
  if (record.last_lease_at_ms === null) return { presence: 'never', since_ms: null };
  if (record.going_to_sleep_at_ms !== null)
    return { presence: 'asleep', since_ms: record.going_to_sleep_at_ms };
  if (now_ms - record.last_lease_at_ms < MAC_AWAY_AFTER_SECONDS * 1000) {
    return { presence: 'present', since_ms: record.continuous_since_ms ?? record.last_lease_at_ms };
  }
  return { presence: 'not_heard_from', since_ms: record.last_lease_at_ms };
}

export function next_presence_change_ms(record: PresenceRecord | null): number | null {
  if (
    record === null ||
    record.refusal !== null ||
    record.last_lease_at_ms === null ||
    record.going_to_sleep_at_ms !== null
  )
    return null;
  return record.last_lease_at_ms + MAC_AWAY_AFTER_SECONDS * 1000;
}

export function continuous_since_after_lease(
  previous: PresenceRecord | null,
  { new_incarnation, now_ms }: { new_incarnation: boolean; now_ms: number },
): number {
  if (
    new_incarnation ||
    previous === null ||
    previous.last_lease_at_ms === null ||
    previous.continuous_since_ms === null ||
    now_ms - previous.last_lease_at_ms > LEASE_CONTINUITY_SECONDS * 1000
  )
    return now_ms;
  return previous.continuous_since_ms;
}

export function lease_changes_presence_signal(
  previous: PresenceRecord | null,
  current: PresenceRecord,
  { new_incarnation, now_ms }: { new_incarnation: boolean; now_ms: number },
): boolean {
  return (
    new_incarnation ||
    (previous !== null && previous.refusal !== null) ||
    judge_presence(previous, now_ms).presence !== judge_presence(current, now_ms).presence ||
    (previous === null || previous.going_to_sleep_at_ms === null) !==
      (current.going_to_sleep_at_ms === null)
  );
}
