import { python_string_repr } from '../../core/python_repr.ts';
/** Python-compatible persisted JSON decoding and canonical encoding. */
import type {
  PublishedUpdate,
  UpdateAttempt,
  UpdateAttemptState,
  UpdateMode,
} from './contracts.ts';
import { parse_optional_runtime_contract } from './logic/runtime_contract.ts';
import {
  JsonNumber,
  parse_numeric_json,
  stringify_python_json,
} from '../../core/http/numeric_json.ts';

export const is_object = (value: unknown): value is Record<string, unknown> =>
  value !== null &&
  typeof value === 'object' &&
  !Array.isArray(value) &&
  !(value instanceof JsonNumber);

/** Python's attempt reader also accepts its JSON encoder's non-finite constants. */
export function parse_attempt_json(source: string): unknown {
  const literals = [...source.matchAll(/"(?:\\[\s\S]|[^"\\])*"/g)].map(
    (match) => JSON.parse(match[0]) as string,
  );
  let prefix = '__panels_update_constant__';
  while (literals.some((literal) => literal.includes(prefix))) prefix += '_';
  const constants = new Map<string, number>();
  let encoded = '',
    quoted = false,
    previous = '';
  for (let index = 0; index < source.length; index++) {
    const character = source[index]!;
    if (quoted) {
      encoded += character;
      if (character === '\\') encoded += source[++index] ?? '';
      else if (character === '"') quoted = false;
      continue;
    }
    if (character === '"') quoted = true;
    const token =
      !quoted && (!previous || '[,:'.includes(previous))
        ? /^(NaN|-Infinity|Infinity)(?=[\s,\]}]|$)/.exec(source.slice(index))?.[0]
        : undefined;
    if (token !== undefined) {
      const marker = prefix + constants.size;
      constants.set(marker, token === 'NaN' ? NaN : token === 'Infinity' ? Infinity : -Infinity);
      encoded += JSON.stringify(marker);
      index += token.length - 1;
      previous = 'v';
    } else {
      encoded += character;
      if (!/\s/.test(character)) previous = character;
    }
  }
  function restore(value: unknown): unknown {
    if (typeof value === 'string' && constants.has(value)) return constants.get(value);
    if (Array.isArray(value)) return value.map(restore);
    if (is_object(value))
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, restore(item)]));
    return value;
  }
  return restore(parse_numeric_json(encoded));
}
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
function repr(value: unknown): string {
  if (value instanceof JsonNumber) return stringify_python_json(value);
  if (value === null || value === undefined) return 'None';
  if (value === true) return 'True';
  if (value === false) return 'False';
  if (typeof value === 'string') return python_string_repr(value);
  if (Array.isArray(value)) return '[' + value.map(repr).join(', ') + ']';
  if (is_object(value))
    return (
      '{' +
      Object.entries(value)
        .map(([key, val]) => `${repr(key)}: ${repr(val)}`)
        .join(', ') +
      '}'
    );
  return String(value);
}
function text(value: unknown): string {
  return typeof value === 'string' ? value : repr(value);
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
  return stringify_python_json(value, { sort_keys: true, compact: true, ensure_ascii: true });
}
