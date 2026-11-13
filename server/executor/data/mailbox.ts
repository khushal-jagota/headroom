/** Scoped synchronous SQL for the durable command mailbox. Never owns a connection. */
import { randomUUID } from 'node:crypto';
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import { JsonNumber, stringify_python_json } from '../../core/http/numeric_json.ts';
import type { ChildReference } from '../../conversation/backends/contracts.ts';
import { read_facts } from '../../conversation/record/data/lifecycle.ts';
import { conversation_event_payload_from_canonical_json } from '../../conversation/record/codec.ts';
import { message_content_text } from '../../conversation/message_content.ts';
import type { PromptDeliveryUncertainEventPayload } from '../../conversation/events.ts';
import type { StallSource, TrackedWait } from '../../stalls/contracts.ts';
import type {
  CommandSettlementReason,
  MailboxFetchResult,
  MailboxRecordResult,
  MailboxRow,
  MailboxSettlement,
  MailboxSettlements,
  PromptWrittenAgain,
  LateSteerAfterRestart,
  WireValue,
} from '../contracts.ts';
import {
  HELD_POLL_EVIDENCE_MS,
  MAX_MAILBOX_POLL_BYTES,
  MAX_MAILBOX_POLL_COMMANDS,
  STEER_DELIVERY_LIMIT_SECONDS,
} from '../configuration.ts';
import {
  as_object,
  decode_json,
  decode_value,
  encode_bounded,
  integer_value,
  ValueError,
} from '../protocol.ts';
import { expiry_reason } from '../logic/command_expiry.ts';
import { read_presence } from '../vps_presence_store.ts';
import { counter } from '../logic/counters.ts';
import {
  open_child_assignment,
  prepare_command_in_transaction,
  record_prompt_outcome_in_transaction,
} from './child_custody.ts';

const settled_states = new Set(['finished', 'withdrawn', 'expired']);
export function is_settled(row: MailboxRow): boolean {
  return settled_states.has(row.state);
}
export function operation(command: WireValue<'MailboxCommand'>): string {
  return command.child_command?.operation ?? 'read_model_list';
}
function outcome_json(value: WireValue<'CommandResult'>): string {
  return stringify_python_json(as_object(value, 'CommandResult'), {
    sort_keys: true,
    compact: true,
  });
}
function result(text: string | null): WireValue<'CommandResult'> | null {
  return text === null ? null : decode_json(text, 'CommandResult');
}
export function current_incarnation(sql: ReadSql): string | null {
  return (
    sql.get<{ executor_incarnation: string | null }>(
      'SELECT executor_incarnation FROM mac_executor_presence WHERE singleton=1',
    )?.executor_incarnation ?? null
  );
}
/** Python typed equality compares numeric values, not JS object identity or token spelling. */
function equivalent(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  const numeric = (value: unknown): number | bigint | null =>
    value instanceof JsonNumber
      ? (value.integer ?? value.value)
      : typeof value === 'boolean'
        ? BigInt(Number(value))
        : typeof value === 'number' || typeof value === 'bigint'
          ? value
          : null;
  const a = numeric(left),
    b = numeric(right);
  if (a !== null && b !== null) {
    if (typeof a === 'bigint' && typeof b === 'number')
      return Number.isInteger(b) && a === BigInt(b);
    if (typeof b === 'bigint' && typeof a === 'number')
      return Number.isInteger(a) && BigInt(a) === b;
    return a === b;
  }
  if (left === null || right === null || typeof left !== 'object' || typeof right !== 'object')
    return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  const l = Object.entries(left),
    r = Object.entries(right);
  return (
    l.length === r.length &&
    l.every(
      ([key, item]) =>
        Object.hasOwn(right, key) && equivalent(item, (right as Record<string, unknown>)[key]),
    )
  );
}
export function command_row(sql: ReadSql, command_id: string): MailboxRow | null {
  const row = sql.get<{
    command_json: string;
    executor_incarnation: string;
    state: MailboxRow['state'];
    outcome_json: string | null;
  }>(
    'SELECT command_json,executor_incarnation,state,outcome_json FROM executor_commands WHERE command_id=?',
    [command_id],
  );
  return row
    ? {
        command: decode_json(row.command_json, 'MailboxCommand'),
        executor_incarnation: row.executor_incarnation,
        state: row.state,
        result: result(row.outcome_json),
      }
    : null;
}
export function settled_row(sql: ReadSql, command_id: string): MailboxRow | null {
  const row = command_row(sql, command_id);
  return row !== null && is_settled(row) ? row : null;
}
export function settlement_result(
  command: WireValue<'MailboxCommand'>,
  disposition: 'refused' | 'uncertain',
  reason: CommandSettlementReason,
): WireValue<'CommandResult'> {
  const child = command.child_command;
  return {
    command_id: command.command_id,
    model_list: null,
    child_outcome:
      child == null
        ? null
        : {
            command_id: command.command_id,
            child: child.child,
            disposition,
            result: { reason },
          },
  };
}
export function steer_never_arrived(outcome: WireValue<'CommandOutcome'> | null): boolean {
  return (
    outcome !== null &&
    (outcome.disposition === 'refused' ||
      (outcome.disposition === 'succeeded' && outcome.result.kind === 'steer_refused'))
  );
}
function insert(
  tx: TransactionSql,
  command: WireValue<'MailboxCommand'>,
  incarnation: string,
): WireValue<'MailboxCommand'> {
  const child = command.child_command;
  tx.run(
    'INSERT INTO executor_commands(command_id,executor_incarnation,operation,conversation_id,child_id,turn_number,command_json,state,created_at_ms) ' +
      "VALUES (?,?,?,?,?,?,'{}','waiting',?)",
    [
      command.command_id,
      incarnation,
      operation(command),
      child?.child.conversation_id ?? null,
      child?.child.child_id ?? null,
      child?.turn == null ? null : integer_value(child.turn.turn_number),
      integer_value(command.created_at_vps_ms),
    ],
  );
  // lastInsertRowid can already be rounded by the driver. Read text before leaving this writer.
  const sequence = tx.get<{ value: string }>(
    'SELECT CAST(last_insert_rowid() AS TEXT) AS value',
  )!.value;
  const numbered = { ...command, command_sequence: counter(sequence) };
  const body = encode_bounded(numbered, 'MailboxCommand');
  tx.run('UPDATE executor_commands SET command_json=? WHERE command_sequence=?', [
    Buffer.from(body).toString('utf8'),
    BigInt(sequence),
  ]);
  return decode_json(body, 'MailboxCommand');
}
export function insert_child_command(
  tx: TransactionSql,
  command: WireValue<'ChildCommand'>,
  now: number,
): WireValue<'MailboxCommand'> | WireValue<'CommandOutcome'> {
  command = decode_value(command, 'ChildCommand');
  const existing = command_row(tx, command.command_id);
  if (existing !== null) {
    if (!equivalent(existing.command.child_command, command))
      throw new ValueError('command identity reused with different content');
    return existing.command;
  }
  const child = command.child;
  if (
    current_incarnation(tx) !== child.executor_incarnation ||
    open_child_assignment(tx, child) === null
  )
    return {
      command_id: command.command_id,
      child,
      disposition: 'refused',
      result: { reason: 'executor_process_replaced' },
    };
  if (['start', 'write_prompt', 'stop'].includes(command.operation))
    prepare_command_in_transaction(tx, command);
  return insert(
    tx,
    {
      command_sequence: 0,
      command_id: command.command_id,
      created_at_vps_ms: now,
      child_command: command,
      model_list_request: null,
    },
    child.executor_incarnation,
  );
}
export function insert_model_list_request(
  tx: TransactionSql,
  request: WireValue<'ModelListRequest'>,
  now: number,
): WireValue<'MailboxCommand'> {
  const incarnation = current_incarnation(tx);
  if (incarnation === null) throw new ValueError('no Mac executor process is known');
  return insert(
    tx,
    {
      command_sequence: 0,
      command_id: randomUUID(),
      created_at_vps_ms: now,
      child_command: null,
      model_list_request: request,
    },
    incarnation,
  );
}
function settle_replaced_deliveries(
  tx: TransactionSql,
  incarnation: string,
  after: bigint,
  now: number,
): MailboxSettlement[] {
  if (current_incarnation(tx) !== incarnation) return [];
  const found: MailboxSettlement[] = [];
  const rows = tx.all<{ sequence: string; command_json: string; outcome_json: string | null }>(
    'SELECT CAST(c.command_sequence AS TEXT) AS sequence,c.command_json,c.outcome_json FROM executor_commands AS c ' +
      'JOIN executor_incarnations AS i ON i.executor_incarnation=c.executor_incarnation ' +
      "WHERE c.state='delivered' AND c.executor_incarnation<>? AND i.replaced_at_ms IS NOT NULL ORDER BY c.command_sequence",
    [incarnation],
  );
  for (const row of rows) {
    const command = decode_json(row.command_json, 'MailboxCommand');
    const never_ran = BigInt(row.sequence) > after && row.outcome_json === null;
    const outcome = settlement_result(
      command,
      never_ran ? 'refused' : 'uncertain',
      'executor_process_replaced',
    );
    tx.run(
      'UPDATE executor_commands SET state=?,outcome_json=?,finished_at_ms=? WHERE command_sequence=?',
      [never_ran ? 'withdrawn' : 'finished', outcome_json(outcome), now, BigInt(row.sequence)],
    );
    found.push([command, outcome]);
  }
  return found;
}
export function fetch(
  tx: TransactionSql,
  incarnation: string,
  after: bigint,
  now: number,
  arrived_at: number,
): MailboxFetchResult {
  const settled = settle_replaced_deliveries(tx, incarnation, after, now);
  if (now - arrived_at > HELD_POLL_EVIDENCE_MS) {
    const last = read_presence(tx)?.last_lease_at_ms;
    if (last == null || now - last > HELD_POLL_EVIDENCE_MS) return { reply: null, settled };
  }
  const rows = tx.all<{
    sequence: string;
    command_json: string;
    state: string;
    conversation_id: string | null;
  }>(
    'SELECT CAST(command_sequence AS TEXT) AS sequence,command_json,state,conversation_id FROM executor_commands ' +
      "WHERE executor_incarnation=? AND command_sequence>? AND state IN ('waiting','delivered') ORDER BY command_sequence LIMIT ?",
    [incarnation, after, MAX_MAILBOX_POLL_COMMANDS],
  );
  const facts = new Map<string, ReturnType<typeof read_facts>>();
  const answered: WireValue<'MailboxCommand'>[] = [];
  const notifications: MailboxSettlement[] = [...settled];
  let budget = MAX_MAILBOX_POLL_BYTES - 16;
  for (const row of rows) {
    const command = decode_json(row.command_json, 'MailboxCommand');
    if (row.state === 'waiting') {
      if (row.conversation_id !== null && !facts.has(row.conversation_id))
        facts.set(row.conversation_id, read_facts(tx, row.conversation_id));
      const reason = expiry_reason(command, facts.get(row.conversation_id ?? '') ?? [], now);
      if (reason !== null) {
        const outcome = settlement_result(command, 'refused', reason);
        tx.run(
          "UPDATE executor_commands SET state='expired',outcome_json=?,finished_at_ms=? WHERE command_sequence=?",
          [outcome_json(outcome), now, BigInt(row.sequence)],
        );
        notifications.push([command, outcome]);
        continue;
      }
    }
    const size = Buffer.byteLength(row.command_json) + 1;
    if (answered.length && size > budget) break;
    budget -= size;
    if (row.state === 'waiting')
      tx.run(
        "UPDATE executor_commands SET state='delivered',delivered_at_ms=? WHERE command_sequence=?",
        [now, BigInt(row.sequence)],
      );
    answered.push(command);
  }
  return { reply: { commands: answered }, settled: notifications };
}
function result_refusal(
  row: MailboxRow | null,
  value: WireValue<'CommandResult'>,
  incarnation: string,
): string | null {
  if (row === null) return 'unknown_command';
  if (row.executor_incarnation !== incarnation) return 'command_of_another_executor_process';
  if (row.state === 'withdrawn' || row.state === 'expired') return `command_${row.state}`;
  const child = row.command.child_command;
  if (child == null)
    return value.model_list == null || value.child_outcome != null ? 'malformed_result' : null;
  const outcome = value.child_outcome;
  return outcome == null ||
    value.model_list != null ||
    outcome.command_id !== value.command_id ||
    !equivalent(outcome.child, child.child)
    ? 'malformed_result'
    : null;
}
export function record(
  tx: TransactionSql,
  incarnation: string,
  values: readonly WireValue<'CommandResult'>[],
  now: number,
): MailboxRecordResult {
  const recorded: string[] = [],
    rejected: WireValue<'RejectedCommandResult'>[] = [];
  const settled: MailboxSettlement[] = [];
  for (const incoming of values) {
    const value = decode_value(incoming, 'CommandResult');
    const row = command_row(tx, value.command_id);
    const reason = result_refusal(row, value, incarnation);
    if (reason !== null) {
      rejected.push({ command_id: value.command_id, reason });
      continue;
    }
    recorded.push(value.command_id);
    if (row!.state === 'finished') continue;
    const command = row!.command.child_command;
    const outcome = value.child_outcome;
    if (command?.operation === 'stop' && outcome?.disposition === 'succeeded') {
      tx.run(
        "UPDATE executor_commands SET outcome_json=COALESCE(outcome_json,?),state='delivered',delivered_at_ms=COALESCE(delivered_at_ms,?) WHERE command_id=?",
        [outcome_json(value), now, value.command_id],
      );
      continue;
    }
    tx.run(
      "UPDATE executor_commands SET state='finished',outcome_json=?,finished_at_ms=? WHERE command_id=?",
      [outcome_json(value), now, value.command_id],
    );
    if (command != null && outcome != null)
      record_prompt_outcome_in_transaction(tx, command, outcome);
    settled.push([row!.command, value]);
  }
  return { reply: { recorded, rejected }, settled };
}
export function withdraw(
  tx: TransactionSql,
  command_id: string,
  reason: CommandSettlementReason,
  now: number,
): MailboxSettlements {
  const row = command_row(tx, command_id);
  if (row === null || row.state !== 'waiting') return [];
  const outcome = settlement_result(row.command, 'refused', reason);
  tx.run(
    "UPDATE executor_commands SET state='withdrawn',outcome_json=?,finished_at_ms=? WHERE command_id=? AND state='waiting'",
    [outcome_json(outcome), now, command_id],
  );
  return [[row.command, outcome]];
}
export function finish_by_proof(
  tx: TransactionSql,
  command_id: string,
  outcome: WireValue<'CommandOutcome'>,
  now: number,
): MailboxSettlements {
  const row = command_row(tx, command_id);
  if (row === null || is_settled(row)) return [];
  const value =
    row.result ??
    decode_value({ command_id, child_outcome: outcome, model_list: null }, 'CommandResult');
  tx.run(
    "UPDATE executor_commands SET state='finished',outcome_json=?,finished_at_ms=? WHERE command_id=?",
    [outcome_json(value), now, command_id],
  );
  return [[row.command, value]];
}
export function replace_incarnation_in_transaction(
  tx: TransactionSql,
  replaced: string,
  _current: string,
  now: number,
): MailboxSettlements {
  const settled: MailboxSettlement[] = [];
  for (const row of tx.all<{ command_json: string }>(
    "SELECT command_json FROM executor_commands WHERE executor_incarnation=? AND state='waiting' ORDER BY command_sequence",
    [replaced],
  )) {
    const command = decode_json(row.command_json, 'MailboxCommand');
    const outcome = settlement_result(command, 'refused', 'executor_process_replaced');
    tx.run(
      "UPDATE executor_commands SET state='withdrawn',outcome_json=?,finished_at_ms=? WHERE command_id=?",
      [outcome_json(outcome), now, command.command_id],
    );
    settled.push([command, outcome]);
  }
  return settled;
}
export function high_sequence(sql: ReadSql): bigint {
  return BigInt(
    sql.get<{ value: string }>(
      'SELECT CAST(COALESCE(MAX(command_sequence),0) AS TEXT) AS value FROM executor_commands',
    )!.value,
  );
}
export function open_command_ids_of(sql: ReadSql, child: ChildReference): readonly string[] {
  return sql
    .all<{ command_id: string }>(
      "SELECT command_id FROM executor_commands WHERE executor_incarnation=? AND child_id=? AND state IN ('waiting','delivered') ORDER BY command_sequence",
      [child.executor_incarnation, child.child_id],
    )
    .map((row) => row.command_id);
}
export function command_for_input(
  sql: ReadSql,
  command_id: string,
): WireValue<'ChildCommand'> | null {
  const row = command_row(sql, command_id);
  return row === null || is_settled(row) ? null : (row.command.child_command ?? null);
}
export function start_of(
  sql: ReadSql,
  child: ChildReference,
): WireValue<'ResolvedConversationStart'> | null {
  const row = sql.get<{ command_json: string }>(
    "SELECT command_json FROM executor_commands WHERE child_id=? AND operation='start' ORDER BY command_sequence DESC LIMIT 1",
    [child.child_id],
  );
  const command = row ? decode_json(row.command_json, 'MailboxCommand').child_command : null;
  return command != null && equivalent(command.child, child) && command.arguments.kind === 'start'
    ? command.arguments.resolved_start
    : null;
}
export function prompt_withdrawn_at_replacement(
  sql: ReadSql,
  child: ChildReference,
  turn: bigint,
): WireValue<'WritePrompt'> | null {
  const row = sql.get<{ command_json: string }>(
    'SELECT command_json FROM executor_commands WHERE executor_incarnation=? AND child_id=? ' +
      "AND operation='write_prompt' AND turn_number=? AND state='withdrawn' AND json_extract(outcome_json,'$.child_outcome.result.reason')=? " +
      "AND NOT EXISTS (SELECT 1 FROM executor_commands WHERE executor_incarnation=? AND child_id=? AND operation='cancel_running_turn')",
    [
      child.executor_incarnation,
      child.child_id,
      turn,
      'executor_process_replaced',
      child.executor_incarnation,
      child.child_id,
    ],
  );
  const command = row ? decode_json(row.command_json, 'MailboxCommand').child_command : null;
  return command?.arguments.kind === 'write_prompt' ? command.arguments : null;
}
export function prompts_written_again(sql: ReadSql): readonly PromptWrittenAgain[] {
  const rows = sql.all<{
    executor_incarnation: string;
    child_id: string;
    conversation_id: string;
    turn_number: string;
    command_json: string;
  }>(
    'SELECT r.executor_incarnation,r.child_id,r.conversation_id,CAST(r.turn_number AS TEXT) AS turn_number,c.command_json FROM mac_runs AS r ' +
      'JOIN mac_executor_presence AS p ON p.singleton=1 AND p.executor_incarnation=r.executor_incarnation ' +
      "JOIN executor_commands AS c ON c.conversation_id=r.conversation_id AND c.turn_number=r.turn_number AND c.operation='write_prompt' " +
      "AND c.child_id<>r.child_id AND c.state='withdrawn' AND json_extract(c.outcome_json,'$.child_outcome.result.reason')=? WHERE r.stop_state='open'",
    ['executor_process_replaced'],
  );
  const found: PromptWrittenAgain[] = [];
  for (const row of rows) {
    const command = decode_json(row.command_json, 'MailboxCommand').child_command;
    if (command?.arguments.kind !== 'write_prompt') continue;
    const written = sql.get(
      "SELECT 1 FROM executor_commands WHERE executor_incarnation=? AND child_id=? AND operation='write_prompt' AND turn_number=?",
      [row.executor_incarnation, row.child_id, BigInt(row.turn_number)],
    );
    const started = sql.get(
      "SELECT 1 FROM executor_commands WHERE executor_incarnation=? AND child_id=? AND operation='start'",
      [row.executor_incarnation, row.child_id],
    );
    found.push([
      {
        executor_incarnation: row.executor_incarnation,
        child_id: row.child_id,
        conversation_id: row.conversation_id,
      },
      counter(row.turn_number),
      command.arguments,
      written !== undefined,
      started !== undefined,
    ]);
  }
  return found;
}
export function late_steers_left_by_a_restart(sql: ReadSql): readonly LateSteerAfterRestart[] {
  const uncertain = sql.all<{ conversation_id: string; sequence: string; payload: string }>(
    "SELECT e.conversation_id,CAST(e.sequence AS TEXT) AS sequence,e.payload FROM conversation_events AS e WHERE e.kind='prompt_delivery_uncertain' " +
      "AND json_extract(e.payload,'$.mode')='steer' AND NOT EXISTS (SELECT 1 FROM conversation_events AS later WHERE later.conversation_id=e.conversation_id " +
      "AND later.sequence>e.sequence AND later.kind IN ('prompt','prompt_delivery_refused','prompt_delivery_uncertain','prompt_discarded') " +
      "AND json_extract(later.payload,'$.sender_message_id')=json_extract(e.payload,'$.sender_message_id')) ORDER BY e.conversation_id,e.sequence",
  );
  const found: LateSteerAfterRestart[] = [];
  const used = new Set<string>();
  for (const row of uncertain) {
    const payload = conversation_event_payload_from_canonical_json(
      'prompt_delivery_uncertain',
      row.payload,
    ) as PromptDeliveryUncertainEventPayload;
    const text = message_content_text(payload.content),
      label = payload.sender_label;
    for (const candidate of sql.all<{
      command_id: string;
      command_json: string;
      state: string;
      outcome_json: string | null;
      created: string;
      finished: string | null;
    }>(
      'SELECT command_id,command_json,state,outcome_json,CAST(created_at_ms AS TEXT) AS created,CAST(finished_at_ms AS TEXT) AS finished ' +
        "FROM executor_commands WHERE conversation_id=? AND operation='steer' AND state<>'waiting' ORDER BY command_sequence",
      [row.conversation_id],
    )) {
      if (used.has(candidate.command_id)) continue;
      const late =
        candidate.state === 'delivered' ||
        (candidate.finished !== null &&
          BigInt(candidate.finished) - BigInt(candidate.created) >=
            BigInt(STEER_DELIVERY_LIMIT_SECONDS * 1000));
      const child = decode_json(candidate.command_json, 'MailboxCommand').child_command;
      const args = child?.arguments;
      if (!late || args?.kind !== 'steer' || args.sender_label !== label) continue;
      const command_text = args.content
        .filter((piece) => 'text' in piece)
        .map((piece) => piece.text)
        .join('\n\n');
      if (command_text !== text) continue;
      used.add(candidate.command_id);
      const outcome = result(candidate.outcome_json)?.child_outcome ?? null;
      found.push([
        row.conversation_id,
        counter(row.sequence),
        candidate.command_id,
        candidate.state === 'delivered' ? null : steer_never_arrived(outcome),
      ]);
      break;
    }
  }
  return found;
}
export function tracked_waits(sql: ReadSql, _now: number): readonly TrackedWait[] {
  const waits: TrackedWait[] = [];
  for (const row of sql.all<{
    command_id: string;
    operation: string;
    conversation_id: string | null;
    state: string;
    created_at_ms: number;
  }>(
    "SELECT command_id,operation,conversation_id,state,created_at_ms FROM executor_commands WHERE state IN ('waiting','delivered') ORDER BY command_sequence",
  )) {
    if (row.state === 'waiting')
      waits.push({
        kind: 'mac_command_unfetched',
        wait_key: row.command_id,
        conversation_id: row.conversation_id,
        detail: `The Mac has not fetched a ${row.operation} command`,
        started_at_ms: row.created_at_ms,
      });
    if (row.operation === 'stop')
      waits.push({
        kind: 'mac_stop_unproven',
        wait_key: row.command_id,
        conversation_id: row.conversation_id,
        detail: 'A stop has no proof that the agent stopped',
        started_at_ms: row.created_at_ms,
      });
  }
  for (const row of sql.all<{ command_id: string; conversation_id: string; created_at_ms: number }>(
    'SELECT c.command_id,c.conversation_id,c.created_at_ms FROM executor_commands AS c JOIN mac_runs AS r ON r.conversation_id=c.conversation_id ' +
      'AND r.child_id=c.child_id AND r.executor_incarnation=c.executor_incarnation JOIN mac_executor_presence AS p ON p.singleton=1 ' +
      "AND p.executor_incarnation=r.executor_incarnation WHERE c.operation='stop' AND c.state='finished' AND r.stop_state='stopping'",
  ))
    waits.push({
      kind: 'mac_stop_unproven',
      wait_key: row.command_id,
      conversation_id: row.conversation_id,
      detail: 'A stop failed, and the agent may still be running',
      started_at_ms: row.created_at_ms,
    });
  return waits;
}
export class MailboxStallSource implements StallSource {
  tracked_waits(sql: ReadSql, now: number): readonly TrackedWait[] {
    return tracked_waits(sql, now);
  }
}
