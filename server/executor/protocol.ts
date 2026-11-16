/** The shared native JSON codec for both executor peers. */
import type { WireRootName, WireTypes, JsonObject } from './wire.generated.ts';
import { ProtocolRefused } from '../execution_identity.ts';
import { MAX_ENVELOPE_BYTES } from './configuration.ts';
import { decode_value, encode_value, finite_json_value } from './protocol_values.ts';
import type { WireInput } from './protocol_values.ts';
import { ExecutorValidationError } from './protocol_errors.ts';
export { ProtocolRefused } from '../execution_identity.ts';
export {
  parse_identity,
  validate_identity,
  refusal_reason_for_versions,
  supports_executor_versions,
} from '../execution_identity.ts';
export { ExecutorValidationError } from './protocol_errors.ts';
export { decode_value, encode_value } from './protocol_values.ts';
export type { WireInput } from './protocol_values.ts';
export { encode_backend_event, decode_event_arguments } from './backend_events.ts';
export type { DecodedBackendEvent } from './backend_events.ts';
export type { WireRootName, WireTypes } from './wire.generated.ts';

export function decode_json<K extends WireRootName>(
  body: Uint8Array | string,
  root: K,
): WireTypes[K] {
  let value: unknown;
  try {
    const text =
      typeof body === 'string'
        ? body
        : new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(body);
    value = JSON.parse(text, finite_json_value);
  } catch (error) {
    throw new ExecutorValidationError(
      `Invalid executor JSON: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
  return decode_value(value, root);
}
export function decode_bounded<K extends WireRootName>(
  body: Uint8Array,
  root: K,
  options: { limit?: number } = {},
): WireTypes[K] {
  if (!body.length || body.length > (options.limit ?? MAX_ENVELOPE_BYTES))
    throw new ProtocolRefused('body exceeds its size limit');
  return decode_json(body, root);
}
export function as_object<K extends WireRootName>(
  value: WireInput<NoInfer<K>>,
  root: K,
): JsonObject {
  const encoded = encode_value(value, root);
  if (encoded === null || typeof encoded !== 'object' || Array.isArray(encoded))
    throw new ExecutorValidationError('Expected an executor JSON object.');
  return encoded as JsonObject;
}
export function encode_bounded<K extends WireRootName>(
  value: WireInput<NoInfer<K>>,
  root: K,
  options: { limit?: number } = {},
): Uint8Array {
  const body = Buffer.from(JSON.stringify(encode_value(value, root)));
  if (!body.length || body.length > (options.limit ?? MAX_ENVELOPE_BYTES))
    throw new ProtocolRefused('value exceeds its encoded size limit');
  return body;
}
export function encode_frame<K extends WireRootName>(
  value: WireInput<NoInfer<K>>,
  root: K,
): Uint8Array {
  const body = encode_bounded(value, root);
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
  return decode_json(await reader.readexactly(length), root);
}
