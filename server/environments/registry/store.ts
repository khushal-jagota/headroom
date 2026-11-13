import { closeSync, constants, mkdirSync, openSync } from 'node:fs';
import { mkdir, readFile, rename, unlink, writeFile } from 'node:fs/promises';
import { dirname, isAbsolute, join, relative } from 'node:path';
import { randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import koffi from 'koffi';
import {
  acquire_server_lifecycle_lease,
  resolve_server_lifecycle_lease_path,
} from '../../lifecycle/lease.ts';
import {
  ServerLifecycleAlreadyOwnedError,
  ServerLifecycleError,
} from '../../lifecycle/contracts.ts';
import type { LifecycleLease } from '../../lifecycle/contracts.ts';
import {
  acquireRuntimeOwnership,
  canonical_database_path,
  runtime_authority_path,
  observe_existing_flock,
  ConversationRuntimeAlreadyOwnedError,
} from '../../core/locks/index.ts';
import { EnvironmentValidationError } from './contracts.ts';
import type {
  EnvironmentInspection,
  EnvironmentManifest,
  EnvironmentManifestPayload,
  EnvironmentPortPolicy,
  PathAdmission,
  ResolvedEnvironmentInstance,
} from './contracts.ts';
import {
  canonical_path,
  dynamic_port,
  fixed_port,
  resolve_environment_instance,
  validate_absolute_environment_root,
  validate_repository_roots,
} from './logic.ts';

export const MANIFEST_FILENAME = 'manifest.json';
export const FAKE_FIXTURE_VERSION = 'fake-fixture-v1';
const libc = koffi.load(process.platform === 'darwin' ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6');
const flock = libc.func('int flock(int fd, int operation)');
function registry_flock(descriptor: number, operation: number): number {
  let result: number;
  do {
    result = flock(descriptor, operation);
  } while (result !== 0 && koffi.errno() === 4); // EINTR: Python retries the interrupted syscall.
  return result;
}

export async function with_registry_lock<T>(
  root: string,
  action: () => Promise<T>,
  admit_path?: PathAdmission,
): Promise<T> {
  const canonical = await validate_absolute_environment_root(root, admit_path);
  const resolved = admit_path?.(canonical) ?? canonical;
  mkdirSync(resolved, { recursive: true });
  const marker = join(resolved, '.registry.lock');
  const descriptor = openSync(
    admit_path?.(marker) ?? marker,
    constants.O_RDWR | constants.O_CREAT,
    0o600,
  );
  try {
    if (registry_flock(descriptor, 2) !== 0) {
      const error = koffi.errno();
      if ([11, 13, 35].includes(error))
        throw new EnvironmentValidationError(`environment registry is locked: ${resolved}`);
      throw new Error(`flock failed: errno ${error}`);
    }
    return await action();
  } finally {
    try {
      registry_flock(descriptor, 8);
    } finally {
      closeSync(descriptor);
    }
  }
}
export function manifest_from_instance(
  instance: ResolvedEnvironmentInstance,
  prepared_at: number | null,
): EnvironmentManifest {
  const { prepared: _prepared, running: _running, allowed_repository_roots, ...fields } = instance;
  return { ...fields, repository_roots: allowed_repository_roots, prepared_at };
}
export function instance_from_manifest(manifest: EnvironmentManifest): ResolvedEnvironmentInstance {
  const { repository_roots, prepared_at, ...fields } = manifest;
  return {
    ...fields,
    allowed_repository_roots: repository_roots,
    prepared: prepared_at !== null,
    running: false,
  };
}
export function manifest_payload(manifest: EnvironmentManifest): EnvironmentManifestPayload {
  const { port_policy, ...fields } = manifest;
  return { ...fields, runtime_port: port_policy };
}
export async function write_manifest(
  manifest: EnvironmentManifest,
  admit_path?: PathAdmission,
): Promise<void> {
  const root = admit_path?.(manifest.instance_root) ?? manifest.instance_root;
  await mkdir(root, { recursive: true });
  const destination = join(root, MANIFEST_FILENAME);
  const target = admit_path?.(destination) ?? destination;
  const staged = join(root, `.${MANIFEST_FILENAME}-${randomUUID()}`);
  const temporary = admit_path?.(staged) ?? staged;
  const sorted = (value: unknown): unknown =>
    Array.isArray(value)
      ? value.map(sorted)
      : value !== null && typeof value === 'object'
        ? Object.fromEntries(
            Object.entries(value)
              .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
              .map(([key, item]) => [key, sorted(item)]),
          )
        : value;
  const text =
    JSON.stringify(sorted(manifest_payload(manifest)), null, 2).replace(
      /[\u007f-\uffff]/g,
      (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'),
    ) + '\n';
  try {
    await writeFile(temporary, text, { flag: 'wx', mode: 0o600 });
    await rename(temporary, target);
  } finally {
    try {
      await unlink(temporary);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
  }
}
function integer(value: unknown): number {
  if (typeof value === 'boolean') return Number(value);
  if (typeof value === 'number' && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === 'string' && /^[+-]?\d(?:_?\d)*$/.test(value.trim()))
    return Number(value.trim().replaceAll('_', ''));
  throw new TypeError('invalid integer');
}
function port_from_payload(payload: Record<string, unknown>): EnvironmentPortPolicy {
  const raw = payload.runtime_port;
  if (raw !== null && typeof raw === 'object' && !Array.isArray(raw)) {
    const port = raw as Record<string, unknown>;
    if (port.kind === 'fixed') return fixed_port(integer(port.port));
    if (port.kind === 'dynamic') return dynamic_port(integer(port.bind_attempts));
  }
  throw new EnvironmentValidationError('invalid environment runtime port policy');
}
export async function read_manifest(
  path: string,
  caller_environment_root: string,
  admit_path?: PathAdmission,
): Promise<EnvironmentManifest> {
  path = admit_path?.(path) ?? path;
  let manifest: EnvironmentManifest;
  try {
    const raw: unknown = JSON.parse(
      new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await readFile(path)),
    );
    if (raw === null || typeof raw !== 'object' || Array.isArray(raw))
      throw new TypeError('manifest object required');
    const payload = raw as Record<string, unknown>;
    const legacy = ['expected_linux_account', 'hermes_home', 'runtime_user_home'].filter((key) =>
      Object.hasOwn(payload, key),
    );
    if (legacy.length)
      throw new EnvironmentValidationError(
        `legacy environment manifest must be re-prepared: ${legacy.sort().join(', ')}`,
      );
    for (const field of [
      'kind',
      'instance_id',
      'environment_root',
      'instance_root',
      'db_path',
      'managed_files_root',
      'logs_dir',
      'dispatcher_lock_path',
      'server_control_socket_path',
      'repository_roots',
    ])
      if (!Object.hasOwn(payload, field)) throw new TypeError('missing manifest field');
    const required_path = (field: string): string => {
      const value = payload[field];
      if (typeof value !== 'string') throw new TypeError('invalid path');
      return value;
    };
    if (
      !Array.isArray(payload.repository_roots) ||
      payload.repository_roots.some((root) => typeof root !== 'string')
    )
      throw new TypeError('invalid path');
    if (payload.credentials_env_file != null && typeof payload.credentials_env_file !== 'string')
      throw new TypeError('invalid credential path');
    manifest = {
      environment_root: required_path('environment_root'),
      instance_root: required_path('instance_root'),
      db_path: required_path('db_path'),
      managed_files_root: required_path('managed_files_root'),
      logs_dir: required_path('logs_dir'),
      dispatcher_lock_path: required_path('dispatcher_lock_path'),
      server_control_socket_path: required_path('server_control_socket_path'),
      kind: payload.kind,
      instance_id: payload.instance_id,
      port_policy: port_from_payload(payload),
      credentials_env_file: payload.credentials_env_file ?? null,
      fixture_version: payload.fixture_version ?? null,
      prepared_at: payload.prepared_at ?? null,
      repository_roots: payload.repository_roots,
    } as EnvironmentManifest;
  } catch (error) {
    if (error instanceof EnvironmentValidationError) throw error;
    if (error instanceof TypeError || error instanceof SyntaxError)
      throw new EnvironmentValidationError(`invalid environment manifest: ${path}`);
    throw error;
  }
  await validate_manifest_location(manifest, path, caller_environment_root, admit_path);
  return manifest;
}
async function validate_manifest_location(
  manifest: EnvironmentManifest,
  path: string,
  root: string,
  admit_path?: PathAdmission,
): Promise<void> {
  const environment_root = await validate_absolute_environment_root(root, admit_path);
  const location = relative(environment_root, await canonical_path(path));
  const kind =
    location === MANIFEST_FILENAME
      ? 'live'
      : location === join('staging', MANIFEST_FILENAME)
        ? 'staging'
        : null;
  if (kind === null)
    throw new EnvironmentValidationError(`invalid environment manifest location: ${path}`);
  for (const field of [
    'environment_root',
    'instance_root',
    'db_path',
    'managed_files_root',
    'logs_dir',
    'dispatcher_lock_path',
    'server_control_socket_path',
    'credentials_env_file',
  ] as const)
    if (manifest[field] !== null && !isAbsolute(manifest[field]))
      throw new EnvironmentValidationError(
        `invalid environment manifest: ${field} must be absolute`,
      );
  for (const [index, repository] of manifest.repository_roots.entries())
    if (!isAbsolute(repository))
      throw new EnvironmentValidationError(
        `invalid environment manifest: repository_roots[${index}] must be absolute`,
      );
  let repositories: readonly string[];
  try {
    const roots = manifest.repository_roots.map((path) => admit_path?.(path) ?? path);
    repositories = await validate_repository_roots(roots, roots);
  } catch (error) {
    if (error instanceof EnvironmentValidationError)
      throw new EnvironmentValidationError(`invalid environment manifest: ${error.message}`);
    throw error;
  }
  if (!isDeepStrictEqual(repositories, manifest.repository_roots))
    throw new EnvironmentValidationError(
      'invalid environment manifest: repository roots must be canonical',
    );
  if (
    manifest.credentials_env_file !== null &&
    (await canonical_path(
      admit_path?.(manifest.credentials_env_file) ?? manifest.credentials_env_file,
    )) !== manifest.credentials_env_file
  )
    throw new EnvironmentValidationError(
      'invalid environment manifest: credentials_env_file must be canonical',
    );
  const expected = manifest_from_instance(
    await resolve_environment_instance({
      kind,
      instance_id: kind,
      environment_root,
      port: manifest.port_policy.kind === 'fixed' ? manifest.port_policy.port : null,
      credentials_env_file: manifest.credentials_env_file,
      allowed_repository_roots: repositories,
      requested_repository_roots: repositories,
      fixture_version: kind === 'live' ? null : FAKE_FIXTURE_VERSION,
      prepared: manifest.prepared_at !== null,
    }),
    manifest.prepared_at,
  );
  for (const field of [
    'kind',
    'instance_id',
    'environment_root',
    'instance_root',
    'db_path',
    'managed_files_root',
    'logs_dir',
    'dispatcher_lock_path',
    'server_control_socket_path',
    'port_policy',
    'credentials_env_file',
    'fixture_version',
    'repository_roots',
  ] as const)
    if (!isDeepStrictEqual(manifest[field], expected[field]))
      throw new EnvironmentValidationError(
        `invalid environment manifest: ${field} does not match canonical value`,
      );
}
export function environment_lifecycle_lease_path(manifest: EnvironmentManifest): string {
  return manifest.port_policy.kind === 'fixed'
    ? resolve_server_lifecycle_lease_path(manifest.port_policy.port)
    : join(manifest.instance_root, 'run/server-lifecycle.lock');
}
export function acquire_stopped_environment_lease(
  manifest: EnvironmentManifest,
  admit_path?: PathAdmission,
): LifecycleLease {
  let lifecycle: LifecycleLease;
  try {
    const path = environment_lifecycle_lease_path(manifest);
    lifecycle = acquire_server_lifecycle_lease(
      admit_path?.(path) ?? path,
      manifest.port_policy.kind === 'fixed' ? manifest.port_policy.port : 0,
    );
  } catch (error) {
    if (error instanceof ServerLifecycleAlreadyOwnedError)
      throw new EnvironmentValidationError('environment instance is running');
    if (error instanceof ServerLifecycleError)
      throw new EnvironmentValidationError(
        `could not prove environment instance is stopped: ${error.message}`,
      );
    throw error;
  }
  try {
    // A supervisor holds the port lease between incarnations. A directly started
    // application holds this canonical DB lock. Both must remain free throughout mutation.
    const database = acquireRuntimeOwnership(admit_path?.(manifest.db_path) ?? manifest.db_path);
    return {
      close() {
        try {
          database.close();
        } finally {
          lifecycle.close();
        }
      },
    };
  } catch (error) {
    lifecycle.close();
    if (error instanceof ConversationRuntimeAlreadyOwnedError)
      throw new EnvironmentValidationError('environment instance is running');
    throw new EnvironmentValidationError(
      `could not prove environment instance is stopped: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}
export function environment_lifecycle_lease_is_held(manifest: EnvironmentManifest): boolean {
  try {
    const database = canonical_database_path(manifest.db_path);
    return [
      runtime_authority_path(database),
      `${database}.conversation-owner.lock`,
      environment_lifecycle_lease_path(manifest),
    ].some((path) => observe_existing_flock(path) === 'held');
  } catch (error) {
    throw new EnvironmentValidationError(
      `could not probe environment running state: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}
export function manifest_to_json_dict(
  manifest: EnvironmentManifest,
  options: { prepared?: boolean; running?: boolean } = {},
): EnvironmentInspection {
  const { port_policy, ...fields } = manifest;
  const common = { ...fields, prepared: options.prepared ?? manifest.prepared_at !== null };
  return port_policy.kind === 'fixed'
    ? {
        ...common,
        runtime_port_policy: 'fixed',
        port: port_policy.port,
        running: options.running ?? environment_lifecycle_lease_is_held(manifest),
      }
    : { ...common, runtime_port_policy: 'dynamic', bind_attempts: port_policy.bind_attempts };
}
export function manifest_to_json(
  manifest: EnvironmentManifest,
  options: { prepared?: boolean; running?: boolean } = {},
): string {
  return JSON.stringify(manifest_to_json_dict(manifest, options));
}
