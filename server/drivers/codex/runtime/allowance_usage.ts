/** Rate-limit policy over an injected, bounded Codex app-server read. */
import { parse_backend_instance_id } from '../../core/identity.ts';
import type {
  BackendUsageAdapter,
  BackendUsageResult,
  BackendUsageWindow,
} from '../../../conversation/backend_usage/contracts.ts';
import { is_record as object } from '../../../core/records.ts';
import {
  parse_datetime,
  percentage,
  usage_window,
  window_kind,
} from '../../../conversation/backend_usage/logic/windows.ts';

export interface CodexUsageSource {
  /** Return the raw RPC result JSON. Do not parse and reserialize its numeric tokens. */
  (executable: string, options: { timeout_seconds: number; signal?: AbortSignal }): Promise<string>;
}
/** The Codex unit owns the short-lived app-server child and its cleanup. */
export class CodexUsageTimeout extends Error {}
export class CodexUsageRequestRejected extends Error {
  readonly code: number;
  constructor(code: number, message: string) {
    super(message);
    this.code = code;
  }
}
export class CodexUsageLoginIsDuplicated extends Error {}

export const CODEX_USAGE_REFRESH_TIMEOUT_SECONDS = 30;
export class CodexUsageAdapter implements BackendUsageAdapter {
  private readonly options: {
    executable_path(name: string): string | null;
    read_codex_rate_limits: CodexUsageSource;
    now(): Date;
  };
  constructor(options: {
    executable_path(name: string): string | null;
    read_codex_rate_limits: CodexUsageSource;
    now(): Date;
  }) {
    this.options = options;
  }
  async refresh(options: { signal?: AbortSignal } = {}): Promise<BackendUsageResult> {
    options.signal?.throwIfAborted();
    const result = (
      outcome: BackendUsageResult['outcome'],
      detail: string,
    ): BackendUsageResult => ({
      backend_key: parse_backend_instance_id('codex'),
      outcome,
      detail,
      observed_at: null,
      windows: [],
    });
    const executable = this.options.executable_path('codex');
    if (executable === null) return result('unavailable', 'Codex is not installed or not on PATH.');
    let response: string;
    try {
      response = await this.options.read_codex_rate_limits(executable, {
        timeout_seconds: CODEX_USAGE_REFRESH_TIMEOUT_SECONDS,
        ...options,
      });
    } catch (error) {
      options.signal?.throwIfAborted();
      if (error instanceof CodexUsageTimeout)
        return result('failed', 'Codex usage did not answer in time. Try again.');
      if (error instanceof CodexUsageRequestRejected) {
        const unauthenticated =
          [401, 403].includes(error.code) ||
          ['not logged in', 'login required', 'authentication required', 'unauthorized'].some(
            (phrase) => error.message.toLowerCase().includes(phrase),
          );
        return result(
          unauthenticated ? 'unauthenticated' : 'failed',
          unauthenticated
            ? 'Codex is not logged in. Run `codex login` and try again.'
            : 'Codex could not refresh its usage. Try again.',
        );
      }
      if (error instanceof CodexUsageLoginIsDuplicated) return result('failed', error.message);
      return result('failed', 'Codex could not refresh its usage. Try again.');
    }
    const windows = parse_codex_windows(response);
    if (windows === null) return result('failed', 'Codex could not refresh its usage. Try again.');
    if (!windows.length)
      return result('failed', 'Codex returned usage in a format Panels does not understand.');
    return {
      backend_key: parse_backend_instance_id('codex'),
      outcome: 'succeeded',
      detail: null,
      observed_at: this.options.now(),
      windows,
    };
  }
}

/** Validate the complete app-server answer before selecting understood windows. */
export function parse_codex_windows(text: string): readonly BackendUsageWindow[] | null {
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return null;
  }
  if (!object(body) || !valid_bucket(body.rateLimits)) return null;
  const by_id = body.rateLimitsByLimitId;
  if (by_id !== undefined && by_id !== null && !object(by_id)) return null;
  const buckets = by_id === undefined || by_id === null ? {} : by_id;
  for (const value of Object.values(buckets)) if (!valid_bucket(value)) return null;
  const account = body.rateLimits;
  const windows = codex_bucket_windows(account, null, null);
  for (const [id, value] of Object.entries(buckets)) {
    const bucket = value as Record<string, unknown>;
    if (account.limitId != null && (id === account.limitId || bucket.limitId === account.limitId))
      continue;
    windows.push(...codex_bucket_windows(bucket, id, (bucket.limitName || id) as string));
  }
  return windows;
}
function valid_bucket(value: unknown): value is Record<string, unknown> {
  if (!object(value)) return false;
  for (const key of ['limitId', 'limitName'])
    if (value[key] != null && typeof value[key] !== 'string') return false;
  for (const key of ['primary', 'secondary']) {
    const window = value[key];
    if (window === undefined || window === null) continue;
    if (
      !object(window) ||
      typeof window.usedPercent !== 'number' ||
      !Number.isInteger(window.usedPercent)
    )
      return false;
    for (const field of ['windowDurationMins', 'resetsAt'])
      if (
        window[field] != null &&
        (typeof window[field] !== 'number' || !Number.isInteger(window[field]))
      )
        return false;
  }
  return true;
}
/** Account windows are named by their length, a named limit's as `<limit id>:<length>`. */
function codex_bucket_windows(
  bucket: Record<string, unknown>,
  limit_id: string | null,
  model_scope: string | null,
): BackendUsageWindow[] {
  const windows: BackendUsageWindow[] = [];
  for (const key of ['primary', 'secondary']) {
    const value = bucket[key];
    if (!object(value)) continue;
    const kind = window_kind(value.windowDurationMins);
    const used_percent = percentage(value.usedPercent);
    const resets_at = parse_datetime(value.resetsAt);
    if (kind !== null && used_percent !== null && resets_at !== null)
      windows.push(
        usage_window({
          id: limit_id === null ? kind : `${limit_id}:${kind}`,
          kind,
          used_percent,
          resets_at,
          model_scope,
        }),
      );
  }
  return windows;
}
