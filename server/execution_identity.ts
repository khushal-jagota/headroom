/** Executor identity checks are shared by both peers, independently of the codec facade. */
import {
  MIN_SUPPORTED_PROTOCOL_VERSION,
  PROTOCOL_VERSION,
  SPOOL_FORMAT_VERSION,
} from './execution_configuration.ts';
import { ExecutorValidationError } from './executor/protocol_errors.ts';
import { strip } from './core/whitespace.ts';
import type { ExecutorIdentity as WireIdentity } from './executor/wire.generated.ts';

export interface ExecutorIdentity {
  protocol_version: number;
  application_revision: string;
  spool_format_version: number;
  executor_incarnation: string;
  connection_generation: number;
}
export class ProtocolRefused extends ExecutorValidationError {
  constructor(message: string) {
    super(message);
    this.name = 'ProtocolRefused';
  }
}
export const MAC_NEEDS_UPDATE = 'Mac needs update';
export const MAC_VERSION_INCOMPATIBLE = 'Mac version is incompatible';
function is_integer(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value);
}
export function supports_executor_versions(
  protocol_version: unknown,
  spool_format_version: unknown,
): boolean {
  return (
    is_integer(protocol_version) &&
    is_integer(spool_format_version) &&
    MIN_SUPPORTED_PROTOCOL_VERSION <= protocol_version &&
    protocol_version <= PROTOCOL_VERSION &&
    spool_format_version === SPOOL_FORMAT_VERSION
  );
}
export function refusal_reason_for_versions(
  protocol_version: number,
  spool_format_version: number,
): string {
  return protocol_version < PROTOCOL_VERSION || spool_format_version < SPOOL_FORMAT_VERSION
    ? MAC_NEEDS_UPDATE
    : MAC_VERSION_INCOMPATIBLE;
}
function valid_uuid(text: string): boolean {
  let hex = text.replaceAll('urn:', '').replaceAll('uuid:', '');
  let start = 0,
    end = hex.length;
  while (start < end && (hex[start] === '{' || hex[start] === '}')) start++;
  while (end > start && (hex[end - 1] === '{' || hex[end - 1] === '}')) end--;
  hex = hex.slice(start, end).replaceAll('-', '');
  if (Array.from(hex).length !== 32 || /[\u001c-\u001f]/u.test(hex)) return false;
  // Retain the wire's ASCII forms without interpreting Unicode digits as hex.
  return /^\+?(?:0[xX]_?)?[a-fA-F0-9](?:_?[a-fA-F0-9])*$/.test(strip(hex));
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
    protocol_version: raw.protocol_version as number,
    application_revision: raw.application_revision as string,
    spool_format_version: raw.spool_format_version as number,
    executor_incarnation: raw.executor_incarnation as string,
    connection_generation: (raw.connection_generation ?? 0) as number,
  };
  validate_identity(identity, options);
  return identity;
}
