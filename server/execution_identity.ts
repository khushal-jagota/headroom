/** Executor identity checks are shared by both peers, independently of the codec facade. */
import {
  MIN_SUPPORTED_PROTOCOL_VERSION,
  PROTOCOL_VERSION,
  SPOOL_FORMAT_VERSION,
} from './execution_configuration.ts';
import { ValueError } from './core/value_error.ts';
import { JsonNumber } from './core/http/numeric_json.ts';
import { strip } from './core/whitespace.ts';
import { assigned_after_unicode_15 } from './core/python_repr_unicode.ts';
import { integer as hexadecimal_integer } from './core/unidentified_agent_calls/logic.ts';
import { integer_value, normalize_integer } from './executor/integers.ts';
import type { ExecutorIdentity as WireIdentity, WireInteger } from './executor/wire.generated.ts';

export interface ExecutorIdentity {
  protocol_version: WireInteger;
  application_revision: string;
  spool_format_version: WireInteger;
  executor_incarnation: string;
  connection_generation: WireInteger;
}
export class ProtocolRefused extends ValueError {
  constructor(message: string) {
    super(message);
    this.name = 'ProtocolRefused';
  }
}
export const MAC_NEEDS_UPDATE = 'Mac needs update';
export const MAC_VERSION_INCOMPATIBLE = 'Mac version is incompatible';
function is_integer(value: unknown): value is WireInteger {
  return value instanceof JsonNumber
    ? value.kind === 'integer'
    : typeof value === 'number' && Number.isInteger(value) && !Object.is(value, -0);
}
export function supports_executor_versions(
  protocol_version: unknown,
  spool_format_version: unknown,
): boolean {
  return (
    is_integer(protocol_version) &&
    is_integer(spool_format_version) &&
    BigInt(MIN_SUPPORTED_PROTOCOL_VERSION) <= integer_value(protocol_version) &&
    integer_value(protocol_version) <= BigInt(PROTOCOL_VERSION) &&
    integer_value(spool_format_version) === BigInt(SPOOL_FORMAT_VERSION)
  );
}
export function refusal_reason_for_versions(
  protocol_version: WireInteger,
  spool_format_version: WireInteger,
): string {
  return integer_value(protocol_version) < BigInt(PROTOCOL_VERSION) ||
    integer_value(spool_format_version) < BigInt(SPOOL_FORMAT_VERSION)
    ? MAC_NEEDS_UPDATE
    : MAC_VERSION_INCOMPATIBLE;
}
/** Python UUID first rewrites its text, then checks length and parses a hexadecimal integer. */
function valid_uuid(text: string): boolean {
  let hex = text.replaceAll('urn:', '').replaceAll('uuid:', '');
  let start = 0,
    end = hex.length;
  while (start < end && (hex[start] === '{' || hex[start] === '}')) start++;
  while (end > start && (hex[end - 1] === '{' || hex[end - 1] === '}')) end--;
  hex = hex.slice(start, end).replaceAll('-', '');
  if (Array.from(hex).length !== 32 || /[\u001c-\u001f]/u.test(hex)) return false;
  hex = strip(hex)
    .replace(/\p{Decimal_Number}/gu, (digit) => {
      let first = digit.codePointAt(0)!;
      if (assigned_after_unicode_15(first)) return digit;
      while (first > 0 && /\p{Decimal_Number}/u.test(String.fromCodePoint(first - 1))) first--;
      return String((digit.codePointAt(0)! - first) % 10);
    })
    .replace(/^(\+?0[xX])_/, '$1');
  try {
    const value = hexadecimal_integer(hex, 16);
    return value >= 0n && value < 1n << 128n;
  } catch {
    return false;
  }
}
export function validate_identity(
  identity: WireIdentity,
  options: { check_versions?: boolean } = {},
): void {
  if (typeof identity.application_revision !== 'string' || !strip(identity.application_revision))
    throw new ProtocolRefused('application revision must be a nonempty diagnostic label');
  if (!valid_uuid(identity.executor_incarnation))
    throw new ProtocolRefused('incarnation must be UUID');
  if (
    ![
      identity.protocol_version,
      identity.spool_format_version,
      identity.connection_generation === undefined ? 0 : identity.connection_generation,
    ].every(is_integer)
  )
    throw new ProtocolRefused('identity versions and generation must be integers');
  if (
    (options.check_versions ?? true) &&
    !supports_executor_versions(identity.protocol_version, identity.spool_format_version)
  )
    throw new ProtocolRefused('executor protocol or spool format mismatch');
}
export function parse_identity(
  payload: unknown,
  options: { check_versions?: boolean } = {},
): ExecutorIdentity {
  if (
    payload === null ||
    typeof payload !== 'object' ||
    Array.isArray(payload) ||
    payload instanceof JsonNumber ||
    ![Object.prototype, null].includes(Object.getPrototypeOf(payload)) ||
    ![
      'protocol_version',
      'application_revision',
      'spool_format_version',
      'executor_incarnation',
    ].every((key) => Object.hasOwn(payload, key))
  )
    throw new ProtocolRefused('invalid identity fields');
  const raw = payload as Record<string, unknown>;
  for (const key of ['protocol_version', 'spool_format_version', 'connection_generation'])
    if (Object.hasOwn(raw, key) && !is_integer(raw[key]))
      throw new ProtocolRefused('identity integer field has wrong type');
  for (const key of ['application_revision', 'executor_incarnation'])
    if (typeof raw[key] !== 'string')
      throw new ProtocolRefused('identity text field has wrong type');
  const identity: ExecutorIdentity = {
    protocol_version: normalize_integer(raw.protocol_version as WireInteger),
    application_revision: raw.application_revision as string,
    spool_format_version: normalize_integer(raw.spool_format_version as WireInteger),
    executor_incarnation: raw.executor_incarnation as string,
    connection_generation: normalize_integer((raw.connection_generation ?? 0) as WireInteger),
  };
  validate_identity(identity, options);
  return identity;
}
