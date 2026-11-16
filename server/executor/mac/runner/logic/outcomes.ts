/** Command consequences are deterministic across live settlement and journal replay. */
import { createHash } from 'node:crypto';
import { is_record as is_json_object } from '../../../../core/records.ts';
import {
  BackendPromptRejected,
  BackendSpawnFailed,
  SessionLoadFailed,
  NeedsRebind,
  PromptWriteRefused,
  TurnEnded,
} from '../../../../conversation/backends/contracts.ts';
import { as_object, decode_value, encode_backend_event, encode_value } from '../../../protocol.ts';
import { inspect, isDeepStrictEqual } from 'node:util';
import type { WireInput } from '../../../protocol.ts';
import type {
  ChildCommand,
  CommandDisposition,
  CommandOutcome,
  CommandResult,
  JsonObject,
  MailboxCommand,
  WireRootName,
} from '../../../wire.generated.ts';
import { MAX_TOOL_DETAIL_CHARACTERS } from '../../configuration.ts';
import { MacChildAdmissionRefused } from '../contracts.ts';
import type { OwedOutput } from '../command_contracts.ts';

export const CHILD_START_FAILED = 'child_start_failed';
const refusals = [
  [MacChildAdmissionRefused, '_CommandAdmissionRefused'],
  [BackendPromptRejected, 'BackendPromptRejected'],
  [BackendSpawnFailed, 'BackendSpawnFailed'],
  [SessionLoadFailed, 'SessionLoadFailed'],
  [NeedsRebind, 'NeedsRebind'],
  [PromptWriteRefused, 'PromptWriteRefused'],
] as const;

/** Compare decoded native values, independently of JSON property order. */
export function same_wire<K extends WireRootName>(
  left: WireInput<K>,
  right: WireInput<K>,
  root: K,
): boolean {
  return isDeepStrictEqual(encode_value(left, root), encode_value(right, root));
}
export function bounded_detail(value: string): string {
  let end = 0;
  let count = 0;
  for (const point of value) {
    if (count++ === MAX_TOOL_DETAIL_CHARACTERS) break;
    end += point.length;
  }
  return value.slice(0, end);
}
export function error_message(error: unknown): string {
  return error instanceof Error
    ? error.message
    : typeof error === 'string'
      ? error
      : inspect(error);
}
export function outcome(
  command: ChildCommand,
  disposition: CommandDisposition,
  result: JsonObject,
): CommandOutcome {
  return { command_id: command.command_id, child: command.child, disposition, result };
}
export function error_outcome(command: ChildCommand, error: unknown): CommandOutcome {
  const refusal = refusals.find(([kind]) => error instanceof kind);
  const name =
    error instanceof Error
      ? error.name === 'Error'
        ? error.constructor.name
        : error.name
      : 'Error';
  return outcome(command, refusal ? 'refused' : 'uncertain', {
    error_type: refusal?.[1] ?? name,
    error_message: bounded_detail(error_message(error)),
  });
}
export function outcome_detail(value: CommandOutcome, fallback: string): string {
  const detail = [value.result.error_message, value.result.reason, fallback].find(Boolean);
  return typeof detail === 'string' ? detail : inspect(detail);
}
function digest(value: string): string {
  return createHash('sha256').update(value).digest('hex');
}
function stop_proof(value: CommandOutcome, submission_id: string): OwedOutput {
  return {
    conversation_id: value.child.conversation_id,
    kind: 'child_stopped',
    payload: as_object(value, 'CommandOutcome'),
    submission_id,
  };
}
export function owed_outputs(command: ChildCommand, value: CommandOutcome): readonly OwedOutput[] {
  if (value.disposition === 'succeeded') {
    if (command.operation === 'stop')
      return [stop_proof(value, 'stopped_' + digest(command.command_id))];
    const application = value.result.answer_application;
    if (
      (command.operation === 'answer_permission_ask' ||
        command.operation === 'answer_user_input') &&
      is_json_object(application)
    ) {
      const answer = decode_value(application, 'AnswerApplication');
      return [
        {
          conversation_id: answer.ask.child.conversation_id,
          kind: 'answer_application',
          payload: as_object(answer, 'AnswerApplication'),
          submission_id: 'application_' + digest(answer.answer_id),
        },
      ];
    }
    return [];
  }
  if (command.operation !== 'write_prompt' || command.turn === null) return [];
  const detail = bounded_detail(outcome_detail(value, value.disposition));
  const text =
    value.disposition === 'refused'
      ? `The Mac could not deliver this message: ${detail}`
      : `The Mac lost track of this message; it may have reached the agent: ${detail}`;
  const [method, arguments_] = encode_backend_event(
    new TurnEnded({ ending: 'failed', error_summary: text }),
  );
  const outputs: OwedOutput[] = [
    {
      conversation_id: command.child.conversation_id,
      kind: 'backend_event',
      payload: as_object(
        { child: command.child, method, turn: command.turn, arguments: arguments_ },
        'BackendEvent',
      ),
      submission_id: 'turn_failed_' + digest(command.command_id),
    },
  ];
  if (value.result.reason === CHILD_START_FAILED)
    outputs.push(
      stop_proof(
        {
          command_id: 'start_failed_stop_' + command.child.child_id,
          child: command.child,
          disposition: 'succeeded',
          result: {},
        },
        'start_failed_stop_' + digest(command.child.child_id),
      ),
    );
  return outputs;
}
/** Imported by the journal worker, using the same policy as live settlement. */
export function command_owes_outputs(command: MailboxCommand, result: CommandResult): boolean {
  return (
    command.child_command != null &&
    result.child_outcome != null &&
    owed_outputs(command.child_command, result.child_outcome).length !== 0
  );
}
