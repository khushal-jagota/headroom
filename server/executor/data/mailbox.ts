/**
 * Scoped mailbox SQL assumes an authorized caller and a caller-owned SQL capability.
 * Poll/result callers require transport ingress, protocol and current-identity admission.
 * Row fences do not authenticate an executor. These helpers never own a connection.
 */
import { randomUUID } from 'node:crypto';
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import { isDeepStrictEqual } from 'node:util';
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
  ExecutorValidationError,
} from '../protocol.ts';
import { expiry_reason } from '../logic/command_expiry.ts';
import { counter } from '../logic/counters.ts';
import { current_incarnation } from '../vps_presence_store.ts';
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
  return JSON.stringify(as_object(value, 'CommandResult'));
}
function result(text: string | null): WireValue<'CommandResult'> | null {
  return text === null ? null : decode_json(text, 'CommandResult');
}
const equivalent = isDeepStrictEqual;
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
      child?.turn == null ? null : child.turn.turn_number,
      command.created_at_vps_ms,
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
    counter(sequence),
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
      throw new ExecutorValidationError('command identity reused with different content');
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
  if (incarnation === null) throw new ExecutorValidationError('no Mac executor process is known');
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
/** Raw rows only. Protocol parsing and serialization run after the SQL scope ends. */
interface FetchRow {
  sequence: string;
  command_json: string;
  state: string;
  conversation_id: string | null;
  outcome_present: number;
}
interface FetchPresence {
  executor_incarnation: string | null;
  last_lease_at_ms: number | null;
  identity_json: string | null;
  refusal_json: string | null;
}
export interface FetchRows {
  replaced: readonly FetchRow[];
  candidates: readonly FetchRow[];
  presence: FetchPresence | null;
}
type Preparation<T> = { ok: true; value: T } | { ok: false; error: unknown };
interface PreparedOutcome {
  result: WireValue<'CommandResult'>;
  text: string;
}
interface PreparedCommand {
  json: string;
  bytes: number;
  command: Preparation<WireValue<'MailboxCommand'>>;
  outcomes: ReadonlyMap<string, Preparation<PreparedOutcome>>;
}
export interface PreparedFetch {
  commands: ReadonlyMap<string, PreparedCommand>;
  presence: FetchPresence | null;
  presence_parse: Preparation<void>;
}
function attempt<T>(work: () => T): Preparation<T> {
  try {
    return { ok: true, value: work() };
  } catch (error) {
    return { ok: false, error };
  }
}
function prepared_value<T>(value: Preparation<T>): T {
  if (!value.ok) throw value.error;
  return value.value;
}
/** Caller supplies an admitted poll. This takes no new authority or owned SQL scope. */
export function read_fetch_rows(sql: ReadSql, incarnation: string, after: number): FetchRows {
  const presence =
    sql.get<FetchPresence>(
      'SELECT executor_incarnation,last_lease_at_ms,identity_json,refusal_json FROM mac_executor_presence WHERE singleton=1',
    ) ?? null;
  const replaced =
    presence?.executor_incarnation === incarnation
      ? sql.all<FetchRow>(
          'SELECT CAST(c.command_sequence AS TEXT) AS sequence,c.command_json,c.state,c.conversation_id,c.outcome_json IS NOT NULL AS outcome_present ' +
            'FROM executor_commands AS c INDEXED BY executor_commands_open ' +
            'JOIN executor_incarnations AS i ON i.executor_incarnation=c.executor_incarnation ' +
            "WHERE c.state IN ('waiting','delivered') AND c.state='delivered' AND c.executor_incarnation<>? " +
            'AND i.replaced_at_ms IS NOT NULL ORDER BY c.command_sequence',
          [incarnation],
        )
      : [];
  const candidates = sql.all<FetchRow>(
    'SELECT CAST(command_sequence AS TEXT) AS sequence,command_json,state,conversation_id,0 AS outcome_present ' +
      'FROM executor_commands INDEXED BY executor_commands_open ' +
      "WHERE executor_incarnation=? AND command_sequence>? AND state IN ('waiting','delivered') ORDER BY command_sequence LIMIT ?",
    [incarnation, after, MAX_MAILBOX_POLL_COMMANDS],
  );
  return { replaced, candidates, presence };
}
/** Never call this while a transaction is open. Errors stay deferred until source visitation. */
export async function prepare_fetch(rows: FetchRows): Promise<PreparedFetch> {
  const commands = new Map<string, PreparedCommand>();
  for (const [replacing, selection] of [
    [true, rows.replaced],
    [false, rows.candidates],
  ] as const) {
    for (const row of selection) {
      const command = attempt(() => decode_json(row.command_json, 'MailboxCommand'));
      const outcomes = new Map<string, Preparation<PreparedOutcome>>();
      if (command.ok) {
        const make = (
          key: string,
          disposition: 'refused' | 'uncertain',
          reason: CommandSettlementReason,
        ) => {
          outcomes.set(
            key,
            attempt(() => {
              const result = settlement_result(command.value, disposition, reason);
              return { result, text: outcome_json(result) };
            }),
          );
        };
        if (replacing) {
          make('replacement_refused', 'refused', 'executor_process_replaced');
          make('replacement_uncertain', 'uncertain', 'executor_process_replaced');
        } else {
          switch (operation(command.value)) {
            case 'steer':
              make('steer_turn_not_running', 'refused', 'steer_turn_not_running');
              make('steer_delivery_limit_passed', 'refused', 'steer_delivery_limit_passed');
              break;
            case 'answer_permission_ask':
            case 'answer_user_input':
              make('answered_request_not_open', 'refused', 'answered_request_not_open');
              break;
            case 'read_model_list':
              make('model_list_limit_passed', 'refused', 'model_list_limit_passed');
              break;
          }
        }
      }
      commands.set(row.sequence, {
        json: row.command_json,
        bytes: Buffer.byteLength(row.command_json) + 1,
        command,
        outcomes,
      });
      // A command is at most one envelope. Yield between commands without a worker or queue.
      await new Promise<void>((resolve) => setImmediate(resolve));
    }
  }
  const presence_parse = attempt(() => {
    // Stored identities/refusals retain Python's shape errors, outside the writer.
    if (rows.presence?.identity_json != null)
      decode_json(rows.presence.identity_json, 'ExecutorIdentity');
    if (rows.presence?.refusal_json != null)
      decode_json(rows.presence.refusal_json, 'ExecutorRefusal');
  });
  return { commands, presence: rows.presence, presence_parse };
}
/**
 * Caller owns the writer and the poll's ingress/protocol/current-identity admission.
 * Null requests a fresh preparation and guarantees that this call made no effects.
 */
export function fetch(
  tx: TransactionSql,
  incarnation: string,
  after: number,
  now: number,
  arrived_at: number,
  prepared: PreparedFetch,
): MailboxFetchResult | null {
  const rows = read_fetch_rows(tx, incarnation, after);
  const stale = now - arrived_at > HELD_POLL_EVIDENCE_MS;
  // Validate every freshly selected row before the first effect, including rows beyond budget.
  if (
    [...rows.replaced, ...rows.candidates].some(
      (row) => prepared.commands.get(row.sequence)?.json !== row.command_json,
    )
  )
    return null;
  if (
    stale &&
    (rows.presence?.identity_json !== prepared.presence?.identity_json ||
      rows.presence?.refusal_json !== prepared.presence?.refusal_json)
  )
    return null;
  const settled: MailboxSettlement[] = [];
  for (const row of rows.replaced) {
    const entry = prepared.commands.get(row.sequence)!;
    const command = prepared_value(entry.command);
    const never_ran = counter(row.sequence) > after && row.outcome_present === 0;
    const outcome = prepared_value(
      entry.outcomes.get(never_ran ? 'replacement_refused' : 'replacement_uncertain')!,
    );
    tx.run(
      'UPDATE executor_commands SET state=?,outcome_json=?,finished_at_ms=? WHERE command_sequence=?',
      [never_ran ? 'withdrawn' : 'finished', outcome.text, now, counter(row.sequence)],
    );
    settled.push([command, outcome.result]);
  }
  if (stale) {
    prepared_value(prepared.presence_parse);
    const last = rows.presence?.last_lease_at_ms;
    if (last == null || now - last > HELD_POLL_EVIDENCE_MS) return { reply: null, settled };
  }
  const facts = new Map<string, ReturnType<typeof read_facts>>();
  const answered: WireValue<'MailboxCommand'>[] = [];
  let budget = MAX_MAILBOX_POLL_BYTES - 16;
  for (const row of rows.candidates) {
    const entry = prepared.commands.get(row.sequence)!;
    const command = prepared_value(entry.command);
    if (row.state === 'waiting') {
      if (row.conversation_id !== null && !facts.has(row.conversation_id))
        facts.set(row.conversation_id, read_facts(tx, row.conversation_id));
      const reason = expiry_reason(command, facts.get(row.conversation_id ?? '') ?? [], now);
      if (reason !== null) {
        const outcome = prepared_value(entry.outcomes.get(reason)!);
        tx.run(
          "UPDATE executor_commands SET state='expired',outcome_json=?,finished_at_ms=? WHERE command_sequence=?",
          [outcome.text, now, counter(row.sequence)],
        );
        settled.push([command, outcome.result]);
        continue;
      }
    }
    // The source decodes even the first over-budget candidate before it stops here.
    if (answered.length && entry.bytes > budget) break;
    budget -= entry.bytes;
    if (row.state === 'waiting')
      tx.run(
        "UPDATE executor_commands SET state='delivered',delivered_at_ms=? WHERE command_sequence=?",
        [now, counter(row.sequence)],
      );
    answered.push(command);
  }
  return { reply: { commands: answered }, settled };
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
/** Caller already admitted the reporting identity. Row checks do not replace ingress authority. */
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
/** The inbox already authorized this child_stopped proof before this participant runs. */
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
/** Stateless participant in an admitted lease transaction. Publish returned pairs after commit. */
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
export function high_sequence(sql: ReadSql): number {
  return counter(
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
  turn: number,
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
      [row.executor_incarnation, row.child_id, counter(row.turn_number)],
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
          counter(candidate.finished) - counter(candidate.created) >=
            STEER_DELIVERY_LIMIT_SECONDS * 1000);
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
/** Stateless reads use the caller's SQL scope and are independent of mailbox admission. */
export class MailboxStallSource implements StallSource {
  tracked_waits(sql: ReadSql, now: number): readonly TrackedWait[] {
    return tracked_waits(sql, now);
  }
}
