/** Presence read subset. Lease writers and transport remain in Unit 22. */
import type { ReadSql } from '../core/database/contracts.ts';
import type { ExecutorIdentity } from '../execution_identity.ts';
import type { ExecutorRefusal, PresenceRecord } from './contracts.ts';
export function read_presence(sql: ReadSql): PresenceRecord | null {
  const row = sql.get<{
    executor_incarnation: string | null;
    identity_json: string | null;
    last_contact_at_ms: number | null;
    last_lease_at_ms: number | null;
    continuous_since_ms: number | null;
    going_to_sleep_at_ms: number | null;
    refusal_json: string | null;
  }>(
    'SELECT executor_incarnation,identity_json,last_contact_at_ms,last_lease_at_ms,' +
      'continuous_since_ms,going_to_sleep_at_ms,refusal_json FROM mac_executor_presence WHERE singleton=1',
  );
  if (!row) return null;
  return {
    executor_incarnation: row.executor_incarnation,
    identity:
      row.identity_json === null ? null : (JSON.parse(row.identity_json) as ExecutorIdentity),
    last_contact_at_ms: row.last_contact_at_ms,
    last_lease_at_ms: row.last_lease_at_ms,
    continuous_since_ms: row.continuous_since_ms,
    going_to_sleep_at_ms: row.going_to_sleep_at_ms,
    refusal: row.refusal_json === null ? null : (JSON.parse(row.refusal_json) as ExecutorRefusal),
  };
}
