/** A replaced run and its recovery evidence commit together through one invocation writer. */
import type { ChildReference } from '../conversation/backends/contracts.ts';
import type { Database, ReadSql } from '../core/database/contracts.ts';
import { import_captured_turns } from '../conversation/deploy_interruptions/data.ts';
import { read_unresolved_turns } from '../conversation/deploy_interruptions/capture.ts';
import { decode_json, KeyError } from './protocol.ts';
import { python_repr } from './protocol_errors.ts';
import { close_child_in_transaction } from './data/runs.ts';
const source_string = (value: unknown): string =>
  typeof value === 'string' ? value : python_repr(value);
function field(record: Record<string, unknown>, key: string): unknown {
  if (!Object.hasOwn(record, key)) throw new KeyError(key);
  return record[key];
}
function answers_the_agent_may_not_have(
  sql: ReadSql,
  reference: ChildReference,
  turn_number: number,
): string | null {
  function stored(kind: string, key: string, value: string): Record<string, unknown> | null {
    const row = sql.get<{ payload: string }>(
      'SELECT payload FROM conversation_events WHERE conversation_id=? AND kind=? AND json_extract(payload,?)=? ORDER BY sequence DESC LIMIT 1',
      [reference.conversation_id, kind, '$.' + key, value],
    );
    return row === undefined ? null : JSON.parse(row.payload);
  }
  const lines: string[] = [];
  for (const row of sql.all<{
    command_id: string;
    command_json: string;
    state: string;
    outcome_json: string | null;
  }>(
    "SELECT command_id,command_json,state,outcome_json FROM executor_commands WHERE executor_incarnation=? AND child_id=? AND turn_number=? AND operation IN ('answer_permission_ask','answer_user_input') ORDER BY command_sequence",
    [reference.executor_incarnation, reference.child_id, turn_number],
  )) {
    if (stored('answer_applied', 'answer_id', row.command_id) !== null) continue;
    const command = decode_json(row.command_json, 'MailboxCommand').child_command;
    if (command == null) continue;
    const outcome =
      row.outcome_json === null
        ? null
        : decode_json(row.outcome_json, 'CommandResult').child_outcome;
    if (row.state === 'expired') continue;
    const never_reached = row.state === 'withdrawn' || outcome?.disposition === 'refused';
    const args = command.arguments;
    let asked: readonly (readonly [string, string])[];
    if (args.kind === 'answer_permission_ask') {
      const ask = stored('permission_asked', 'ask_id', `${turn_number}:permission:${args.ask_id}`);
      const options = (ask === null ? [] : field(ask, 'options')) as Record<string, unknown>[];
      const option = options.find((option) => field(option, 'option_id') === args.option_id);
      asked = [
        [
          ask === null ? args.ask_id : source_string(field(ask, 'title')),
          source_string(option === undefined ? args.option_id : field(option, 'label')),
        ],
      ];
    } else if (args.kind === 'answer_user_input') {
      const request = stored(
        'user_input_requested',
        'request_id',
        `${turn_number}:user_input:${args.request_id}`,
      );
      const questions = new Map(
        ((request === null ? [] : field(request, 'questions')) as Record<string, unknown>[]).map(
          (question) => [
            source_string(field(question, 'question_id')),
            source_string(field(question, 'question')),
          ],
        ),
      );
      asked = args.answers.map((answer) => [
        questions.get(answer.question_id) ?? answer.question_id,
        answer.answers.join(', '),
      ]);
    } else continue;
    for (const [question, answer] of asked)
      lines.push(
        `Before the restart you asked: "${question}". The answer was: ${answer}. ` +
          (never_reached
            ? 'That answer never reached you.'
            : 'That answer may already have reached you before the restart.'),
      );
  }
  return lines.join(' ') || null;
}
export function close_run_and_record_mac_process_interruption(
  database: Database,
  reference: ChildReference,
  replaced_by: string,
  now_ms: number,
  options: { undelivered_message?: string | null } = {},
): void {
  database.write((tx) => {
    const conversation_id = reference.conversation_id,
      now = Math.floor(now_ms / 1000);
    close_child_in_transaction(tx, reference, now_ms);
    const turns = read_unresolved_turns(tx, { conversation_ids: [conversation_id] });
    const notes = new Map<string, string>();
    for (const turn of turns) {
      const key = JSON.stringify([turn.conversation_id, turn.turn_number]);
      let note = answers_the_agent_may_not_have(tx, reference, turn.turn_number);
      if (options.undelivered_message != null)
        note =
          'The message that opened this turn never reached you: the Mac ' +
          `restarted before fetching it. The message was: ${options.undelivered_message}` +
          (note === null ? '' : ' ' + note);
      if (note !== null) notes.set(key, note);
    }
    if (turns.length)
      import_captured_turns(
        tx,
        {
          deployment_id: `mac-process:${replaced_by}`,
          capture_id: `mac-process:${replaced_by}:${conversation_id}`,
          captured_at: now,
          state: 'captured',
          detail: null,
          turns,
          version: 1,
        },
        now,
        {
          claim_error_reason: 'interrupted by a Mac restart',
          ended_by: 'A Mac restart',
          turn_notes: notes,
        },
      );
  });
}
