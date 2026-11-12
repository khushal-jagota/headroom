import { MAC_AWAY_AFTER_SECONDS } from '../configuration.ts';
import type { PresenceJudgement, PresenceRecord } from '../contracts.ts';
export function judge_presence(record: PresenceRecord | null, now_ms: number): PresenceJudgement {
    if (record === null)
        return { presence: 'never', since_ms: null };
    if (record.refusal !== null)
        return { presence: 'refused', since_ms: record.refusal.refused_at_ms };
    if (record.last_lease_at_ms === null)
        return { presence: 'never', since_ms: null };
    if (record.going_to_sleep_at_ms !== null)
        return { presence: 'asleep', since_ms: record.going_to_sleep_at_ms };
    if (now_ms - record.last_lease_at_ms < MAC_AWAY_AFTER_SECONDS * 1000) {
        return { presence: 'present', since_ms: record.continuous_since_ms ?? record.last_lease_at_ms };
    }
    return { presence: 'not_heard_from', since_ms: record.last_lease_at_ms };
}
