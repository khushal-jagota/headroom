/** Shared executor codec facade. Both halves import their runtime wire operations here. */
import { JsonNumber, stringify_pydantic_json } from '../core/http/numeric_json.ts';
import type { WireRootName, WireTypes, JsonObject } from './wire.generated.ts';
import { ProtocolRefused } from '../execution_identity.ts';
import { MAX_ENVELOPE_BYTES } from './configuration.ts';
import {
  parse_executor_json,
  ExecutorJSONSyntaxError,
  ordered_json_entries,
} from './protocol_json.ts';
import { decode_json_value, encode_value, encode_typed_value } from './protocol_values.ts';
import type { WireInput } from './protocol_values.ts';
import { ValidationError, PydanticSerializationError, ValueError } from './protocol_errors.ts';
export { ProtocolRefused } from '../execution_identity.ts';
export {
  parse_identity,
  validate_identity,
  refusal_reason_for_versions,
  supports_executor_versions,
} from '../execution_identity.ts';
export {
  ValidationError,
  KeyError,
  PydanticSerializationError,
  ValueError,
} from './protocol_errors.ts';
export { decode_value, encode_value } from './protocol_values.ts';
export { integer_value } from './integers.ts';
export type { WireInput } from './protocol_values.ts';
export { encode_backend_event, decode_event_arguments } from './backend_events.ts';
export type { DecodedBackendEvent } from './backend_events.ts';
export type { WireRootName, WireTypes, WireInteger } from './wire.generated.ts';
const surrogate = /\p{Surrogate}/u;

export function decode_json<K extends WireRootName>(
  body: Uint8Array | string,
  root: K,
  options: { strict?: boolean } = {},
): WireTypes[K] {
  if (typeof body === 'string' && surrogate.test(body))
    throw new ValidationError(root, [
      {
        type: 'string_unicode',
        loc: [],
        message: 'Input should be a valid string, unable to parse raw data as a unicode string',
        input: body,
      },
    ]);
  let value: unknown;
  try {
    value = parse_executor_json(typeof body === 'string' ? Buffer.from(body) : body);
  } catch (error) {
    if (!(error instanceof ExecutorJSONSyntaxError)) throw error;
    throw new ValidationError(root, [
      { type: 'json_invalid', loc: [], message: 'Invalid JSON: ' + error.message, input: body },
    ]);
  }
  return decode_json_value(value, root, options.strict ?? false, 'json');
}
export function decode_bounded<K extends WireRootName>(
  body: Uint8Array,
  root: K,
  options: { limit?: number; strict?: boolean } = {},
): WireTypes[K] {
  if (!body.length || body.length > (options.limit ?? MAX_ENVELOPE_BYTES))
    throw new ProtocolRefused('body exceeds its size limit');
  return decode_json(body, root, options);
}
export function as_object<K extends WireRootName>(
  value: WireInput<NoInfer<K>>,
  root: K,
): JsonObject {
  const encoded = encode_value(value, root);
  if (
    encoded === null ||
    typeof encoded !== 'object' ||
    Array.isArray(encoded) ||
    encoded instanceof JsonNumber
  )
    throw new ValueError('expected object');
  return encoded as JsonObject;
}
function payload<K extends WireRootName>(
  value: WireInput<K>,
  root: K,
  operation: 'bounded' | 'frame',
): Buffer {
  const encoded = encode_typed_value(value, root, operation);
  // Pydantic's bytes serializer refuses unpaired UTF-16 even though value conversion retains it.
  const find = (v: unknown): string | undefined => {
    if (typeof v === 'string') return surrogate.test(v) ? v : undefined;
    if (Array.isArray(v)) {
      for (const item of v) {
        const result = find(item);
        if (result !== undefined) return result;
      }
    } else if (v !== null && typeof v === 'object' && !(v instanceof JsonNumber))
      for (const [k, x] of ordered_json_entries(v as Record<string, unknown>)) {
        const key = find(k);
        if (key !== undefined) return key;
        const result = find(x);
        if (result !== undefined) return result;
      }
  };
  const invalid = find(encoded);
  if (invalid !== undefined) {
    throw new PydanticSerializationError(
      'Executor JSON text contains an unpaired Unicode surrogate',
    );
  }
  return Buffer.from(stringify_pydantic_json(encoded));
}
export function encode_bounded<K extends WireRootName>(
  value: WireInput<NoInfer<K>>,
  root: K,
  options: { limit?: number } = {},
): Uint8Array {
  const body = payload(value, root, 'bounded');
  if (!body.length || body.length > (options.limit ?? MAX_ENVELOPE_BYTES))
    throw new ProtocolRefused('value exceeds its encoded size limit');
  return body;
}
export function encode_frame<K extends WireRootName>(
  value: WireInput<NoInfer<K>>,
  root: K,
): Uint8Array {
  const body = payload(value, root, 'frame');
  if (!body.length || body.length > MAX_ENVELOPE_BYTES)
    throw new ProtocolRefused('frame exceeds envelope limit');
  const header = Buffer.alloc(4);
  header.writeUInt32BE(body.length);
  return Buffer.concat([header, body]);
}
export async function read_typed_frame<K extends WireRootName>(
  reader: { readexactly(length: number): Promise<Uint8Array> },
  root: K,
): Promise<WireTypes[K]> {
  const length = Buffer.from(await reader.readexactly(4)).readUInt32BE();
  if (!length || length > MAX_ENVELOPE_BYTES) throw new ProtocolRefused('invalid frame length');
  return decode_json(await reader.readexactly(length), root, { strict: true });
}
