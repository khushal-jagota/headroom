/** The executor's closed backend-event registry and native core constructors. */
import type * as Backend from '../conversation/backends/contracts.ts';
import { WIRE_SCHEMA } from './wire.generated.ts';
import type { MessageContent } from '../conversation/message_content.ts';
import type {
  BackendEvent,
  BackendEventMethod,
  JsonObject,
  WireRootName,
} from './wire.generated.ts';
import { decode_value, encode_value, matches_schema } from './protocol_values.ts';
import { ExecutorValidationError } from './protocol_errors.ts';
import { native_class_name, wire_classes } from './protocol_classes.ts';

// Match actual core constructors without relying on JavaScript constructor names.
const event_types = WIRE_SCHEMA.event_codecs as Record<
  BackendEventMethod,
  WireRootName & keyof typeof wire_classes
>;
const event_methods = new Map<string, BackendEventMethod>(
  Object.entries(event_types).map(([method, root]) => [root, method as BackendEventMethod]),
);

/** Incoming event arguments become the existing native core classes. */
export type DecodedBackendEvent = Backend.BackendEventValue;
function construct(constructor: Function, fields: unknown): object {
  return Reflect.construct(constructor, [fields]);
}
function fields(value: unknown): Record<string, unknown> {
  return value as Record<string, unknown>; // Input already passed the selected wire schema.
}
function objects(value: unknown, convert: (value: unknown) => object): object[] {
  return (value as unknown[]).map(convert);
}
function message_piece(value: unknown): object {
  for (const root of ['MessageFile', 'MessageImage', 'MessageText'] as const)
    if (matches_schema(value, root)) return construct(wire_classes[root], value);
  throw new ExecutorValidationError('Invalid message piece.');
}
/** Input already passed its message schema. */
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
  if (method === undefined)
    throw new ExecutorValidationError(`Unsupported backend event: ${String(actual)}`);
  return [method, encode_value(event, event_types[method]) as JsonObject];
}

export function decode_event_arguments(
  event: Pick<BackendEvent, 'method' | 'arguments'>,
): DecodedBackendEvent {
  if (!Object.hasOwn(event_types, event.method))
    throw new ExecutorValidationError(`Unknown backend event method: ${event.method}`);
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
