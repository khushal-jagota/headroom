import type { ReadSql } from '../core/database/contracts.ts';
import { ORIGINAL_MAC_MACHINE_ID, supports_executor_versions } from '../execution_identity.ts';
import { read_machine } from '../machines/data.ts';
import { judge_presence } from '../executor/logic/presence.ts';
import { read_presence } from '../executor/vps_presence_store.ts';
import type { ExecutorRefusal } from '../executor/contracts.ts';
import type { TicketLocale } from './contracts.ts';
export const CURRENT_EXECUTION_LOCALE: TicketLocale = 'vps';
export const WAITING_FOR_THE_MAC = 'Waiting for the Mac';
export function can_execute_locale(sql: ReadSql, locale: TicketLocale): boolean {
  if (locale === CURRENT_EXECUTION_LOCALE) return true;
  if (locale !== 'macos') return false;
  const record = read_presence(sql);
  return (
    record !== null &&
    record.executor_incarnation !== null &&
    record.identity !== null &&
    record.refusal === null &&
    supports_executor_versions(
      record.identity.protocol_version,
      record.identity.spool_format_version,
    )
  );
}
export function mac_is_present(
  sql: ReadSql,
  now_ms: number,
  machine_id = ORIGINAL_MAC_MACHINE_ID,
): boolean {
  return judge_presence(read_presence(sql, machine_id), now_ms).presence === 'present';
}
function _render_refusal(refusal: ExecutorRefusal): string {
  const server = refusal.server_identity;
  const server_text = `server ${server.application_revision} (protocol ${server.protocol_version}, outbox ${server.spool_format_version})`;
  const peer = refusal.peer_identity;
  if (peer === null) {
    return (
      `${refusal.reason}: the Mac runs the old WebSocket transport; ${server_text}. ` +
      'Last contact confirmed a version mismatch.'
    );
  }
  return (
    `${refusal.reason}: Mac ${peer.application_revision} ` +
    `(protocol ${peer.protocol_version}, outbox ${peer.spool_format_version}); ` +
    `${server_text}. Last contact confirmed a version mismatch.`
  );
}
export function execution_unavailable_reason(sql: ReadSql, locale: TicketLocale): string {
  if (locale === 'macos') {
    const record = read_presence(sql);
    if (record?.refusal) return _render_refusal(record.refusal);
    if (can_execute_locale(sql, locale)) return WAITING_FOR_THE_MAC;
    return 'Mac Tickets cannot start: no compatible Mac executor has leased yet';
  }
  return `The Ticket locale ${locale} cannot execute on this machine`;
}

export function can_execute_machine(machine_id: string, connection: ReadSql): boolean {
  const record = read_presence(connection, machine_id);
  return (
    record !== null &&
    record.executor_incarnation !== null &&
    record.identity !== null &&
    record.refusal === null &&
    supports_executor_versions(
      record.identity.protocol_version,
      record.identity.spool_format_version,
    )
  );
}

export function machine_unavailable_reason(machine_id: string, connection: ReadSql): string {
  const machine = read_machine(connection, machine_id);
  if (machine_id === ORIGINAL_MAC_MACHINE_ID) {
    const reason = execution_unavailable_reason(connection, 'macos');
    return machine.name === 'Mac' ? reason : reason.replace(/the Mac|Mac/g, () => machine.name);
  }
  return `Waiting for ${machine.name}`;
}
