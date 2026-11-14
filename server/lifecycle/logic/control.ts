import {
  is_json_record as object,
  parse_numeric_json,
  JsonNumber,
} from '../../core/http/numeric_json.ts';
import { MAX_CONTROL_MESSAGE_BYTES } from '../configuration.ts';
import { ServerRestartProtocolError } from '../contracts.ts';
import type { ControlRequest, ControlResponse, StopCapabilityRecord } from '../contracts.ts';

function shape(value: Record<string, unknown>, names: readonly string[]): boolean {
  return Object.keys(value).sort().join(',') === [...names].sort().join(',');
}
function version(value: unknown, expected: number): boolean {
  return value instanceof JsonNumber && value.kind === 'integer' && value.value === expected;
}
export function decode_message(bytes: Buffer): Record<string, unknown> {
  if (bytes.length > MAX_CONTROL_MESSAGE_BYTES || bytes.at(-1) !== 10)
    throw new ServerRestartProtocolError('control message must be one bounded JSON line');
  let value: unknown;
  try {
    value = parse_numeric_json(
      new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes),
    );
  } catch {
    throw new ServerRestartProtocolError('control message is not valid JSON');
  }
  if (!object(value)) throw new ServerRestartProtocolError('control message must be a JSON object');
  return value;
}
export function encode_message(
  value: ControlRequest | ControlResponse | StopCapabilityRecord,
): Buffer {
  const bytes = Buffer.from(JSON.stringify(value) + '\n');
  if (bytes.length > MAX_CONTROL_MESSAGE_BYTES)
    throw new ServerRestartProtocolError('control message exceeds the protocol limit');
  return bytes;
}
export function decode_request(bytes: Buffer): ControlRequest {
  const value = decode_message(bytes);
  if (value.operation === 'restart') {
    if (!shape(value, ['version', 'operation']))
      throw new ServerRestartProtocolError('invalid control request shape');
    if (!version(value.version, 1))
      throw new ServerRestartProtocolError('incompatible control request');
    return { version: 1, operation: 'restart' };
  }
  if (
    !shape(value, ['version', 'operation', 'incarnation', 'capability']) ||
    !version(value.version, 2) ||
    value.operation !== 'stop_intent' ||
    typeof value.incarnation !== 'string' ||
    typeof value.capability !== 'string'
  )
    throw new ServerRestartProtocolError('incompatible control request');
  return {
    version: 2,
    operation: 'stop_intent',
    incarnation: value.incarnation,
    capability: value.capability,
  };
}
export function decode_response(bytes: Buffer, expected_version: 1 | 2 = 1): ControlResponse {
  const value = decode_message(bytes);
  if (expected_version === 1) {
    if (!shape(value, ['version', 'status']))
      throw new ServerRestartProtocolError('invalid control response shape');
    if (!version(value.version, 1) || value.status !== 'accepted')
      throw new ServerRestartProtocolError('incompatible control response');
    return { version: 1, status: 'accepted' };
  }
  const pid = value.supervisor_pid;
  if (
    !shape(value, ['version', 'status', 'incarnation', 'supervisor_pid']) ||
    !version(value.version, 2) ||
    value.status !== 'accepted' ||
    typeof value.incarnation !== 'string' ||
    !(pid instanceof JsonNumber) ||
    pid.kind !== 'integer' ||
    !Number.isSafeInteger(pid.value) ||
    pid.value <= 0
  )
    throw new ServerRestartProtocolError('incompatible control response');
  return {
    version: 2,
    status: 'accepted',
    incarnation: value.incarnation,
    supervisor_pid: pid.value,
  };
}
export function decode_capability(bytes: Buffer): StopCapabilityRecord {
  const value = decode_message(bytes),
    pid = value.supervisor_pid;
  if (
    !shape(value, ['version', 'incarnation', 'capability', 'supervisor_pid']) ||
    !version(value.version, 2) ||
    typeof value.incarnation !== 'string' ||
    typeof value.capability !== 'string' ||
    !/^[0-9a-f]{64}$/.test(value.capability) ||
    !(pid instanceof JsonNumber) ||
    pid.kind !== 'integer' ||
    !Number.isSafeInteger(pid.value) ||
    pid.value <= 0
  )
    throw new ServerRestartProtocolError('incompatible stop capability record');
  return {
    version: 2,
    incarnation: value.incarnation,
    capability: value.capability,
    supervisor_pid: pid.value,
  };
}
