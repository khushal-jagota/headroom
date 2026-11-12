/** The Python executor's closed backend-event registry, with existing core classes. */
import * as Backend from '../conversation/backends/contracts.ts';
import { ComposerCatalogEntry } from '../conversation/contracts.ts';
import {
  PermissionAskOption,
  PlanEntry,
  UserInputOption,
  UserInputQuestion,
} from '../conversation/events.ts';
import { MessageFile, MessageImage, MessageText } from '../conversation/message_content.ts';
import { JsonNumber } from '../core/http/numeric_json.ts';
import type { NumericJSON } from '../core/http/numeric_json.ts';
import type {
  BackendEvent,
  BackendEventMethod,
  JsonObject,
  WireRootName,
} from './wire.generated.ts';
import { decode_value, encode_value } from './protocol_values.ts';
import { KeyError } from './protocol_errors.ts';

// Exact constructors, not constructor names or instanceof matching. Python uses type(event).
const event_types = {
  agent_message_delta: [Backend.AgentMessageDelta, 'AgentMessageDelta'],
  model_thinking_happened: [Backend.ModelThinkingHappened, 'ModelThinkingHappened'],
  agent_message_completed: [Backend.AgentMessageCompleted, 'AgentMessageCompleted'],
  command_reply_reported: [Backend.CommandReplyReported, 'CommandReplyReported'],
  tool_call_started: [Backend.ToolCallStarted, 'ToolCallStarted'],
  tool_call_progress: [Backend.ToolCallProgress, 'ToolCallProgress'],
  tool_call_finished: [Backend.ToolCallFinished, 'ToolCallFinished'],
  plan_updated: [Backend.PlanUpdated, 'PlanUpdated'],
  token_usage_reported: [Backend.TokenUsageReported, 'TokenUsageReported'],
  context_compacted: [Backend.ContextCompacted, 'ContextCompacted'],
  permission_ask_raised: [Backend.PermissionAskRaised, 'PermissionAskRaised'],
  user_input_requested: [Backend.UserInputRequested, 'UserInputRequested'],
  user_input_failed: [Backend.UserInputFailed, 'UserInputFailed'],
  turn_ended: [Backend.TurnEnded, 'TurnEnded'],
  vendor_session_cursor_rebound: [Backend.VendorSessionCursorRebound, 'VendorSessionCursorRebound'],
  composer_catalog_reported: [Backend.ComposerCatalogReported, 'ComposerCatalogReported'],
  background_task_started: [Backend.BackgroundTaskStarted, 'BackgroundTaskStarted'],
  background_task_ended: [Backend.BackgroundTaskEnded, 'BackgroundTaskEnded'],
  backend_stream_ended: [Backend.BackendStreamEnded, 'BackendStreamEnded'],
} as const satisfies Record<BackendEventMethod, readonly [Function, WireRootName]>;

/** Actual core classes, with exact markers retained only where their number view loses precision. */
export type DecodedBackendEvent = NumericJSON<Backend.BackendEventValue>;
function construct(constructor: Function, fields: unknown): object {
  // A finite float already denotes a native double. Safe integers also project exactly.
  // Other integer markers retain custody, under the truthful NumericJSON result type.
  const values = Object.fromEntries(
    Object.entries(fields as Record<string, unknown>).map(([key, value]) => {
      if (value instanceof JsonNumber) {
        if (value.kind === 'float' && Number.isFinite(value.value)) return [key, value.value];
        if (value.kind === 'integer' && Number.isSafeInteger(value.value))
          return [key, Number(value.integer)];
      }
      return [key, value];
    }),
  );
  return Reflect.construct(constructor, [values]);
}
function fields(value: unknown): Record<string, unknown> {
  return value as Record<string, unknown>; // Input already passed the selected Python-value decoder.
}
function objects(value: unknown, convert: (value: unknown) => object): object[] {
  return (value as unknown[]).map(convert);
}
function message_piece(value: unknown): object {
  const piece = fields(value);
  // These dictionaries are normalized union winners, not the original ambiguous raw input.
  return construct(
    Object.hasOwn(piece, 'text')
      ? MessageText
      : Object.hasOwn(piece, 'byte_count')
        ? MessageFile
        : MessageImage,
    piece,
  );
}
function permission_ask(value: unknown): object {
  const ask = fields(value);
  return construct(Backend.BackendPermissionAsk, {
    ...ask,
    options: objects(ask.options, (option) => construct(PermissionAskOption, option)),
  });
}
function user_input(value: unknown): object {
  const request = fields(value);
  return construct(Backend.BackendUserInputRequest, {
    ...request,
    questions: objects(request.questions, (value) => {
      const question = fields(value);
      return construct(UserInputQuestion, {
        ...question,
        options: objects(question.options, (option) => construct(UserInputOption, option)),
      });
    }),
  });
}

export function encode_backend_event(
  event: Backend.BackendEventValue | DecodedBackendEvent,
): readonly [BackendEventMethod, NumericJSON<JsonObject>] {
  const actual = Object.getPrototypeOf(event)?.constructor;
  const entry = Object.entries(event_types).find(([, [constructor]]) => actual === constructor);
  if (!entry) throw new KeyError(actual);
  const [method, [, root]] = entry;
  return [method as BackendEventMethod, encode_value(event, root) as NumericJSON<JsonObject>];
}

export function decode_event_arguments(
  event: Pick<NumericJSON<BackendEvent>, 'method' | 'arguments'>,
): DecodedBackendEvent {
  if (!Object.hasOwn(event_types, event.method)) throw new KeyError(event.method);
  const [constructor, root] = event_types[event.method];
  const value = fields(decode_value(event.arguments, root));
  let converted = value;
  switch (event.method) {
    case 'agent_message_completed':
      converted = { ...value, content: objects(value.content, message_piece) };
      break;
    case 'plan_updated':
      converted = {
        ...value,
        entries: objects(value.entries, (entry) => construct(PlanEntry, entry)),
      };
      break;
    case 'permission_ask_raised':
      converted = { ...value, ask: permission_ask(value.ask) };
      break;
    case 'user_input_requested':
      converted = { ...value, request: user_input(value.request) };
      break;
    case 'composer_catalog_reported':
      converted = {
        ...value,
        composer_catalog: objects(value.composer_catalog, (entry) =>
          construct(ComposerCatalogEntry, entry),
        ),
      };
      break;
  }
  return construct(constructor, converted) as DecodedBackendEvent;
}
