import { isAbsolute, dirname, join } from 'node:path';
import { path_is_within } from '../../core/path_containment.ts';
import { readFile, stat } from 'node:fs/promises';
import { resolved_path } from '../paths.ts';
import { EnvironmentValidationError } from './contracts.ts';
import type {
  EnvironmentCredentialPolicy,
  EnvironmentKind,
  ResolvedEnvironmentInstance,
  EnvironmentPortPolicy,
  ResolveEnvironmentRequest,
  PathAdmission,
} from './contracts.ts';

export const DEFAULT_ENVIRONMENT_DEFAULTS = Object.freeze({
  live_port: 8767,
  staging_bind_attempts: 10,
});
export const DEFAULT_ENVIRONMENT_CREDENTIAL_POLICY: EnvironmentCredentialPolicy = Object.freeze({
  allowed_keys: new Set(['ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'GOOGLE_API_KEY']),
  production_only_keys: new Set<string>(),
});
export const MAX_AF_UNIX_SOCKET_PATH_BYTES = 103;
const FORBIDDEN = new Set([
  'HOME',
  'LOGNAME',
  'PATH',
  'PWD',
  'PYTHONHOME',
  'PYTHONPATH',
  'SHELL',
  'USER',
  'VIRTUAL_ENV',
  'VIRTUAL_ENV_PROMPT',
]);
/** Resolve paths without expanding home prefixes: relative '~' remains a literal filename. */
export const canonical_path = (path: string): Promise<string> =>
  resolved_path(isAbsolute(path) ? path : process.cwd() + '/' + path);
export function validate_instance_id(kind: EnvironmentKind, id: string | null = null): string {
  if (kind !== 'live' && kind !== 'staging')
    throw new EnvironmentValidationError(`unknown environment kind: ${kind}`);
  if (id !== null && id !== kind)
    throw new EnvironmentValidationError(`${kind} instance id must be '${kind}'`);
  return kind;
}
export async function validate_absolute_environment_root(
  root: string,
  admit_path?: PathAdmission,
): Promise<string> {
  if (!isAbsolute(root)) throw new EnvironmentValidationError('environment root must be absolute');
  return canonical_path(admit_path?.(root) ?? root);
}
export function fixed_port(port: number, label = 'fixed environment port'): EnvironmentPortPolicy {
  if (port < 1 || port > 65535)
    throw new EnvironmentValidationError(`${label} must be between 1 and 65535`);
  return { kind: 'fixed', port };
}
export function dynamic_port(bind_attempts = 10): EnvironmentPortPolicy {
  if (bind_attempts < 1)
    throw new EnvironmentValidationError('dynamic port bind attempts must be positive');
  return { kind: 'dynamic', bind_attempts };
}
export function validate_server_control_socket_path_length(path: string): string {
  const bytes = Buffer.byteLength(path);
  if (bytes > MAX_AF_UNIX_SOCKET_PATH_BYTES)
    throw new EnvironmentValidationError(
      `server control socket path is too long for AF_UNIX (${bytes} bytes; max 103): ${path}`,
    );
  return path;
}
async function exists(path: string, directory = false): Promise<boolean> {
  try {
    const info = await stat(path);
    return directory ? info.isDirectory() : info.isFile();
  } catch (error) {
    if (['ENOENT', 'ENOTDIR'].includes((error as NodeJS.ErrnoException).code ?? '')) return false;
    throw error;
  }
}
export async function validate_repository_roots(
  requested: readonly string[],
  allowed: readonly string[],
): Promise<string[]> {
  async function checked(path: string): Promise<string> {
    const root = await canonical_path(path);
    if (!(await exists(root, true)))
      throw new EnvironmentValidationError(`repository root does not exist: ${root}`);
    const marker = join(root, '.git');
    let valid = false;
    if (await exists(marker, true))
      valid = (await exists(join(marker, 'HEAD'))) && (await exists(join(marker, 'objects'), true));
    else if (await exists(marker)) {
      try {
        const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true })
          .decode(await readFile(marker))
          .trim();
        const colon = text.indexOf(':');
        if (colon >= 0 && text.slice(0, colon) === 'gitdir') {
          const target = text.slice(colon + 1).trim();
          const directory = await canonical_path(
            isAbsolute(target) ? target : dirname(marker) + '/' + target,
          );
          valid = (await exists(directory, true)) && (await exists(join(directory, 'HEAD')));
        }
      } catch (error) {
        if (!(error instanceof TypeError) && !('code' in (error as object))) throw error;
      }
    }
    if (!valid) throw new EnvironmentValidationError(`repository root is not a worktree: ${root}`);
    return root;
  }
  const allowed_roots: string[] = [],
    requested_roots: string[] = [];
  for (const root of allowed) allowed_roots.push(await checked(root));
  for (const root of requested) requested_roots.push(await checked(root));
  for (const root of requested_roots)
    if (!allowed_roots.includes(root))
      throw new EnvironmentValidationError(`repository root is not allowed: ${root}`);
  return requested_roots;
}
export async function credential_reference(
  kind: EnvironmentKind,
  root: string,
  path: string | null,
): Promise<string | null> {
  if (path === null) return null;
  const value = await canonical_path(path);
  if (kind === 'live')
    throw new EnvironmentValidationError(
      "live uses the vps user's normal provider homes and must not register a credential file",
    );
  if (path_is_within(await validate_absolute_environment_root(root), value))
    throw new EnvironmentValidationError(
      `staging credential references must not be equal to or nested under the environment root: ${value}`,
    );
  return value;
}
function overlap(left: string, right: string): boolean {
  return path_is_within(left, right) || path_is_within(right, left);
}
function isolation_paths(instance: ResolvedEnvironmentInstance): Record<string, string> {
  const paths: Record<string, string> = {
    instance_root: instance.instance_root,
    db_path: instance.db_path,
    managed_files_root: instance.managed_files_root,
    logs_dir: instance.logs_dir,
    dispatcher_lock_path: instance.dispatcher_lock_path,
    server_control_socket_path: instance.server_control_socket_path,
  };
  if (instance.credentials_env_file !== null)
    paths.credentials_env_file = instance.credentials_env_file;
  instance.allowed_repository_roots.forEach((root, index) => {
    paths[`repository_roots[${index}]`] = root;
  });
  return paths;
}
export function validate_resolved_registry(
  instances: readonly ResolvedEnvironmentInstance[],
): void {
  const ports = new Map<number, string>(),
    ids = new Set<string>();
  for (const instance of instances)
    if (instance.port_policy.kind === 'fixed') {
      const previous = ports.get(instance.port_policy.port);
      if (previous !== undefined)
        throw new EnvironmentValidationError(
          `duplicate port ${instance.port_policy.port}: ${previous} and ${instance.instance_id}`,
        );
      ports.set(instance.port_policy.port, instance.instance_id);
    }
  for (const instance of instances) {
    if (ids.has(instance.instance_id))
      throw new EnvironmentValidationError(`duplicate instance id: ${instance.instance_id}`);
    ids.add(instance.instance_id);
  }
  if (instances.filter((instance) => instance.kind === 'live').length > 1)
    throw new EnvironmentValidationError('only one live environment is allowed');
  instances.forEach((left, index) => {
    for (const right of instances.slice(index + 1))
      for (const [a, ap] of Object.entries(isolation_paths(left)))
        for (const [b, bp] of Object.entries(isolation_paths(right)))
          if (overlap(ap, bp))
            throw new EnvironmentValidationError(
              `overlapping paths: ${left.instance_id}.${a}=${ap} and ${right.instance_id}.${b}=${bp}`,
            );
  });
}
export async function resolve_environment_instance(
  options: ResolveEnvironmentRequest,
): Promise<ResolvedEnvironmentInstance> {
  const instance_id = validate_instance_id(options.kind, options.instance_id ?? null);
  const root = await validate_absolute_environment_root(options.environment_root);
  const repositories = await validate_repository_roots(
    options.requested_repository_roots ?? [],
    options.allowed_repository_roots ?? [],
  );
  if (options.kind === 'live' && repositories.length)
    throw new EnvironmentValidationError(
      'live runs the deployed app and must not register a source repository',
    );
  if (options.kind === 'live' && options.credentials_env_file != null)
    throw new EnvironmentValidationError(
      "live uses the vps user's normal provider homes and must not register a credential file",
    );
  const defaults = options.defaults ?? DEFAULT_ENVIRONMENT_DEFAULTS;
  fixed_port(defaults.live_port, 'live default port');
  dynamic_port(defaults.staging_bind_attempts);
  const instance_root = options.kind === 'live' ? root : join(root, 'staging');
  const durable = options.kind === 'live' ? join(root, 'current') : instance_root;
  let port_policy: EnvironmentPortPolicy;
  if (options.kind === 'live') port_policy = fixed_port(options.port ?? defaults.live_port);
  else {
    if (options.port != null)
      throw new EnvironmentValidationError(
        'staging selects its port at launch; --port is only valid for live',
      );
    port_policy = dynamic_port(defaults.staging_bind_attempts);
  }
  const credentials = await credential_reference(
    options.kind,
    root,
    options.credentials_env_file ?? null,
  );
  const run = options.kind === 'live' ? join(durable, 'data') : join(instance_root, 'run');
  return {
    kind: options.kind,
    instance_id,
    environment_root: root,
    instance_root,
    db_path: join(durable, 'data/planner.db'),
    managed_files_root: join(durable, 'data/files'),
    logs_dir: join(durable, 'logs'),
    dispatcher_lock_path: join(run, 'dispatcher.lock'),
    server_control_socket_path: validate_server_control_socket_path_length(
      join(run, 'server-control.sock'),
    ),
    port_policy,
    credentials_env_file: credentials,
    allowed_repository_roots: repositories,
    fixture_version: options.fixture_version ?? null,
    prepared: options.prepared ?? false,
    running: options.running ?? false,
  };
}
export function validate_environment_key(
  key: string,
  kind: EnvironmentKind,
  policy = DEFAULT_ENVIRONMENT_CREDENTIAL_POLICY,
): void {
  if (!/^[A-Z_][A-Z0-9_]*$/.test(key))
    throw new EnvironmentValidationError(`malformed environment key: ${key}`);
  if (FORBIDDEN.has(key) || key.startsWith('PLAN_') || key.startsWith('HERMES_'))
    throw new EnvironmentValidationError(`forbidden environment key: ${key}`);
  if (!policy.allowed_keys.has(key))
    throw new EnvironmentValidationError(`unknown environment key: ${key}`);
  if (kind !== 'live' && policy.production_only_keys.has(key))
    throw new EnvironmentValidationError(`production-only environment key: ${key}`);
}
export function validate_environment_values(
  values: Readonly<Record<string, string>>,
  kind: EnvironmentKind,
  policy = DEFAULT_ENVIRONMENT_CREDENTIAL_POLICY,
): Record<string, string> {
  for (const key of Object.keys(values)) validate_environment_key(key, kind, policy);
  return { ...values };
}
export async function parse_environment_file(
  path: string,
  kind: EnvironmentKind,
  policy = DEFAULT_ENVIRONMENT_CREDENTIAL_POLICY,
): Promise<Record<string, string>> {
  const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(
    await readFile(path),
  );
  const values: Record<string, string> = {};
  for (const [index, raw] of text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/).entries()) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const equals = line.indexOf('='),
      key = equals < 0 ? '' : line.slice(0, equals).trim();
    if (!/^[A-Z_][A-Z0-9_]*$/.test(key))
      throw new EnvironmentValidationError(`malformed environment line ${index + 1}`);
    if (Object.hasOwn(values, key))
      throw new EnvironmentValidationError(`duplicate environment key: ${key}`);
    validate_environment_key(key, kind, policy);
    let value = line.slice(equals + 1).trim();
    if (value.length >= 2 && value[0] === value.at(-1) && ["'", '"'].includes(value[0]))
      value = value.slice(1, -1);
    values[key] = value;
  }
  return values;
}
