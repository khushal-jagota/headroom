/** Caller-owned presence SQL. Lease admission and postcommit publication belong to the owner. */
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { ORIGINAL_MAC_MACHINE_ID, type ExecutorIdentity } from '../execution_identity.ts';
import type { AcceptedLease, ExecutorRefusal, PresenceRecord, WireValue } from './contracts.ts';
import { as_object, decode_json, decode_value, ExecutorValidationError } from './protocol.ts';
import { continuous_since_after_lease } from './logic/presence.ts';

/** Stored identities need shape decoding, without ingress UUID or version admission. */
function identity(value: WireValue<'ExecutorIdentity'>): ExecutorIdentity {
  return {
    ...value,
    connection_generation:
      value.connection_generation === undefined ? 0 : value.connection_generation,
  };
}
function refusal(value: WireValue<'ExecutorRefusal'>): ExecutorRefusal {
  return {
    ...value,
    server_identity: identity(value.server_identity),
    peer_identity: value.peer_identity === null ? null : identity(value.peer_identity),
  };
}
function parse_lease(text: string | null): WireValue<'LeaseRequest'> | null {
  if (text === null) return null;
  try {
    return decode_json(text, 'LeaseRequest');
  } catch (error) {
    // A lease from an older shape is diagnostic only. Other stored records remain authoritative.
    if (error instanceof ExecutorValidationError) return null;
    throw error;
  }
}
export function read_presence(
  sql: ReadSql,
  machine_id: string = ORIGINAL_MAC_MACHINE_ID,
): PresenceRecord | null {
  const row = sql.get<{
    executor_incarnation: string | null;
    identity_json: string | null;
    last_contact_at_ms: number | null;
    last_lease_at_ms: number | null;
    continuous_since_ms: number | null;
    going_to_sleep_at_ms: number | null;
    lease_json: string | null;
    refusal_json: string | null;
  }>(
    'SELECT executor_incarnation,identity_json,last_contact_at_ms,last_lease_at_ms,' +
      'continuous_since_ms,going_to_sleep_at_ms,lease_json,refusal_json FROM mac_executor_presence WHERE machine_id=?',
    [machine_id],
  );
  if (!row) return null;
  return {
    executor_incarnation: row.executor_incarnation,
    identity:
      row.identity_json === null
        ? null
        : identity(decode_json(row.identity_json, 'ExecutorIdentity')),
    last_contact_at_ms: row.last_contact_at_ms,
    last_lease_at_ms: row.last_lease_at_ms,
    continuous_since_ms: row.continuous_since_ms,
    going_to_sleep_at_ms: row.going_to_sleep_at_ms,
    lease: parse_lease(row.lease_json),
    refusal:
      row.refusal_json === null ? null : refusal(decode_json(row.refusal_json, 'ExecutorRefusal')),
  };
}
export const read_stall_presence = read_presence;

export function not_current(executor_incarnation: string): PlannerError {
  return new PlannerError(
    'executor_incarnation_not_current',
    'This Mac executor process is not the current one.',
    { executor_incarnation },
  );
}
export function current_incarnation(
  sql: ReadSql,
  machine_id: string = ORIGINAL_MAC_MACHINE_ID,
): string | null {
  return (
    sql.get<{ executor_incarnation: string | null }>(
      'SELECT executor_incarnation FROM mac_executor_presence WHERE machine_id=?',
      [machine_id],
    )?.executor_incarnation ?? null
  );
}
/** Binding fence: call inside the transaction that writes, after transport admission. */
export function require_current_incarnation_in_transaction(
  sql: ReadSql,
  executor_incarnation: string,
  machine_id: string = ORIGINAL_MAC_MACHINE_ID,
): void {
  if (current_incarnation(sql, machine_id) !== executor_incarnation)
    throw not_current(executor_incarnation);
}

/** Caller admits versions and samples now_ms inside its writer. Settle replaced commands in this same transaction. */
export function accept_lease_in_transaction(
  tx: TransactionSql,
  request: WireValue<'LeaseRequest'>,
  now_ms: number,
  machine_id: string = ORIGINAL_MAC_MACHINE_ID,
): AcceptedLease {
  request = decode_value(request, 'LeaseRequest');
  const incarnation = request.identity.executor_incarnation;
  const seen = tx.get<{ replaced_at_ms: number | null; machine_id: string }>(
    'SELECT replaced_at_ms,machine_id FROM executor_incarnations WHERE executor_incarnation=?',
    [incarnation],
  );
  if (seen !== undefined && (seen.replaced_at_ms !== null || seen.machine_id !== machine_id))
    throw not_current(incarnation);
  const previous = read_presence(tx, machine_id);
  const replaced = previous?.executor_incarnation ?? null;
  const new_incarnation = replaced !== incarnation;
  if (new_incarnation) {
    tx.run(
      'UPDATE executor_incarnations SET replaced_at_ms=? WHERE machine_id=? AND replaced_at_ms IS NULL AND executor_incarnation<>?',
      [now_ms, machine_id, incarnation],
    );
    tx.run(
      'INSERT INTO executor_incarnations(executor_incarnation,first_leased_at_ms,replaced_at_ms,machine_id) VALUES (?,?,NULL,?) ON CONFLICT(executor_incarnation) DO NOTHING',
      [incarnation, now_ms, machine_id],
    );
  }
  const current: PresenceRecord = {
    executor_incarnation: incarnation,
    identity: identity(request.identity),
    last_contact_at_ms:
      previous === null || previous.last_contact_at_ms === null
        ? now_ms
        : Math.max(now_ms, previous.last_contact_at_ms),
    last_lease_at_ms: now_ms,
    continuous_since_ms: continuous_since_after_lease(previous, { new_incarnation, now_ms }),
    going_to_sleep_at_ms: request.going_to_sleep
      ? ((new_incarnation ? null : previous?.going_to_sleep_at_ms) ?? now_ms)
      : null,
    lease: request,
    refusal: null,
  };
  tx.run(
    'INSERT INTO mac_executor_presence(machine_id,executor_incarnation,identity_json,last_contact_at_ms,last_lease_at_ms,continuous_since_ms,going_to_sleep_at_ms,lease_json,refusal_json) VALUES (?,?,?,?,?,?,?,?,?) ' +
      'ON CONFLICT(machine_id) DO UPDATE SET executor_incarnation=excluded.executor_incarnation,identity_json=excluded.identity_json,last_contact_at_ms=excluded.last_contact_at_ms,last_lease_at_ms=excluded.last_lease_at_ms,continuous_since_ms=excluded.continuous_since_ms,going_to_sleep_at_ms=excluded.going_to_sleep_at_ms,lease_json=excluded.lease_json,refusal_json=excluded.refusal_json',
    [
      machine_id,
      incarnation,
      JSON.stringify(as_object(current.identity!, 'ExecutorIdentity')),
      current.last_contact_at_ms,
      now_ms,
      current.continuous_since_ms,
      current.going_to_sleep_at_ms,
      JSON.stringify(as_object(request, 'LeaseRequest')),
      null,
    ],
  );
  return {
    previous,
    current,
    new_incarnation,
    replaced_incarnation: new_incarnation ? replaced : null,
  };
}
export function same_identity(
  left: ExecutorIdentity | null,
  right: ExecutorIdentity | null,
): boolean {
  if (left === null || right === null) return left === right;
  return (
    left.application_revision === right.application_revision &&
    left.build_id === right.build_id &&
    left.executor_incarnation === right.executor_incarnation &&
    left.protocol_version === right.protocol_version &&
    left.spool_format_version === right.spool_format_version &&
    left.connection_generation === right.connection_generation
  );
}
/** Version refusal precedes the current-incarnation fence. True means this commit announces a change. */
export function record_refusal(
  tx: TransactionSql,
  value: ExecutorRefusal,
  machine_id: string = ORIGINAL_MAC_MACHINE_ID,
): boolean {
  value = refusal(decode_value(value, 'ExecutorRefusal'));
  const stored = read_presence(tx, machine_id)?.refusal ?? null;
  if (
    stored !== null &&
    stored.reason === value.reason &&
    same_identity(stored.peer_identity, value.peer_identity)
  )
    return false;
  tx.run(
    'INSERT INTO mac_executor_presence(machine_id,last_contact_at_ms,refusal_json) VALUES (?,?,?) ON CONFLICT(machine_id) DO UPDATE SET last_contact_at_ms=MAX(COALESCE(last_contact_at_ms,0),excluded.last_contact_at_ms),refusal_json=excluded.refusal_json',
    [machine_id, value.refused_at_ms, JSON.stringify(as_object(value, 'ExecutorRefusal'))],
  );
  return true;
}
/** Contact-only commits remain silent. No lease or incarnation authority is required. */
export function record_contact(
  tx: TransactionSql,
  at_ms: number,
  machine_id: string = ORIGINAL_MAC_MACHINE_ID,
): void {
  tx.run(
    'INSERT INTO mac_executor_presence(machine_id,last_contact_at_ms) VALUES (?,?) ON CONFLICT(machine_id) DO UPDATE SET last_contact_at_ms=MAX(COALESCE(last_contact_at_ms,0),excluded.last_contact_at_ms)',
    [machine_id, at_ms],
  );
}

/** An old incarnation can replay only within its original machine. */
export function require_incarnation_owner(
  sql: ReadSql,
  incarnation: string,
  machine_id: string = ORIGINAL_MAC_MACHINE_ID,
): void {
  const row = sql.get<{ machine_id: string }>(
    'SELECT machine_id FROM executor_incarnations WHERE executor_incarnation=?',
    [incarnation],
  );
  if (row === undefined || row.machine_id !== machine_id)
    throw new PlannerError('agent_forbidden', 'executor reference belongs to another machine');
}
