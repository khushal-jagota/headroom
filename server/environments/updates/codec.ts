/** Exact-number JSON decoding and canonical encoding for persisted updates. */
import type {
  PublishedUpdate,
  UpdateAttempt,
  UpdateAttemptState,
  UpdateMode,
} from './contracts.ts';
import { parse_optional_runtime_contract } from './logic/runtime_contract.ts';
import {
  is_json_record as is_object,
  JsonNumber,
  parse_numeric_json,
} from '../../core/http/numeric_json.ts';

export { is_json_record as is_object } from '../../core/http/numeric_json.ts';

export const parse_attempt_json = parse_numeric_json;
function required(value: Record<string, unknown>, key: string): unknown {
  if (!(key in value)) throw new Error(`missing update field: ${key}`);
  return value[key];
}
function truth(value: unknown): boolean {
  if (value instanceof JsonNumber) return value.value !== 0;
  if (value === null || value === undefined || value === false || value === 0 || value === '')
    return false;
  if (Array.isArray(value)) return value.length !== 0;
  if (is_object(value)) return Object.keys(value).length !== 0;
  return true;
}
function text(value: unknown): string {
  if (value instanceof JsonNumber)
    return value.integer === null ? String(value.value) : String(value.integer);
  if (value === undefined || (value !== null && typeof value === 'object'))
    throw new TypeError('Update field must be text or a scalar value');
  return String(value);
}
function integer(value: unknown): number | JsonNumber {
  const raw = text(value).trim();
  if (!/^[+-]?[0-9]+(?:_[0-9]+)*$/.test(raw)) throw new Error('invalid publication integer');
  const exact = BigInt(raw.replaceAll('_', '')).toString();
  const projected = Number(exact);
  return Number.isSafeInteger(projected) ? projected : new JsonNumber(exact);
}
export function parse_release(payload: Record<string, unknown>): PublishedUpdate {
  const manifest = required(payload, 'manifest');
  if (!is_object(manifest)) throw new Error('publication manifest must be an object');
  const mac = payload.mac ?? null;
  if (mac !== null && !is_object(mac)) throw new Error('Mac publication must be an object');
  return {
    publication_run_id: integer(required(payload, 'publication_run_id')),
    artifact_id: integer(required(payload, 'artifact_id')),
    proof_artifact_id: integer(required(payload, 'proof_artifact_id')),
    manifest: {
      app_sha: required(manifest, 'app_sha') as string,
      source_digest: required(manifest, 'source_digest') as string,
      artifact_digest: required(manifest, 'artifact_digest') as string,
      runtime_contract: parse_optional_runtime_contract(manifest.runtime_contract),
    },
    archive_sha256: text(required(payload, 'archive_sha256')),
    schema_floor: text(required(payload, 'schema_floor')),
    expires_at: text(required(payload, 'expires_at')),
    mac:
      mac === null
        ? null
        : {
            artifact_id: required(mac, 'artifact_id') as number | JsonNumber,
            archive_sha256: required(mac, 'archive_sha256') as string,
            archive_size: required(mac, 'archive_size') as number | JsonNumber,
          },
  };
}
const KNOWN_FIELDS = new Set([
  'attempt_id',
  'from_sha',
  'to_sha',
  'mode',
  'confirm_activity',
  'state',
  'created_at',
  'updated_at',
  'release',
  'detail',
  'deadline_at',
  'backup',
  'steps',
  'mac_attempt_id',
]);
export function parse_attempt(payload: unknown): UpdateAttempt {
  if (!is_object(payload)) throw new Error('update attempt must be an object');
  const release = required(payload, 'release');
  if (!is_object(release)) throw new Error('update attempt release must be an object');
  const steps = truth(payload.steps) ? payload.steps : {};
  if (!is_object(steps)) throw new Error('update attempt steps must be an object');
  return {
    attempt_id: text(required(payload, 'attempt_id')),
    from_sha: text(required(payload, 'from_sha')),
    to_sha: text(required(payload, 'to_sha')),
    mode: required(payload, 'mode') as UpdateMode,
    confirm_activity: truth(payload.confirm_activity),
    state: required(payload, 'state') as UpdateAttemptState,
    created_at: text(required(payload, 'created_at')),
    updated_at: text(required(payload, 'updated_at')),
    release: parse_release(release),
    detail: (payload.detail ?? null) as string | null,
    deadline_at: (payload.deadline_at ?? null) as string | null,
    backup: (payload.backup ?? null) as string | null,
    steps: Object.fromEntries(Object.entries(steps).map(([key, val]) => [key, text(val)])),
    mac_attempt_id: (payload.mac_attempt_id ?? null) as string | null,
    extra: Object.fromEntries(Object.entries(payload).filter(([key]) => !KNOWN_FIELDS.has(key))),
  };
}
export function attempt_to_dict(attempt: UpdateAttempt): Record<string, unknown> {
  const { extra, ...known } = attempt;
  return { ...extra, ...known };
}
export function merged(previous: unknown, current: unknown): unknown {
  if (!is_object(previous) || !is_object(current)) return current;
  return {
    ...previous,
    ...Object.fromEntries(
      Object.entries(current).map(([key, value]) => [key, merged(previous[key], value)]),
    ),
  };
}
export function canonical_json(value: unknown): string {
  return JSON.stringify(value);
}
