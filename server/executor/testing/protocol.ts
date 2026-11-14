/** One pure proof of native wire meanings, schema admission, and frame custody. */
import assert from 'node:assert/strict';
import {
  ExecutorValidationError,
  as_object,
  decode_bounded,
  decode_json,
  decode_value,
  encode_backend_event,
  decode_event_arguments,
  encode_bounded,
  encode_frame,
  read_typed_frame,
} from '../protocol.ts';
import { WIRE_SCHEMA } from '../wire.generated.ts';
import type { WireRootName } from '../wire.generated.ts';
import { native_wire_fixtures } from './native_protocol_fixtures.ts';

const native = native_wire_fixtures();
assert.deepEqual(Object.keys(native).sort(), [...WIRE_SCHEMA.roots].sort());
const roots: string[] = [];
for (const root of Object.keys(native) as WireRootName[]) {
  const body = encode_bounded(native[root], root);
  const document = JSON.parse(Buffer.from(body).toString());
  const decoded = decode_bounded(body, root);
  assert.deepEqual(decoded, document, root);
  assert.deepEqual(
    JSON.parse(Buffer.from(encode_bounded(decoded, root)).toString()),
    document,
    root,
  );
  roots.push(root);
}
assert.deepEqual(as_object(native.JsonObject, 'JsonObject'), native.JsonObject);

const refused: string[] = [];
function invalid(name: string, work: () => unknown): void {
  assert.throws(work, ExecutorValidationError, name);
  refused.push(name);
}
invalid('malformed-json', () => decode_json('{', 'IpcResponse'));
invalid('missing-required-field', () => decode_value({ turn_number: 1 }, 'TurnToken'));
invalid('missing-nullable-field', () =>
  decode_value({ request_id: 'r', result: null }, 'IpcResponse'),
);
assert.deepEqual(decode_value({ request_id: 'r', result: null, error: null }, 'IpcResponse'), {
  request_id: 'r',
  result: null,
  error: null,
  unconfirmed: null,
});
invalid('unknown-enum', () =>
  decode_value({ ...native.CommandOutcome, disposition: 'unknown' }, 'CommandOutcome'),
);
invalid('no-scalar-coercion', () =>
  decode_value({ conversation_id: 'c', turn_number: '2' }, 'TurnToken'),
);
for (const value of [Number.MAX_SAFE_INTEGER, Number.MIN_SAFE_INTEGER]) {
  const token = { conversation_id: 'c', turn_number: value };
  assert.equal(decode_value(token, 'TurnToken').turn_number, value);
}
for (const value of [Number.MAX_SAFE_INTEGER + 1, Number.MIN_SAFE_INTEGER - 1]) {
  const token = { conversation_id: 'c', turn_number: value };
  invalid(`unsafe-integer-input-${value}`, () => decode_json(JSON.stringify(token), 'TurnToken'));
  invalid(`unsafe-integer-output-${value}`, () => encode_bounded(token, 'TurnToken'));
}
invalid('nonfinite-json-token', () => decode_json('{"value":NaN}', 'JsonObject'));
invalid('nonfinite-json-exponent', () => decode_json('{"value":1e999}', 'JsonObject'));
invalid('nonfinite-structured-extra', () => decode_json('{"conversation_id":"c","turn_number":1,"extra":1e999}', 'TurnToken'));
for (const value of [NaN, Infinity, -Infinity])
  invalid(`nonfinite-output-${String(value)}`, () =>
    encode_bounded({ values: [value] }, 'JsonObject'),
  );
invalid('command-kind-conflict', () =>
  decode_value({ ...native.ChildCommand, operation: 'stop' }, 'ChildCommand'),
);
invalid('command-needs-turn', () =>
  decode_value({ ...native.ChildCommand, turn: null }, 'ChildCommand'),
);
invalid('mailbox-payload-conflict', () =>
  decode_value(
    { ...native.MailboxCommand, model_list_request: native.ModelListRequest },
    'MailboxCommand',
  ),
);
invalid('mailbox-command-id-conflict', () =>
  decode_value({ ...native.MailboxCommand, command_id: 'other' }, 'MailboxCommand'),
);
invalid('body-budget', () => decode_bounded(Buffer.from('{}'), 'JsonObject', { limit: 1 }));
invalid('output-budget', () => encode_bounded(native.IpcResponse, 'IpcResponse', { limit: 1 }));

const frame = Buffer.from(encode_frame(native.IpcResponse, 'IpcResponse'));
assert.equal(frame.readUInt32BE(), frame.length - 4);
let offset = 0;
const decoded = await read_typed_frame(
  {
    async readexactly(length: number) {
      const bytes = frame.subarray(offset, offset + length);
      offset += length;
      assert.equal(bytes.length, length);
      return bytes;
    },
  },
  'IpcResponse',
);
assert.deepEqual(decoded, JSON.parse(frame.subarray(4).toString()));
const event = decode_event_arguments({
  method: 'agent_message_delta',
  arguments: { text_delta: 'native λ' },
});
const [method, args] = encode_backend_event(event);
assert.equal(method, 'agent_message_delta');
assert.deepEqual(args, { text_delta: 'native λ' });
process.stdout.write(JSON.stringify({ roots, refused, framed: true, events: true }));
