/** The Python executor's closed backend-event registry, with existing core classes. */
import type * as Backend from '../conversation/backends/contracts.ts';
import type { MessageContent } from '../conversation/message_content.ts';
import type {
  BackendEvent,
  BackendEventMethod,
  JsonObject,
  WireRootName,
  WireTypes,
} from './wire.generated.ts';
import { decode_value, encode_value } from './protocol_values.ts';
import { KeyError, class_name } from './protocol_errors.ts';
import { native_class_name, wire_classes } from './protocol_classes.ts';

// Exact constructors, not constructor names or instanceof matching. Python uses type(event).
const event_types = {
  agent_message_delta: 'AgentMessageDelta',
  model_thinking_happened: 'ModelThinkingHappened',
  agent_message_completed: 'AgentMessageCompleted',
  command_reply_reported: 'CommandReplyReported',
  tool_call_started: 'ToolCallStarted',
  tool_call_progress: 'ToolCallProgress',
  tool_call_finished: 'ToolCallFinished',
  plan_updated: 'PlanUpdated',
  token_usage_reported: 'TokenUsageReported',
  context_compacted: 'ContextCompacted',
  permission_ask_raised: 'PermissionAskRaised',
  user_input_requested: 'UserInputRequested',
  user_input_failed: 'UserInputFailed',
  turn_ended: 'TurnEnded',
  vendor_session_cursor_rebound: 'VendorSessionCursorRebound',
  composer_catalog_reported: 'ComposerCatalogReported',
  background_task_started: 'BackgroundTaskStarted',
  background_task_ended: 'BackgroundTaskEnded',
  backend_stream_ended: 'BackendStreamEnded',
} as const satisfies Record<BackendEventMethod, WireRootName>;
const event_methods = new Map<string, BackendEventMethod>(
  Object.entries(event_types).map(([method, root]) => [root, method as BackendEventMethod]),
);

/** Actual core classes, with exact markers retained only where their number view loses precision. */
export type DecodedBackendEvent = WireTypes[(typeof event_types)[BackendEventMethod]];
function construct(constructor: Function, fields: unknown): object {
  return Reflect.construct(constructor, [fields]);
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
    wire_classes[class_name(value) as 'MessageText' | 'MessageFile' | 'MessageImage'],
    piece,
  );
}
/** Input already passed its selected wire root. Preserve that root's union winners and exact markers. */
export function native_message_content(value: unknown): MessageContent {
  return objects(value, message_piece) as MessageContent;
}
function permission_ask(value: unknown): object {
  const ask = fields(value);
  return construct(wire_classes.BackendPermissionAsk, {
    ...ask,
    options: objects(ask.options, (option) => construct(wire_classes.PermissionAskOption, option)),
  });
}
function user_input(value: unknown): object {
  const request = fields(value);
  return construct(wire_classes.BackendUserInputRequest, {
    ...request,
    questions: objects(request.questions, (value) => {
      const question = fields(value);
      return construct(wire_classes.UserInputQuestion, {
        ...question,
        options: objects(question.options, (option) =>
          construct(wire_classes.UserInputOption, option),
        ),
      });
    }),
  });
}

export function encode_backend_event(
  event: Backend.BackendEventValue | DecodedBackendEvent,
): readonly [BackendEventMethod, JsonObject] {
  const actual = Object.getPrototypeOf(event)?.constructor;
  const root_name = native_class_name(event);
  const method = root_name === undefined ? undefined : event_methods.get(root_name);
  if (method === undefined) throw new KeyError(actual);
  return [method, encode_value(event, event_types[method]) as JsonObject];
}

export function decode_event_arguments(
  event: Pick<BackendEvent, 'method' | 'arguments'>,
): DecodedBackendEvent {
  if (!Object.hasOwn(event_types, event.method)) throw new KeyError(event.method);
  const root = event_types[event.method];
  const constructor = wire_classes[root];
  const value = fields(decode_value(event.arguments, root));
  let converted = value;
  switch (event.method) {
    case 'agent_message_completed':
      converted = { ...value, content: native_message_content(value.content) };
      break;
    case 'plan_updated':
      converted = {
        ...value,
        entries: objects(value.entries, (entry) => construct(wire_classes.PlanEntry, entry)),
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
          construct(wire_classes.ComposerCatalogEntry, entry),
        ),
      };
      break;
  }
  return construct(constructor, converted) as DecodedBackendEvent;
}
