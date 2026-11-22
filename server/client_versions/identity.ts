/** Client declarations describe versions. Authority still comes from the existing boundary. */
import type { ClientIdentity, ClientKind } from './contracts.ts';
const kinds = new Set<ClientKind>(['ios', 'desktop', 'browser', 'machine', 'cli']);
const numeric_fields = ['build', 'bridge_version', 'protocol_version', 'outbox_format'] as const;

export function parse_client_identity(
  headers: readonly string[] | undefined,
): ClientIdentity | null {
  if (headers === undefined) return null;
  if (headers.length !== 1 || headers[0]!.length > 2048)
    throw new Error('Send one bounded X-Panels-Client header.');
  let value: unknown;
  try {
    value = JSON.parse(headers[0]!);
  } catch {
    throw new Error('X-Panels-Client must contain a JSON identity.');
  }
  if (value === null || typeof value !== 'object' || Array.isArray(value))
    throw new Error('X-Panels-Client must contain a JSON identity.');
  const identity = value as Record<string, unknown>;
  if (
    !kinds.has(identity.kind as ClientKind) ||
    typeof identity.device_id !== 'string' ||
    !/^[A-Za-z0-9_.:-]{1,128}$/.test(identity.device_id) ||
    typeof identity.version !== 'string' ||
    !identity.version.trim() ||
    identity.version.length > 128
  )
    throw new Error('Client identity requires kind, device_id, and version.');
  for (const field of numeric_fields) {
    const number = identity[field];
    if (number !== undefined && (!Number.isSafeInteger(number) || (number as number) < 0))
      throw new Error(`Client identity ${field} must be a nonnegative integer.`);
  }
  // Keep only the version facts in the contract, not arbitrary caller data.
  return {
    kind: identity.kind as ClientKind,
    device_id: identity.device_id,
    version: identity.version,
    ...Object.fromEntries(
      numeric_fields
        .filter((field) => identity[field] !== undefined)
        .map((field) => [field, identity[field]]),
    ),
  };
}
