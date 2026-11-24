/** Plain JSON records for persisted updates. */
import type {
  PublishedUpdate,
  ArtifactSource,
  ReleaseParts,
  UpdateAttempt,
  UpdateAttemptState,
  UpdateMode,
} from './contracts.ts';
import { parse_optional_runtime_contract } from './logic/runtime_contract.ts';
import { is_record as is_object } from '../../core/records.ts';
import type { ClientCompatibility } from '../../client_versions/contracts.ts';

export { is_record as is_object } from '../../core/records.ts';

function required(value: Record<string, unknown>, key: string): unknown {
  if (!(key in value)) throw new Error(`missing update field: ${key}`);
  return value[key];
}
function text(value: unknown): string {
  if (value === undefined || (value !== null && typeof value === 'object'))
    throw new TypeError('Update field must be text or a scalar value');
  return String(value);
}
function integer(value: unknown): number {
  const number = Number(value);
  if (!Number.isSafeInteger(number)) throw new Error('invalid publication integer');
  return number;
}
function artifact_source(payload: Record<string, unknown>): { artifact_source?: ArtifactSource } {
  if (payload.artifact_source === undefined) return {};
  if (payload.artifact_source !== 'github-release') throw new Error('invalid artifact source');
  return { artifact_source: payload.artifact_source };
}
function parse_compatibility(value: unknown): ClientCompatibility {
  if (
    !is_object(value) ||
    !is_object(value.ios) ||
    !is_object(value.desktop_shell) ||
    !is_object(value.machine)
  )
    throw new Error('invalid release compatibility');
  const positive_integer = (input: unknown): number => {
    if (typeof input !== 'number' || !Number.isSafeInteger(input) || input < 1)
      throw new Error('invalid release compatibility');
    return input;
  };
  const result: ClientCompatibility = {
    ios: { minimum_build: positive_integer(value.ios.minimum_build) },
    desktop_shell: {
      minimum_bridge_version: positive_integer(value.desktop_shell.minimum_bridge_version),
    },
    machine: {
      minimum_protocol_version: positive_integer(value.machine.minimum_protocol_version),
      maximum_protocol_version: positive_integer(value.machine.maximum_protocol_version),
    },
    outbox_format: positive_integer(value.outbox_format),
  };
  if (result.machine.minimum_protocol_version > result.machine.maximum_protocol_version)
    throw new Error('invalid release compatibility');
  return result;
}
function parse_parts(value: unknown): ReleaseParts {
  if (
    !is_object(value) ||
    !is_object(value.server) ||
    !is_object(value.screens) ||
    !is_object(value.machines)
  )
    throw new Error('invalid release parts');
  const digest = (input: unknown): string => {
    if (typeof input !== 'string' || !/^[0-9a-f]{64}$/.test(input))
      throw new Error('invalid part digest');
    return input;
  };
  if (typeof value.server.app_sha !== 'string' || !/^[0-9a-f]{40}$/.test(value.server.app_sha))
    throw new Error('invalid server part SHA');
  return {
    server: {
      app_sha: value.server.app_sha,
      artifact_digest: digest(value.server.artifact_digest),
    },
    screens: { sha256: digest(value.screens.sha256) },
    machines: Object.fromEntries(
      Object.entries(value.machines).map(([system, machine]) => {
        if (
          !is_object(machine) ||
          (machine.build_id !== null && typeof machine.build_id !== 'string')
        )
          throw new Error('invalid machine part');
        return [
          system,
          { archive_sha256: digest(machine.archive_sha256), build_id: machine.build_id },
        ];
      }),
    ),
  };
}
export function parse_release(payload: Record<string, unknown>): PublishedUpdate {
  const manifest = required(payload, 'manifest');
  if (!is_object(manifest)) throw new Error('publication manifest must be an object');
  const mac = payload.mac ?? null;
  if (mac !== null && !is_object(mac)) throw new Error('Mac publication must be an object');
  const linux = payload.linux_executor ?? null;
  const source = artifact_source(payload);
  const expiry = required(payload, 'expires_at');
  if (expiry === null && source.artifact_source !== 'github-release')
    throw new Error('Only GitHub Releases can have no expiry');
  return {
    ...source,
    ...(payload.parts === undefined ? {} : { parts: parse_parts(payload.parts) }),
    ...(payload.compatibility === undefined
      ? {}
      : { compatibility: parse_compatibility(payload.compatibility) }),
    linux_executor:
      !is_object(linux) ||
      !['artifact_id', 'archive_sha256', 'archive_size'].every((key) => Object.hasOwn(linux, key))
        ? null
        : {
            ...artifact_source(linux),
            artifact_id: required(linux, 'artifact_id') as number,
            archive_sha256: required(linux, 'archive_sha256') as string,
            archive_size: required(linux, 'archive_size') as number,
          },
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
    expires_at: expiry === null ? null : text(expiry),
    mac:
      mac === null
        ? null
        : {
            ...artifact_source(mac),
            artifact_id: required(mac, 'artifact_id') as number,
            archive_sha256: required(mac, 'archive_sha256') as string,
            archive_size: required(mac, 'archive_size') as number,
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
  'snapshot_required',
  'steps',
  'mac_attempt_id',
]);
export function parse_attempt(payload: unknown): UpdateAttempt {
  if (!is_object(payload)) throw new Error('update attempt must be an object');
  const release = required(payload, 'release');
  if (!is_object(release)) throw new Error('update attempt release must be an object');
  const steps = payload.steps ?? {};
  if (!is_object(steps)) throw new Error('update attempt steps must be an object');
  return {
    attempt_id: text(required(payload, 'attempt_id')),
    from_sha: text(required(payload, 'from_sha')),
    to_sha: text(required(payload, 'to_sha')),
    mode: required(payload, 'mode') as UpdateMode,
    confirm_activity: Boolean(payload.confirm_activity),
    state: required(payload, 'state') as UpdateAttemptState,
    created_at: text(required(payload, 'created_at')),
    updated_at: text(required(payload, 'updated_at')),
    release: parse_release(release),
    detail: (payload.detail ?? null) as string | null,
    deadline_at: (payload.deadline_at ?? null) as string | null,
    backup: (payload.backup ?? null) as string | null,
    snapshot_required: payload.snapshot_required === true,
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
