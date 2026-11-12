/** Presence read subset. Lease writers and transport remain in Unit 22. */
import type { ReadSql } from '../core/database/contracts.ts';
import type { ExecutorIdentity } from '../execution_identity.ts';
import type {
  ExecutorRefusal,
  PresenceRecord,
  StallLease,
  StallPresenceRecord,
} from './contracts.ts';
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

/** Extend the existing presence reader only with the lease projection this area needs. */
export function read_stall_presence(sql: ReadSql): StallPresenceRecord | null {
  const presence = read_presence(sql);
  if (!presence) return null;
  const row = sql.get<{ lease_json: string | null }>(`
    SELECT lease_json FROM mac_executor_presence WHERE singleton=1
  `);
  return {
    ...presence,
    lease: lease_projection(row?.lease_json ?? null),
  };
}
/** Invalid used-field shapes read as no lease. This temporary projection does not
 * validate the complete LeaseRequest. Unit 22 supplies its full wire-schema decoder. */
function lease_projection(text: string | null): StallLease | null {
  if (text === null) return null;
  try {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== 'object') return null;
    const record = value as Record<string, unknown>;
    if (!Array.isArray(record.waiting_conversations) || !Array.isArray(record.working_turns))
      return null;
    for (const item of record.waiting_conversations) {
      if (
        !item ||
        typeof item !== 'object' ||
        typeof item.conversation_id !== 'string' ||
        !Number.isInteger(item.oldest_sequence)
      )
        return null;
    }
    for (const item of record.working_turns) {
      if (
        !item ||
        typeof item !== 'object' ||
        typeof item.tool_running !== 'boolean' ||
        !item.child ||
        typeof item.child !== 'object' ||
        typeof item.child.conversation_id !== 'string'
      )
        return null;
    }
    return record as unknown as StallLease;
  } catch {
    return null;
  }
}
