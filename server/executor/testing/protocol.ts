/** One pure cross-language exchange, with independent native TS senders. */
import { readFileSync } from 'node:fs';
import * as backend from '../../conversation/backends/contracts.ts';
import {
  MessageText,
  MessageImage,
  MessageFile,
  require_message_content,
} from '../../conversation/message_content.ts';
import {
  JsonNumber,
  numeric_value,
  stringify_pydantic_json,
  stringify_python_json,
} from '../../core/http/numeric_json.ts';
import {
  parse_identity,
  validate_identity,
  supports_executor_versions,
  refusal_reason_for_versions,
} from '../../execution_identity.ts';
import {
  ValueError,
  as_object,
  decode_bounded,
  decode_event_arguments,
  decode_json,
  decode_value,
  encode_backend_event,
  encode_bounded,
  encode_frame,
  encode_value,
  integer_value,
  read_typed_frame,
} from '../protocol.ts';
import { parse_executor_json } from '../protocol_json.ts';
import type { JsonValue, WireRootName, WireTypes } from '../wire.generated.ts';
import { native_wire_fixtures } from './native_protocol_fixtures.ts';

type Fixture = { root: WireRootName; python: string };
type Case = {
  name: string;
  root: WireRootName;
  dto: unknown;
  body: string;
  strict?: boolean;
  limit?: unknown;
  operation?: string;
};
type IdentityCase = {
  name: string;
  operation: string;
  value?: unknown;
  versions?: [unknown, unknown];
  check_versions?: boolean;
};
const spec = parse_executor_json(readFileSync(0)) as unknown as {
  fixtures: Fixture[];
  cases: Case[];
  events: Record<string, string>;
  identities: IdentityCase[];
};
const native = native_wire_fixtures();
const bytes = (body: Uint8Array): string => Buffer.from(body).toString('base64');
const read = <K extends WireRootName>(root: K): WireTypes[K] =>
  decode_bounded(
    Buffer.from(spec.fixtures.find((fixture) => fixture.root === root)!.python, 'base64'),
    root,
  );
const fixtures: Record<string, unknown> = {};
for (const fixture of spec.fixtures) {
  const decoded = read(fixture.root);
  fixtures[fixture.root] = {
    read: bytes(encode_bounded(decoded, fixture.root)),
    write: bytes(encode_bounded(native[fixture.root], fixture.root)),
    value_bytes: stringify_pydantic_json(encode_value(decoded, fixture.root)),
  };
}
function failure(name: string, error: unknown): Record<string, unknown> {
  if (!(error instanceof Error)) throw error;
  return {
    name,
    ok: false,
    type: error.name,
    message: error.message,
    value_error: error instanceof ValueError,
  };
}
// Deliberately malformed incoming sender values can cross only this test boundary.
function invalid_sender(value: unknown): WireTypes[WireRootName] {
  return value as WireTypes[WireRootName];
}
const cases = [];
for (const entry of spec.cases) {
  const started = performance.now();
  let result: Record<string, unknown>;
  try {
    const body = Buffer.from(entry.body, 'base64');
    if (entry.operation === 'native-frame') {
      result = {
        name: entry.name,
        ok: true,
        body: bytes(encode_frame(native.ChildCommand, 'ChildCommand')),
      };
      cases.push(result);
      continue;
    }
    const limit = entry.limit === undefined ? undefined : numeric_value(entry.limit);
    let value: WireTypes[WireRootName];
    if (entry.operation === 'frame') {
      let offset = 0;
      value = await read_typed_frame(
        {
          async readexactly(length: number): Promise<Uint8Array> {
            const chunk = body.subarray(offset, offset + length);
            offset += length;
            if (chunk.byteLength !== length) throw new Error('fixture frame ended early');
            return chunk;
          },
        },
        entry.root,
      );
    } else if (entry.operation === 'value') value = decode_value(entry.dto, entry.root);
    else if (entry.operation === 'encode') value = invalid_sender(entry.dto);
    else if (entry.operation === 'native-default')
      value = { ...native.ExecutorIdentity, connection_generation: undefined };
    else if (entry.operation === 'native-surrogate')
      value = { ...native.CanonicalSkillSource, source_text: '\ud800' };
    else if (entry.operation === 'native-int-overflow')
      value = { conversation_id: 'conversation', oldest_sequence: 1e19 };
    else if (entry.operation === 'native-exact-int-overflow')
      value = {
        conversation_id: 'conversation',
        oldest_sequence: new JsonNumber('1' + '0'.repeat(4300)),
      };
    else if (entry.operation === 'native-deep-output') {
      let nested: JsonValue = null;
      for (let depth = 0; depth < 201; depth++) nested = [nested];
      value = { nested };
    } else if (entry.operation === 'json')
      value = decode_json(body.toString('utf8'), entry.root, { strict: entry.strict });
    else value = decode_bounded(body, entry.root, { strict: entry.strict, limit });
    result = {
      name: entry.name,
      ok: true,
      body: bytes(encode_bounded(value, entry.root, entry.operation === 'encode' ? { limit } : {})),
    };
  } catch (error) {
    result = failure(entry.name, error);
  }
  if (entry.name === 'long-space-number') result.elapsed_ms = performance.now() - started;
  cases.push(result);
}
const identities = spec.identities.map((entry) => {
  const started = performance.now();
  let result: Record<string, unknown>;
  try {
    let value: unknown;
    if (entry.operation === 'parse') value = parse_identity(entry.value);
    else if (entry.operation === 'validate') {
      validate_identity(entry.value as Parameters<typeof validate_identity>[0], {
        check_versions: entry.check_versions,
      });
      value = null;
    } else if (entry.operation === 'supports')
      value = supports_executor_versions(...entry.versions!);
    else
      value = refusal_reason_for_versions(
        ...(entry.versions! as Parameters<typeof refusal_reason_for_versions>),
      );
    result = { name: entry.name, ok: true, value };
  } catch (error) {
    result = failure(entry.name, error);
  }
  if (entry.name.startsWith('identity-long-')) result.elapsed_ms = performance.now() - started;
  return result;
});
const events: Record<string, unknown> = {};
const constructors = backend as unknown as Record<string, Function>;
const pieces = [
  ['MessageText', MessageText],
  ['MessageImage', MessageImage],
  ['MessageFile', MessageFile],
] as const;
const renamed = [
  ...Object.keys(spec.events).map((root) => constructors[root]!),
  ...pieces.map(([, ctor]) => ctor),
];
const descriptors = renamed.map((constructor) =>
  Object.getOwnPropertyDescriptor(constructor, 'name')!,
);
try {
  for (const constructor of renamed)
    Object.defineProperty(constructor, 'name', { value: 'RenamedWireClass', configurable: true });
  for (const [root, method] of Object.entries(spec.events)) {
    const event = decode_event_arguments({
      method,
      arguments: read(root as WireRootName),
    } as Parameters<typeof decode_event_arguments>[0]);
    const constructor = constructors[root]!;
    const [encoded_method, args] = encode_backend_event(event);
    events[root] = {
      instance: event instanceof constructor,
      exact_constructor: Object.getPrototypeOf(event).constructor === constructor,
      method: encoded_method,
      argument_bytes: stringify_pydantic_json(args),
      pieces:
        event instanceof backend.AgentMessageCompleted
          ? require_message_content(event.content).map(
              (piece) =>
                pieces.find(([, ctor]) => Object.getPrototypeOf(piece).constructor === ctor)![0],
            )
          : null,
    };
  }
} finally {
  renamed.forEach((constructor, index) =>
    Object.defineProperty(constructor, 'name', descriptors[index]!),
  );
}
const identity = read('ExecutorIdentity'),
  turn = read('TurnToken'),
  lease = read('LeaseReply');
validate_identity(identity);
const unsafe_case = spec.cases.find((entry) => entry.name === 'structured-unsafe-int')!;
const unsafe = decode_bounded(Buffer.from(unsafe_case.body, 'base64'), 'WaitingConversation');
// Exactly one realistic hot-path batch, independently built from native TS values.
const limit = 4 * 1024 * 1024;
const event_count = 256;
const batch: WireTypes['LiveEventBatch'] = {
  events: Array.from({ length: event_count }, () => ({
    ...native.LiveEventBatch.events[0]!,
    arguments: { text_delta: 'news' },
  })),
};
const initial = encode_bounded(batch, 'LiveEventBatch', { limit });
const padding = limit - initial.byteLength;
batch.events.forEach((event, index) => {
  const length = Math.floor(padding / event_count) + Number(index < padding % event_count);
  event.arguments = {
    text_delta: 'news' + 'x'.repeat(length),
  };
});
const large = encode_bounded(batch, 'LiveEventBatch', { limit });
const start = performance.now();
const decoded = decode_bounded(large, 'LiveEventBatch', { limit });
const decoded_at = performance.now();
const encoded = encode_bounded(decoded, 'LiveEventBatch', { limit });
const encoded_at = performance.now();
const performance_result = {
  bytes: large.byteLength,
  event_count,
  decode_ms: decoded_at - start,
  encode_ms: encoded_at - decoded_at,
  elapsed_ms: encoded_at - start,
  exact: Buffer.compare(large, encoded) === 0,
  body: bytes(encoded),
};
process.stdout.write(
  stringify_python_json(
    {
      fixtures,
      cases,
      identities,
      events,
      frame: bytes(encode_frame(native.IpcResponse, 'IpcResponse')),
      object_bytes: stringify_pydantic_json(as_object(read('JsonObject'), 'JsonObject')),
      numbers: {
        safe_integer: typeof turn.turn_number === 'number' && turn.turn_number === 3,
        float: typeof lease.away_after_seconds === 'number' && lease.away_after_seconds === 60,
        supports_decoded_identity: supports_executor_versions(
          identity.protocol_version,
          identity.spool_format_version,
        ),
        unsafe_exact: String(integer_value(unsafe.oldest_sequence)),
      },
      performance: performance_result,
    },
    { compact: true },
  ),
);

// Compile-only public sender contract. These functions are never invoked.
function type_contracts(value: unknown, lease: WireTypes['LeaseRequest']): void {
  // @ts-expect-error Unknown sender input cannot evade the generated root contract.
  encode_bounded(value, 'LeaseRequest');
  // @ts-expect-error The value serializer requires its root.
  encode_value(lease);
  // @ts-expect-error The object serializer requires its root.
  as_object(lease);
}
void type_contracts;
