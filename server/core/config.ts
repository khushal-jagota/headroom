import { readFileSync, existsSync, realpathSync, statSync } from 'node:fs';
import { isAbsolute, dirname, join } from 'node:path';
import { homedir } from 'node:os';
import { parse } from 'yaml';
import { PlannerError } from './contracts.ts';
export const HOST = '127.0.0.1';
export const SSE_HEARTBEAT_MS = 15000;
export const DB_BUSY_TIMEOUT_MS = 5000;
export const SHUTDOWN_GRACE_SECONDS = 30;
export const MAX_CONVERSATION_SEND_REQUEST_BYTES = 20 * 1024 * 1024;
export interface Config {
  db_path: string;
  port: number;
  tick_seconds: number;
  dispatch_enabled: boolean;
  dispatcher_lock_path: string;
  logs_dir: string;
  backup_dir: string;
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
export function pythonRepr(value: unknown): string {
  if (value === null) return 'None';
  if (typeof value === 'boolean') return value ? 'True' : 'False';
  if (typeof value === 'string')
    return `'${value.replaceAll('\\', '\\\\').replaceAll("'", "\\'").replaceAll('\n', '\\n').replaceAll('\r', '\\r')}'`;
  return String(value);
}
export function loadConfig(path = 'config.yaml', env: NodeJS.ProcessEnv = process.env): Config {
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
  const bool = (v: unknown, key: string): boolean => {
    if (typeof v === 'boolean') return v;
    const s = String(v).trim().toLowerCase();
    if (['1', 'true', 'yes', 'on'].includes(s)) return true;
    if (['0', 'false', 'no', 'off'].includes(s)) return false;
    throw new PlannerError('validation', `invalid boolean for ${key}: ${pythonRepr(v)}`);
  };
  const integer = (key: string, variable: string, fallback: number): number => {
    const v = raw(key, variable, fallback);
    if (
      typeof v === 'boolean' ||
      !/^[+-]?\d+$/.test(String(v).trim()) ||
      !Number.isSafeInteger(Number(v))
    )
      throw new PlannerError('validation', `invalid integer for ${key}: ${pythonRepr(v)}`);
    return Number(v);
  };
  const directory = (key: string, variable: string): string | null => {
    const v = raw(key, variable);
    if (v == null) return null;
    if (typeof v !== 'string' || !v.trim())
      throw new PlannerError('validation', `invalid ${key}: ${pythonRepr(v)}`);
    const expanded = v === '~' ? homedir() : v.startsWith('~/') ? join(homedir(), v.slice(2)) : v;
    try {
      if (!isAbsolute(expanded)) throw new Error('the path must be absolute');
      const result = realpathSync(expanded);
      if (!statSync(result).isDirectory())
        throw new Error('the path must be an existing directory');
      return result;
    } catch (error) {
      throw new PlannerError(
        'validation',
        `invalid ${key} ${pythonRepr(v)}: ${(error as Error).message}`,
      );
    }
  };
  const test_mode = bool(env.PLAN_TEST_MODE ?? '0', 'test_mode');
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
    db_path,
    port: integer('port', 'PLAN_PORT', 8767),
    tick_seconds: integer('tick_seconds', 'PLAN_TICK_SECONDS', 60),
    dispatch_enabled: bool(
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
