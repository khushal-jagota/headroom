import type { APNsConfig } from '../notifications/contracts.ts';
import { load_apns_config } from '../notifications/config.ts';
import { readFileSync, existsSync, realpathSync, statSync } from 'node:fs';
import { isAbsolute, dirname, join } from 'node:path';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { parse } from 'yaml';
import { PlannerError } from './contracts.ts';
import { copy_environment } from './environment.ts';
export const HOST = '127.0.0.1';
export const SSE_HEARTBEAT_MS = 15000;
export const DB_BUSY_TIMEOUT_MS = 5000;
export { SHUTDOWN_GRACE_SECONDS } from '../lifecycle/configuration.ts';
export const MAX_CONVERSATION_SEND_REQUEST_BYTES = 20 * 1024 * 1024;
/** Only these explicit host inputs cross the installed application launcher boundary. */
export const EXTERNAL_RUNTIME_ENVIRONMENT_KEYS: ReadonlySet<string> = new Set([
  'HOME',
  'LOGNAME',
  'PATH',
  'SHELL',
  'LANG',
  'TERM',
  'TMPDIR',
  'USER',
  'XDG_RUNTIME_DIR',
  'DBUS_SESSION_BUS_ADDRESS',
  'PLAN_CONFIG_PATH',
  'PLAN_DB_PATH',
  'PLAN_PORT',
  'PLAN_BOUNDARY_HOUR',
  'PLAN_TICK_SECONDS',
  'PLAN_DISPATCH_ENABLED',
  'PLAN_DISPATCHER_LOCK_PATH',
  'PLAN_LOGS_DIR',
  'PLAN_BACKUP_DIR',
  'PLAN_TRUSTED_INGRESS_PROVIDER',
  'PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN',
  'PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN',
  'PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS',
  'PLAN_GROQ_API_KEY',
  'PLAN_APNS_KEY_PATH',
  'PLAN_APNS_KEY_ID',
  'PLAN_APNS_TEAM_ID',
  'PLAN_APNS_BUNDLE_IDS',
  'PLAN_HERMES_HOME',
  'PLAN_HERMES_PYTHON',
  'PLAN_CLAUDE_EXECUTABLE',
  'PLAN_SERVER_CONTROL_SOCKET',
  'PLAN_UPDATE_REPOSITORY',
  'PLAN_UPDATE_TOKEN_FILE',
  'ANTHROPIC_API_KEY',
  'OPENAI_API_KEY',
  'GOOGLE_API_KEY',
  // TS's explicit lifecycle address must survive the first launcher boundary.
  'PLAN_SERVER_LIFECYCLE_LEASE_PATH',
  'PLAN_SERVER_LISTENER_FD',
]);
export interface Config {
  apns?: APNsConfig | null;
  db_path: string;
  port: number;
  tick_seconds: number;
  dispatch_enabled: boolean;
  dispatcher_lock_path: string;
  logs_dir: string;
  backup_dir: string;
  refuse_unidentified_agent_calls: boolean;
  trusted_ingress_provider: string | null;
  trusted_ingress_allowed_login: string | null;
  trusted_ingress_canonical_origin: string | null;
  voice_transcription_base_url: string;
  voice_transcription_model: string;
  voice_transcription_api_key: string | null;
  test_mode: boolean;
  fake_now: string | null;
  executor_id: string | null;
  app_sha: string | null;
  credential_key_path: string;
  measure_source_timeout_seconds: number;
  workspace_root: string | null;
  ticket_scratch_root: string | null;
  ticket_scratch_database: string | null;
}
export function parseConfigBoolean(
  value: unknown,
  key: string,
  source = String(value).trim(),
): boolean {
  if (typeof value === 'boolean') return value;
  const text = source.toLowerCase();
  if (['1', 'true', 'yes', 'on'].includes(text)) return true;
  if (['0', 'false', 'no', 'off'].includes(text)) return false;
  throw new PlannerError('validation', `invalid boolean for ${key}: ${JSON.stringify(value)}`);
}
/** The I/O guards use the same environment-only test setting as loadConfig. */
export function testModeFromEnvironment(env: NodeJS.ProcessEnv = process.env): boolean {
  return parseConfigBoolean(env.PLAN_TEST_MODE ?? '0', 'test_mode');
}
/** Socket routing and child authority are explicit inputs of the Mac CLI. */
export function executor_client_settings(env: Readonly<NodeJS.ProcessEnv> = process.env) {
  return {
    root: env.PLAN_EXECUTOR_ROOT,
    child_id: env.PLAN_EXECUTOR_CHILD_ID ?? null,
    capability: env.PLAN_EXECUTOR_CAPABILITY ?? null,
  };
}
export function mac_runtime_settings(env: Readonly<NodeJS.ProcessEnv> = process.env) {
  return {
    child: env.PLAN_EXECUTOR_RUNTIME_CHILD === '1',
    ready_path: env.PLAN_EXECUTOR_RUNTIME_READY ?? null,
    handoff_path: env.PLAN_EXECUTOR_RUNTIME_HANDOFF ?? null,
  };
}
/** Lifecycle consumers share raw inputs and a copy that does not forward the listener. */
export function lifecycle_launch_settings(env: Readonly<NodeJS.ProcessEnv>) {
  const environment = copy_environment(env);
  const listener_fd = env.PLAN_SERVER_LISTENER_FD;
  delete environment.PLAN_SERVER_LISTENER_FD;
  return {
    environment,
    config_path: env.PLAN_CONFIG_PATH,
    control_socket_path: env.PLAN_SERVER_CONTROL_SOCKET,
    lease_path: env.PLAN_SERVER_LIFECYCLE_LEASE_PATH,
    listener_fd,
  };
}
/** Update discovery uses the invocation snapshot, without another Config load. */
export function update_repository_configuration(
  environment: Readonly<NodeJS.ProcessEnv>,
): { readonly repository: string; readonly token_file: string } | null {
  const repository = environment.PLAN_UPDATE_REPOSITORY;
  const token_file = environment.PLAN_UPDATE_TOKEN_FILE;
  return repository && token_file ? { repository, token_file } : null;
}

/** Account launch inputs are environment-only, shared by native hosts and discovery. */
export function provider_launch_settings(env: Readonly<NodeJS.ProcessEnv> = process.env) {
  return {
    environment: { ...env },
    hermes_python: env.PLAN_HERMES_PYTHON || '~/.hermes/hermes-agent/venv/bin/python',
    hermes_home: env.PLAN_HERMES_HOME || '~/.hermes',
    claude_executable: env.PLAN_CLAUDE_EXECUTABLE,
    claude_home: env.CLAUDE_CONFIG_DIR || '~/.claude',
    codex_home: env.CODEX_HOME || '~/.codex',
    app_root: env.PLAN_APP_ROOT,
  };
}
const installer_unit_environment_names = new Set([
  'HOME',
  'CODEX_HOME',
  'CLAUDE_CONFIG_DIR',
  'HERMES_HOME',
  'PLAN_HERMES_HOME',
  'PATH',
  'LANG',
  'LANGUAGE',
  'TERM',
  'TMPDIR',
  'TMP',
  'TEMP',
  'USER',
  'LOGNAME',
  'XDG_RUNTIME_DIR',
  'DBUS_SESSION_BUS_ADDRESS',
  'PLAN_CONFIG_PATH',
  'PLAN_DB_PATH',
  'PLAN_APP_ROOT',
  'PLAN_LOGS_DIR',
  'PLAN_BACKUP_DIR',
  'PLAN_WORKSPACE_ROOT',
  'PLAN_TICKET_SCRATCH_ROOT',
  'PLAN_TICKET_SCRATCH_DATABASE',
  'PLAN_CREDENTIAL_KEY_PATH',
  'PLAN_DISPATCHER_LOCK_PATH',
  'PLAN_SERVER_CONTROL_SOCKET',
  'PLAN_SERVER_LIFECYCLE_LEASE_PATH',
  'PLAN_SERVER_URL',
  'PLAN_TEST_MODE',
]);
const installer_unit_path_names = new Set([
  'HOME',
  'CODEX_HOME',
  'CLAUDE_CONFIG_DIR',
  'HERMES_HOME',
  'PLAN_HERMES_HOME',
  'TMPDIR',
  'TMP',
  'TEMP',
  'XDG_RUNTIME_DIR',
  'PLAN_CONFIG_PATH',
  'PLAN_DB_PATH',
  'PLAN_APP_ROOT',
  'PLAN_LOGS_DIR',
  'PLAN_BACKUP_DIR',
  'PLAN_WORKSPACE_ROOT',
  'PLAN_TICKET_SCRATCH_ROOT',
  'PLAN_TICKET_SCRATCH_DATABASE',
  'PLAN_CREDENTIAL_KEY_PATH',
  'PLAN_DISPATCHER_LOCK_PATH',
  'PLAN_SERVER_CONTROL_SOCKET',
  'PLAN_SERVER_LIFECYCLE_LEASE_PATH',
]);
/** Select explicit production-unit inputs without forwarding test-runner markers. */
export function installer_unit_environment(env: Readonly<NodeJS.ProcessEnv>): {
  environment: NodeJS.ProcessEnv;
  paths: readonly string[];
} {
  const environment: NodeJS.ProcessEnv = {},
    paths: string[] = [];
  for (const [name, value] of Object.entries(env)) {
    if (
      (!installer_unit_environment_names.has(name) && !/^LC_[A-Z_]+$/.test(name)) ||
      value === undefined
    )
      continue;
    environment[name] = value;
    if (installer_unit_path_names.has(name) && value) paths.push(value);
  }
  return { environment, paths };
}

/** Application evidence has the same environment-only selector as Python. */
export function applicationRootFromEnvironment(env: NodeJS.ProcessEnv = process.env): string {
  return env.PLAN_APP_ROOT ?? fileURLToPath(new URL('../../', import.meta.url));
}
export function loadConfig(
  path: string | undefined = undefined,
  env: NodeJS.ProcessEnv = process.env,
  paths: { readonly home?: string; readonly admit_path?: (path: string) => string } = {},
): Config {
  const refusalFlag = env.PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS ?? '0';
  const refuse_unidentified_agent_calls = parseConfigBoolean(
    refusalFlag,
    'refuse_unidentified_agent_calls',
    refusalFlag.trim(),
  );
  if (env === process.env) delete process.env.PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS;
  path ??= env.PLAN_CONFIG_PATH ?? 'config.yaml';
  path = paths.admit_path?.(path) ?? path;
  const file: unknown = existsSync(path) ? parse(readFileSync(path, 'utf8')) : {};
  if (file !== null && (typeof file !== 'object' || Array.isArray(file)))
    throw new PlannerError('validation', `config root must be a mapping: ${path}`);
  const values = (file ?? {}) as Record<string, unknown>;
  const raw = (key: string, variable: string, fallback?: unknown): unknown =>
    env[variable] ?? values[key] ?? fallback;
  const text = (key: string, variable: string, fallback: string): string =>
    String(raw(key, variable, fallback));
  const optional = (key: string, variable: string): string | null => {
    const v = raw(key, variable);
    return v == null ? null : String(v).trim() || null;
  };
  const integer = (key: string, variable: string, fallback: number): number => {
    const v = raw(key, variable, fallback);
    if (
      typeof v === 'boolean' ||
      !/^[+-]?\d+$/.test(String(v).trim()) ||
      !Number.isSafeInteger(Number(v))
    )
      throw new PlannerError('validation', `invalid integer for ${key}: ${JSON.stringify(v)}`);
    return Number(v);
  };
  const directory = (key: string, variable: string): string | null => {
    const v = raw(key, variable);
    if (v == null) return null;
    if (typeof v !== 'string' || !v.trim())
      throw new PlannerError('validation', `invalid ${key}: ${JSON.stringify(v)}`);
    // Partial-env callers keep their existing ambient default unless home is explicit.
    const directory_home = paths.home === '' ? '/' : paths.home;
    const expanded =
      v === '~'
        ? (directory_home ?? homedir())
        : v.startsWith('~/')
          ? join(directory_home ?? homedir(), v.slice(2))
          : v;
    try {
      if (!isAbsolute(expanded)) throw new Error('the path must be absolute');
      const result = realpathSync(paths.admit_path?.(expanded) ?? expanded);
      if (!statSync(result).isDirectory())
        throw new Error('the path must be an existing directory');
      return result;
    } catch (error) {
      throw new PlannerError(
        'validation',
        `invalid ${key} ${JSON.stringify(v)}: ${(error as Error).message}`,
      );
    }
  };
  const test_mode = testModeFromEnvironment(env);
  let provider =
    optional('trusted_ingress_provider', 'PLAN_TRUSTED_INGRESS_PROVIDER')?.toLowerCase() ?? null;
  let login = optional('trusted_ingress_allowed_login', 'PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN');
  let origin = optional(
    'trusted_ingress_canonical_origin',
    'PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN',
  );
  if (!provider && (login || origin))
    throw new PlannerError(
      'validation',
      'trusted_ingress_provider is required when trusted ingress is configured',
    );
  if (provider) {
    if (provider !== 'tailscale')
      throw new PlannerError('validation', `unsupported trusted_ingress_provider: ${provider}`);
    if (!login)
      throw new PlannerError(
        'validation',
        'trusted_ingress_allowed_login is required for Tailscale trusted ingress',
      );
    if (!origin)
      throw new PlannerError(
        'validation',
        'trusted_ingress_canonical_origin is required for trusted ingress',
      );
    let valid = false;
    try {
      const u = new URL(origin);
      valid =
        (u.protocol === 'https:' ||
          (test_mode &&
            u.protocol === 'http:' &&
            ['127.0.0.1', 'localhost'].includes(u.hostname))) &&
        u.pathname === '/' &&
        !u.search &&
        !u.hash;
      origin = u.origin;
    } catch {
      /* reported below */
    }
    if (!valid)
      throw new PlannerError(
        'validation',
        'trusted_ingress_canonical_origin must be an HTTPS origin with no path',
      );
  } else {
    provider = login = origin = null;
  }
  const app_sha = optional('app_sha', 'PLAN_APP_SHA');
  if (app_sha && !/^[a-f0-9]{40}$/.test(app_sha))
    throw new PlannerError('validation', 'app SHA must be a 40-character lowercase Git SHA');
  const executor_id = optional('executor_id', 'PLAN_EXECUTOR_ID');
  if (executor_id && !/^[A-Za-z0-9_.:-]{1,128}$/.test(executor_id))
    throw new PlannerError('validation', 'executor_id must name a logical executor');
  const db_path = text('db_path', 'PLAN_DB_PATH', 'data/planning.db');
  const timeout = integer(
    'measure_source_timeout_seconds',
    'PLAN_MEASURE_SOURCE_TIMEOUT_SECONDS',
    30,
  );
  if (timeout <= 0)
    throw new PlannerError(
      'validation',
      `measure_source_timeout_seconds must be greater than zero: ${timeout}`,
    );
  return {
    apns: load_apns_config(env),
    db_path,
    port: integer('port', 'PLAN_PORT', 8767),
    tick_seconds: integer('tick_seconds', 'PLAN_TICK_SECONDS', 60),
    dispatch_enabled: parseConfigBoolean(
      raw('dispatch_enabled', 'PLAN_DISPATCH_ENABLED', true),
      'dispatch_enabled',
    ),
    dispatcher_lock_path: text(
      'dispatcher_lock_path',
      'PLAN_DISPATCHER_LOCK_PATH',
      'data/dispatcher.lock',
    ),
    logs_dir: text('logs_dir', 'PLAN_LOGS_DIR', 'data/logs'),
    backup_dir: text('backup_dir', 'PLAN_BACKUP_DIR', 'data/backups'),
    refuse_unidentified_agent_calls,
    trusted_ingress_provider: provider,
    trusted_ingress_allowed_login: login,
    trusted_ingress_canonical_origin: origin,
    voice_transcription_base_url: text(
      'voice_transcription_base_url',
      'PLAN_VOICE_TRANSCRIPTION_BASE_URL',
      'https://api.groq.com/openai/v1',
    ),
    voice_transcription_model: text(
      'voice_transcription_model',
      'PLAN_VOICE_TRANSCRIPTION_MODEL',
      'whisper-large-v3-turbo',
    ),
    voice_transcription_api_key: env.PLAN_GROQ_API_KEY || null,
    test_mode,
    fake_now: test_mode ? env.PLAN_FAKE_NOW || null : null,
    executor_id,
    app_sha,
    credential_key_path: text(
      'credential_key_path',
      'PLAN_CREDENTIAL_KEY_PATH',
      join(dirname(db_path), 'credential-secrets', 'key'),
    ),
    measure_source_timeout_seconds: timeout,
    workspace_root: directory('workspace_root', 'PLAN_WORKSPACE_ROOT'),
    ticket_scratch_root: directory('ticket_scratch_root', 'PLAN_TICKET_SCRATCH_ROOT'),
    ticket_scratch_database: optional('ticket_scratch_database', 'PLAN_TICKET_SCRATCH_DATABASE'),
  };
}
