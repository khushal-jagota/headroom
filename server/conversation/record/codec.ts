/** The Python record's canonical JSON and legacy stored-payload decoder. */
import * as E from '../events.ts';
import {
  AnswerFate,
  PromptDeliveryMode,
  PromptDeliveryRefusalReason,
  DeploymentRecoveryReference,
} from '../contracts.ts';
import type { Principal } from '../../core/contracts.ts';
import type { CredentialRequestRowBody } from '../../../web/src/lib/conversation/view/contracts.ts';
import { message_content_from_stored, message_content_json_entries } from '../message_content.ts';

type ObjectValue = Record<string, unknown>;
function object(value: unknown, detail: string): ObjectValue {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error(detail);
  return value as ObjectValue;
}
function required(stored: ObjectValue, key: string): unknown {
  if (!(key in stored)) throw new Error(`'${key}'`);
  return stored[key];
}
function text(stored: ObjectValue, key: string): string {
  const value = required(stored, key);
  if (typeof value !== 'string') throw new Error(`${key} must be text`);
  return value;
}
function optionalText(stored: ObjectValue, key: string): string | null {
  const value = stored[key];
  if (value === null || value === undefined) return null;
  if (typeof value !== 'string') throw new Error(`${key} must be text or absent`);
  return value;
}
function boolean(stored: ObjectValue, key: string): boolean {
  const value = required(stored, key);
  if (typeof value !== 'boolean') throw new Error(`${key} must be a boolean`);
  return value;
}
function optionalNumber(stored: ObjectValue, key: string, whole: boolean): number | null {
  const value = stored[key];
  if (value === null || value === undefined) return null;
  if (typeof value !== 'number' || (whole && !Number.isInteger(value)))
    throw new Error(`${key} must be a ${whole ? 'whole number' : 'number'} or absent`);
  return value;
}
function sequence(stored: ObjectValue, key: string): unknown[] {
  const value = required(stored, key);
  if (!Array.isArray(value)) throw new Error(`${key} must be a list`);
  return value;
}
function choice<T extends string>(
  value: unknown,
  values: Readonly<Record<string, T>>,
  label: string,
): T {
  if (typeof value !== 'string' || !Object.values(values).includes(value as T))
    throw new Error(`${JSON.stringify(value)} is not a valid ${label}`);
  return value as T;
}
function principal(stored: ObjectValue, key: string): Principal | null {
  const value = stored[key];
  if (value === null || value === undefined) return null;
  const fields = object(value, `${key} must be a principal object or absent`);
  if (typeof fields.kind !== 'string' || typeof fields.id !== 'string')
    throw new Error(`${key} must contain text kind and id`);
  const kind = fields.kind === 'sprint_item' ? 'manager' : fields.kind;
  if (!['owner', 'chief', 'manager', 'goal', 'ticket'].includes(kind))
    throw new Error(`'${kind}' is not a valid PrincipalKind`);
  if (!fields.id || fields.id.trim() !== fields.id)
    throw new Error('principal id must be non-empty and must not contain outer whitespace');
  if ((kind === 'owner' || kind === 'chief') && fields.id !== kind)
    throw new Error(`${kind} principal id must be ${kind}`);
  return { kind: kind as Principal['kind'], id: fields.id };
}
function senderFields(stored: ObjectValue) {
  return {
    sender: principal(stored, 'sender'),
    recipient: principal(stored, 'recipient'),
    sender_message_id: optionalText(stored, 'sender_message_id'),
    sent_at_unix_milliseconds: optionalNumber(stored, 'sent_at_unix_milliseconds', true),
  };
}
function promptFields(stored: ObjectValue) {
  const references = stored.recovery_references ?? [];
  if (!Array.isArray(references)) throw new Error('recovery_references must be a list');
  return {
    content: message_content_from_stored(stored),
    sender_label: text(stored, 'sender_label'),
    ...senderFields(stored),
    recovery_references: references.map((value) => {
      const reference = object(value, 'recovery reference must be an object');
      return new DeploymentRecoveryReference({
        interruption_id: text(reference, 'interruption_id'),
        expected_stage: optionalText(reference, 'expected_stage'),
      });
    }),
  };
}
function mode(stored: ObjectValue) {
  const value = text(stored, 'mode');
  return value === 'run_when_free'
    ? PromptDeliveryMode.queue
    : choice(value, PromptDeliveryMode, 'PromptDeliveryMode');
}
function record(value: unknown): ObjectValue {
  return object(value, 'entry must be an object');
}

/** Decodes the public metadata snapshot only. Secret storage and request rules belong to credentials. */
function credentialSnapshot(value: unknown): CredentialRequestRowBody {
  try {
    const row = record(value);
    const nullable = <T>(key: string, decode: (value: unknown) => T): T | null => {
      const value = required(row, key);
      return value === null ? null : decode(value);
    };
    const stringOrNull = (key: string) => {
      required(row, key);
      return optionalText(row, key);
    };
    const authKind = (value: unknown) =>
      choice(value, { bearer: 'bearer', header: 'header' } as const, 'auth_kind');
    // Pydantic's integer fields accept integral numbers and numeric strings.
    const revision = (fields: ObjectValue) => {
      const value = required(fields, 'revision');
      if (typeof value === 'string' && !/^[+-]?\d+(?:\.0+)?$/.test(value.trim()))
        throw new Error('revision');
      const number =
        typeof value === 'boolean'
          ? Number(value)
          : typeof value === 'string'
            ? Number(value)
            : value;
      if (typeof number !== 'number' || !Number.isInteger(number)) throw new Error('revision');
      return number;
    };
    return {
      kind: choice(
        required(row, 'kind'),
        { credential_request: 'credential_request' } as const,
        'kind',
      ),
      request_id: text(row, 'request_id'),
      name: text(row, 'name'),
      provider: text(row, 'provider'),
      use: text(row, 'use'),
      auth_kind: authKind(required(row, 'auth_kind')),
      header_name: stringOrNull('header_name'),
      help: stringOrNull('help'),
      help_url: stringOrNull('help_url'),
      replacement: nullable('replacement', (value) => {
        const fields = record(value);
        return {
          credential_id: text(fields, 'credential_id'),
          name: text(fields, 'name'),
          revision: revision(fields),
        };
      }),
      state: choice(
        required(row, 'state'),
        {
          pending: 'pending',
          saved: 'saved',
          replaced: 'replaced',
          rejected: 'rejected',
          reused: 'reused',
        } as const,
        'state',
      ),
      credential: nullable('credential', (value) => {
        const fields = record(value),
          reference = record(required(fields, 'reference'));
        return {
          id: text(fields, 'id'),
          reference: {
            scheme: choice(required(reference, 'scheme'), { local: 'local' } as const, 'scheme'),
            name: text(reference, 'name'),
          },
          auth_kind: authKind(required(fields, 'auth_kind')),
          revision: revision(fields),
        };
      }),
      delivery: nullable('delivery', (value) => {
        const fields = record(value);
        required(fields, 'refusal_reason');
        return {
          attempt_id: text(fields, 'attempt_id'),
          status: choice(
            required(fields, 'status'),
            {
              pending: 'pending',
              started: 'started',
              queued: 'queued',
              injected: 'injected',
              refused: 'refused',
              uncertain: 'uncertain',
              canceled: 'canceled',
            } as const,
            'status',
          ),
          refusal_reason: optionalText(fields, 'refusal_reason'),
        };
      }),
    };
  } catch {
    throw new Error('invalid credential request snapshot');
  }
}

export function conversation_event_payload_from_canonical_json(
  kind: E.ConversationEventKind,
  payload_json: string,
): E.ConversationEventPayload {
  const stored = object(JSON.parse(payload_json), `${kind} payload is not a JSON object`);
  switch (kind) {
    case 'prompt':
      return new E.PromptEventPayload({
        ...promptFields(stored),
        mode: mode(stored),
        reconciles_sequence: optionalNumber(stored, 'reconciles_sequence', true),
        system_generated:
          'system_generated' in stored ? boolean(stored, 'system_generated') : false,
      });
    case 'prompt_delivery_refused':
      return new E.PromptDeliveryRefusedEventPayload({
        ...promptFields(stored),
        mode: mode(stored),
        refusal_reason: choice(
          text(stored, 'refusal_reason'),
          PromptDeliveryRefusalReason,
          'PromptDeliveryRefusalReason',
        ),
        refusal_detail: optionalText(stored, 'refusal_detail'),
      });
    case 'prompt_delivery_uncertain':
      return new E.PromptDeliveryUncertainEventPayload({
        ...promptFields(stored),
        mode: mode(stored),
      });
    case 'prompt_discarded':
      return new E.PromptDiscardedEventPayload({
        ...promptFields(stored),
        suppression_reason: optionalText(stored, 'suppression_reason'),
      });
    case 'message_to_owner': {
      const fields = senderFields(stored);
      if (!fields.sender || !fields.recipient)
        throw new Error('message_to_owner requires sender and recipient');
      return new E.MessageToOwnerEventPayload({
        ...fields,
        sender: fields.sender,
        recipient: fields.recipient,
        content: message_content_from_stored(stored),
        sender_label: text(stored, 'sender_label'),
      });
    }
    case 'agent_message':
      return new E.AgentMessageEventPayload({ content: message_content_from_stored(stored) });
    case 'explicit_reply_missing': {
      const prompt_sender = principal(stored, 'prompt_sender');
      if (!prompt_sender) throw new Error('explicit_reply_missing requires prompt_sender');
      return new E.RetiredExplicitReplyMissingEventPayload({ prompt_sender });
    }
    case 'tool_call_started':
      return new E.ToolCallStartedEventPayload({
        tool_call_id: text(stored, 'tool_call_id'),
        title: text(stored, 'title'),
        tool_kind: text(stored, 'tool_kind'),
        detail: optionalText(stored, 'detail'),
      });
    case 'tool_call_finished':
      return new E.ToolCallFinishedEventPayload({
        tool_call_id: text(stored, 'tool_call_id'),
        tool_call_status: choice(
          text(stored, 'tool_call_status'),
          E.ToolCallStatus,
          'ToolCallStatus',
        ),
        detail: optionalText(stored, 'detail'),
      });
    case 'permission_asked':
      return new E.PermissionAskedEventPayload({
        ask_id: text(stored, 'ask_id'),
        title: text(stored, 'title'),
        detail: optionalText(stored, 'detail'),
        options: sequence(stored, 'options').map((value) => {
          const fields = record(value);
          return new E.PermissionAskOption({
            option_id: text(fields, 'option_id'),
            label: text(fields, 'label'),
            option_kind: text(fields, 'option_kind'),
          });
        }),
      });
    case 'permission_answered':
      return new E.PermissionAnsweredEventPayload({
        ask_id: text(stored, 'ask_id'),
        option_id: text(stored, 'option_id'),
        fate: choice(stored.fate === undefined ? 'applied' : stored.fate, AnswerFate, 'AnswerFate'),
        answer_id: stored.answer_id as string | null | undefined,
      });
    case 'answer_applied':
      return new E.AnswerAppliedEventPayload({ answer_id: text(stored, 'answer_id') });
    case 'user_input_requested':
      return new E.UserInputRequestedEventPayload({
        request_id: text(stored, 'request_id'),
        questions: sequence(stored, 'questions').map((value) => {
          const fields = record(value);
          return new E.UserInputQuestion({
            question_id: text(fields, 'question_id'),
            header: text(fields, 'header'),
            question: text(fields, 'question'),
            multi_select: boolean(fields, 'multi_select'),
            allow_other: boolean(fields, 'allow_other'),
            options: sequence(fields, 'options').map((value) => {
              const option = record(value);
              return new E.UserInputOption({
                label: text(option, 'label'),
                description: text(option, 'description'),
              });
            }),
          });
        }),
      });
    case 'user_input_answered': {
      const answers = object(required(stored, 'answers'), 'answers must be an object');
      return new E.UserInputAnsweredEventPayload({
        request_id: text(stored, 'request_id'),
        fate: choice(stored.fate === undefined ? 'applied' : stored.fate, AnswerFate, 'AnswerFate'),
        answer_id: stored.answer_id as string | null | undefined,
        answers: Object.entries(answers)
          .filter(
            ([, value]) => value !== null && typeof value === 'object' && !Array.isArray(value),
          )
          .map(([question_id, value]) => {
            const entries = required(record(value), 'answers');
            if (!Array.isArray(entries) || !entries.every((entry) => typeof entry === 'string'))
              throw new Error('answers must be a list of text');
            return new E.UserInputAnswer({ question_id, answers: entries });
          }),
      });
    }
    case 'credential_request':
      return new E.CredentialRequestEventPayload({
        request: credentialSnapshot(required(stored, 'request')),
      });
    case 'user_input_failed':
      return new E.UserInputFailedEventPayload({
        request_id: text(stored, 'request_id'),
        detail: text(stored, 'detail'),
      });
    case 'plan_updated':
      return new E.PlanUpdatedEventPayload({
        entries: sequence(stored, 'entries').map((value) => {
          const fields = record(value);
          return new E.PlanEntry({
            text: text(fields, 'text'),
            status: choice(text(fields, 'status'), E.PlanEntryStatus, 'PlanEntryStatus'),
          });
        }),
      });
    case 'model_changed':
      return new E.ModelChangedEventPayload({
        model: text(stored, 'model'),
        reasoning_effort: optionalText(stored, 'reasoning_effort'),
      });
    case 'token_usage':
      return new E.TokenUsageEventPayload({
        input_tokens: optionalNumber(stored, 'input_tokens', true),
        output_tokens: optionalNumber(stored, 'output_tokens', true),
        cached_input_tokens: optionalNumber(stored, 'cached_input_tokens', true),
        cost_usd: optionalNumber(stored, 'cost_usd', false),
      });
    case 'context_compacted':
      return new E.ContextCompactedEventPayload();
    case 'command_reply':
      return new E.CommandReplyEventPayload({ reply_text: text(stored, 'reply_text') });
    case 'turn_ended': {
      const result = optionalText(stored, 'automatic_compaction_result');
      return new E.TurnEndedEventPayload({
        ending: choice(text(stored, 'ending'), E.ConversationTurnEnding, 'ConversationTurnEnding'),
        error_summary: optionalText(stored, 'error_summary'),
        automatic_compaction_result:
          result === null
            ? null
            : choice(result, E.AutomaticCompactionResult, 'AutomaticCompactionResult'),
      });
    }
  }
}

function minted(key: string, value: unknown): ObjectValue {
  return value === null ? {} : { [key]: value };
}
function principals(payload: {
  sender: Principal | null;
  recipient: Principal | null;
}): ObjectValue {
  return { ...minted('sender', payload.sender), ...minted('recipient', payload.recipient) };
}
function senderEntries(payload: {
  sender_message_id: string | null;
  sent_at_unix_milliseconds: number | null;
}): ObjectValue {
  return {
    ...minted('sender_message_id', payload.sender_message_id),
    ...minted('sent_at_unix_milliseconds', payload.sent_at_unix_milliseconds),
  };
}
export function conversation_event_payload_json_object(
  payload: E.ConversationEventPayload,
): ObjectValue {
  switch (payload.kind) {
    case 'credential_request':
      return { request: payload.request };
    case 'prompt':
    case 'prompt_delivery_refused':
    case 'prompt_delivery_uncertain':
    case 'prompt_discarded':
      return {
        ...message_content_json_entries(payload.content),
        sender_label: payload.sender_label,
        ...senderEntries(payload),
        ...principals(payload),
        ...(payload.recovery_references.length
          ? {
              recovery_references: payload.recovery_references.map((reference) => ({
                interruption_id: reference.interruption_id,
                expected_stage: reference.expected_stage,
              })),
            }
          : {}),
        ...(payload.kind === 'prompt_discarded'
          ? minted('suppression_reason', payload.suppression_reason)
          : { mode: payload.mode }),
        ...(payload.kind === 'prompt'
          ? {
              ...minted('reconciles_sequence', payload.reconciles_sequence),
              ...(payload.system_generated ? { system_generated: true } : {}),
            }
          : {}),
        ...(payload.kind === 'prompt_delivery_refused'
          ? { refusal_reason: payload.refusal_reason, refusal_detail: payload.refusal_detail }
          : {}),
      };
    case 'message_to_owner':
      return {
        ...message_content_json_entries(payload.content),
        sender_label: payload.sender_label,
        ...senderEntries(payload),
        ...principals(payload),
      };
    case 'agent_message':
      return message_content_json_entries(payload.content);
    case 'explicit_reply_missing':
      return { prompt_sender: payload.prompt_sender };
    case 'tool_call_started':
      return {
        tool_call_id: payload.tool_call_id,
        title: payload.title,
        tool_kind: payload.tool_kind,
        detail: payload.detail,
      };
    case 'tool_call_finished':
      return {
        tool_call_id: payload.tool_call_id,
        tool_call_status: payload.tool_call_status,
        detail: payload.detail,
      };
    case 'permission_asked':
      return {
        ask_id: payload.ask_id,
        title: payload.title,
        detail: payload.detail,
        options: payload.options.map((option) => ({
          option_id: option.option_id,
          label: option.label,
          option_kind: option.option_kind,
        })),
      };
    case 'permission_answered':
      return {
        ask_id: payload.ask_id,
        option_id: payload.option_id,
        ...(payload.answer_id === null ? {} : { fate: payload.fate, answer_id: payload.answer_id }),
      };
    case 'answer_applied':
      return { answer_id: payload.answer_id };
    case 'user_input_requested':
      return {
        request_id: payload.request_id,
        questions: payload.questions.map((question) => ({
          question_id: question.question_id,
          header: question.header,
          question: question.question,
          options: question.options.map((option) => ({
            label: option.label,
            description: option.description,
          })),
          multi_select: question.multi_select,
          allow_other: question.allow_other,
        })),
      };
    case 'user_input_answered':
      return {
        request_id: payload.request_id,
        ...(payload.answer_id === null ? {} : { fate: payload.fate, answer_id: payload.answer_id }),
        answers: Object.fromEntries(
          payload.answers.map((answer) => [answer.question_id, { answers: answer.answers }]),
        ),
      };
    case 'user_input_failed':
      return { request_id: payload.request_id, detail: payload.detail };
    case 'plan_updated':
      return {
        entries: payload.entries.map((entry) => ({ text: entry.text, status: entry.status })),
      };
    case 'model_changed':
      return { model: payload.model, reasoning_effort: payload.reasoning_effort };
    case 'token_usage':
      return {
        ...minted('input_tokens', payload.input_tokens),
        ...minted('output_tokens', payload.output_tokens),
        ...minted('cached_input_tokens', payload.cached_input_tokens),
        ...minted('cost_usd', payload.cost_usd),
      };
    case 'context_compacted':
      return {};
    case 'command_reply':
      return { reply_text: payload.reply_text };
    case 'turn_ended':
      return {
        ending: payload.ending,
        error_summary: payload.error_summary,
        ...minted('automatic_compaction_result', payload.automatic_compaction_result),
      };
  }
}

function codePointOrder(a: string, b: string): number {
  const left = Array.from(a, (character) => character.codePointAt(0)!),
    right = Array.from(b, (character) => character.codePointAt(0)!);
  for (let i = 0; i < Math.min(left.length, right.length); i++)
    if (left[i] !== right[i]) return left[i] - right[i];
  return left.length - right.length;
}
/** Python float repr differs from JSON.stringify at integral values and exponent thresholds. */
function pythonFloat(value: number): string {
  if (!Number.isFinite(value)) throw new Error('non-finite event number');
  if (Object.is(value, -0)) return '-0.0';
  const magnitude = Math.abs(value);
  let raw =
    magnitude !== 0 && (magnitude < 1e-4 || magnitude >= 1e16)
      ? value.toExponential()
      : String(value);
  if (raw.includes('e'))
    raw = raw.replace(
      /e([+-]?)(\d+)$/,
      (_, sign: string, digits: string) => 'e' + (sign || '+') + digits.padStart(2, '0'),
    );
  else if (!raw.includes('.')) raw += '.0';
  return raw;
}
/** Sorted keys are emitted directly, so numeric-looking keys retain Python lexical order. */
export function canonical_json(value: unknown, field?: string): string {
  if (value === null) return 'null';
  if (typeof value === 'number') {
    if (field === 'cost_usd') return pythonFloat(value);
    if (!Number.isFinite(value)) throw new Error('non-finite event number');
    return JSON.stringify(value);
  }
  if (typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map((item) => canonical_json(item)).join(',') + ']';
  const fields = object(value, 'event JSON value is not serializable');
  return (
    '{' +
    Object.keys(fields)
      .sort(codePointOrder)
      .map((key) => JSON.stringify(key) + ':' + canonical_json(fields[key], key))
      .join(',') +
    '}'
  );
}
export function conversation_event_payload_to_canonical_json(
  payload: E.ConversationEventPayload,
): string {
  return canonical_json(conversation_event_payload_json_object(payload));
}
