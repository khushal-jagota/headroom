/**
 * Antigravity's 5-hour and weekly windows, read with the agy CLI's own login. The login file, the
 * injected HTTP function and the injected login renewal are the only external doors.
 */
// Translated from T3 Code (MIT), apps/server/src/provider/antigravityUsageLimits.ts, commit b79d277c.
import { readFileSync } from 'node:fs';
import { parse_backend_instance_id } from '../../core/identity.ts';
import type {
  BackendUsageAdapter,
  BackendUsageResult,
  BackendUsageWindow,
  BackendUsageWindowKind,
  UsageHttpResponse,
} from '../../../conversation/backend_usage/contracts.ts';
import { backend_io_is_test } from '../../../conversation/snapshot/environment.ts';
import { is_record as object } from '../../../core/records.ts';
import { parse_datetime, usage_window } from '../../../conversation/backend_usage/logic/windows.ts';

const DAILY_HOST = 'https://daily-cloudcode-pa.googleapis.com';
const PROD_HOST = 'https://cloudcode-pa.googleapis.com';
// Personal sign-in is metered on the daily host first. Panels signs in personally only.
const QUOTA_HOSTS = [DAILY_HOST, PROD_HOST];
const ANTIGRAVITY_USAGE_TIMEOUT_SECONDS = 20;
/** An access token this close to its expiry is renewed first. */
const TOKEN_REUSE_MARGIN_MS = 60_000;
const SIGN_IN_MESSAGE = 'Antigravity is signed out. Sign in to Antigravity again.';
const UNREACHABLE_MESSAGE = 'Antigravity usage could not be reached. Try again.';

export interface UsageHttpPost {
  (
    url: string,
    headers: Readonly<Record<string, string>>,
    body: string,
    timeout_seconds: number,
    options?: { signal?: AbortSignal },
  ): Promise<UsageHttpResponse>;
}

/** The production transport refuses test contexts before any credential reaches fetch. */
export const http_post_usage: UsageHttpPost = async (
  url,
  headers,
  body,
  timeout_seconds,
  options = {},
) => {
  if (backend_io_is_test())
    throw new Error('Production backend I/O is unavailable in tests. Inject recording adapters.');
  const timeout = AbortSignal.timeout(timeout_seconds * 1000);
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
  const response = await fetch(url, { method: 'POST', headers, body, signal, redirect: 'manual' });
  let parsed: unknown = null;
  try {
    parsed = await response.json();
  } catch {
    signal.throwIfAborted();
    // The adapter reports a missing body by status.
  }
  return { status_code: response.status, body: parsed };
};

/** The CLI login's access token and when it expires. agy keeps the refresh to itself. */
export interface AntigravityCliLogin {
  readonly access_token: string;
  readonly expires_at: Date;
}

/** `<home>/antigravity-cli/antigravity-oauth-token`, or null when it is missing or unreadable. */
export function read_antigravity_cli_login(path: string): AntigravityCliLogin | null {
  let document: unknown;
  try {
    document = JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
  if (!object(document) || !object(document.token)) return null;
  const { access_token, expiry } = document.token;
  const expires_at = parse_datetime(expiry);
  if (typeof access_token !== 'string' || !access_token || expires_at === null) return null;
  return { access_token, expires_at };
}

/**
 * The Google account the CLI login belongs to, from its `id_token_email`. Null when the login is
 * missing, unreadable or names no email. Only the email leaves this reader, never a token.
 */
export function read_antigravity_account_email(path: string): string | null {
  let document: unknown;
  try {
    document = JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
  if (!object(document) || typeof document.id_token_email !== 'string') return null;
  return document.id_token_email.trim() || null;
}

/** Node's platform and architecture as Go names them, as agy's own client reports them. */
const GO_PLATFORM: Readonly<Record<string, string>> = { win32: 'windows' };
const GO_ARCHITECTURE: Readonly<Record<string, string>> = { x64: 'amd64', ia32: '386' };
/**
 * T3's client name. Without a user agent the quota summary answers 403, and with agy 1.5.10's own
 * it returns only the Claude and GPT buckets (verified on the VPS, 18 November 2026).
 */
function antigravity_user_agent(): string {
  const os_type = GO_PLATFORM[process.platform] ?? process.platform;
  const arch = GO_ARCHITECTURE[process.arch] ?? process.arch;
  return `antigravity/hub/2.24.0 (aidev_client; os_type=${os_type}; arch=${arch})`;
}

export interface AntigravityUsageOptions {
  /** The CLI login file. */
  readonly login_path: string;
  readonly http_post: UsageHttpPost;
  /**
   * Makes agy renew its own login, as a throwaway session does. Throws when agy cannot be run.
   * Panels cannot renew the token itself: the login holds no client credentials.
   */
  readonly refresh_login: (signal?: AbortSignal) => Promise<void>;
  readonly now: () => Date;
}

export class AntigravityUsageAdapter implements BackendUsageAdapter {
  private readonly options: AntigravityUsageOptions;
  constructor(options: AntigravityUsageOptions) {
    this.options = options;
  }
  async refresh(options: { signal?: AbortSignal } = {}): Promise<BackendUsageResult> {
    options.signal?.throwIfAborted();
    const result = (
      outcome: BackendUsageResult['outcome'],
      detail: string,
    ): BackendUsageResult => ({
      backend_key: parse_backend_instance_id('antigravity'),
      outcome,
      detail,
      observed_at: null,
      windows: [],
    });
    const usable = (login: AntigravityCliLogin | null): login is AntigravityCliLogin =>
      login !== null &&
      login.expires_at.getTime() > this.options.now().getTime() + TOKEN_REUSE_MARGIN_MS;
    let login = read_antigravity_cli_login(this.options.login_path);
    if (login === null) return result('unauthenticated', SIGN_IN_MESSAGE);
    if (!usable(login)) {
      try {
        await this.options.refresh_login(options.signal);
      } catch {
        options.signal?.throwIfAborted();
        return result('failed', UNREACHABLE_MESSAGE);
      }
      login = read_antigravity_cli_login(this.options.login_path);
      if (!usable(login)) return result('unauthenticated', SIGN_IN_MESSAGE);
    }
    const access_token = login.access_token;
    let reached = false;
    let signed_out = false;
    const query = async (operation: string, parse: (body: unknown) => Keyed[]) => {
      const readings = await Promise.all(
        QUOTA_HOSTS.map(async (host) => {
          try {
            const response = await this.options.http_post(
              `${host}/v1internal:${operation}`,
              {
                authorization: `Bearer ${access_token}`,
                'content-type': 'application/json',
                'user-agent': antigravity_user_agent(),
              },
              '{}',
              ANTIGRAVITY_USAGE_TIMEOUT_SECONDS,
              options,
            );
            if (response.status_code === 401 || response.status_code === 403) signed_out = true;
            if (response.status_code < 200 || response.status_code >= 300) return [];
            reached = true;
            return parse(response.body);
          } catch {
            options.signal?.throwIfAborted();
            return [];
          }
        }),
      );
      // The first host's reading of a bucket wins.
      const merged = new Map<string, BackendUsageWindow>();
      for (const [bucket_id, window] of readings.flat())
        if (!merged.has(bucket_id)) merged.set(bucket_id, window);
      return [...merged.values()];
    };
    let windows = await query('retrieveUserQuotaSummary', parse_summary_windows);
    if (!windows.length) windows = await query('fetchAvailableModels', parse_model_windows);
    if (windows.length)
      return {
        backend_key: parse_backend_instance_id('antigravity'),
        outcome: 'succeeded',
        detail: null,
        observed_at: this.options.now(),
        windows,
      };
    if (signed_out && !reached) return result('unauthenticated', SIGN_IN_MESSAGE);
    if (!reached) return result('failed', UNREACHABLE_MESSAGE);
    return result('unavailable', 'Antigravity did not return usable quota information.');
  }
}

function used_percent_of(quota: Record<string, unknown>): number | null {
  const remaining = quota.remainingFraction;
  if (typeof remaining !== 'number' || !Number.isFinite(remaining)) return null;
  return (1 - Math.max(0, Math.min(1, remaining))) * 100;
}

type Keyed = [string, BackendUsageWindow];

/**
 * A group's display name without its trailing "models": "Gemini Models" is Gemini, "Claude and
 * GPT models" is Claude and GPT, and "Gemini 4 Argon" stays as it is.
 */
function scope_label_of_group(display_name: unknown): string | null {
  if (typeof display_name !== 'string') return null;
  return display_name.trim().replace(/\s+models$/i, '') || null;
}
/** A bucket outside any named group, by its id: `gemini-5h` is Gemini, `3p-weekly` is Claude and GPT. */
function family_of_bucket(bucket_id: string): string {
  if (bucket_id.startsWith('3p-')) return 'Claude and GPT';
  if (bucket_id.startsWith('gemini-')) return 'Gemini';
  return bucket_id;
}
/** `retrieveUserQuotaSummary` buckets, keyed by bucket id. */
function parse_summary_windows(body: unknown): Keyed[] {
  if (!object(body)) return [];
  const groups: { name: string | null; buckets: unknown[] }[] = [];
  if (Array.isArray(body.groups))
    for (const group of body.groups)
      if (object(group) && Array.isArray(group.buckets))
        groups.push({ name: scope_label_of_group(group.displayName), buckets: group.buckets });
  if (!groups.some((group) => group.buckets.length) && Array.isArray(body.buckets))
    groups.splice(0, groups.length, { name: null, buckets: body.buckets });
  const windows: Keyed[] = [];
  for (const group of groups)
    for (const bucket of group.buckets) {
      if (!object(bucket) || bucket.disabled === true) continue;
      const bucket_id = bucket.bucketId;
      if (typeof bucket_id !== 'string' || !bucket_id) continue;
      const kind: BackendUsageWindowKind | null =
        bucket.window === '5h' ? 'five_hour' : bucket.window === 'weekly' ? 'seven_day' : null;
      const used_percent = used_percent_of(bucket);
      if (kind === null || used_percent === null) continue;
      // An untouched bucket names no reset. It stays, so the other host's reading of it
      // cannot replace it with a reset time this host does not report.
      const resets_at = parse_datetime(bucket.resetTime);
      const model_scope = group.name ?? family_of_bucket(bucket_id);
      windows.push([
        bucket_id,
        usage_window({ id: bucket_id, kind, used_percent, resets_at, model_scope }),
      ]);
    }
  return windows;
}

/** Older accounts expose only per-model weekly quotas. Quotas without a window length are skipped. */
function parse_model_windows(body: unknown): Keyed[] {
  if (!object(body) || !object(body.models)) return [];
  const windows = new Map<string, BackendUsageWindow>();
  for (const [model_id, model] of Object.entries(body.models)) {
    if (!object(model) || !object(model.weeklyQuotaInfo)) continue;
    const provider = model.modelProvider;
    const scope_label =
      provider === 'MODEL_PROVIDER_GOOGLE' || model_id.startsWith('gemini-')
        ? 'Gemini'
        : provider === 'MODEL_PROVIDER_ANTHROPIC' || model_id.startsWith('claude-')
          ? 'Claude'
          : provider === 'MODEL_PROVIDER_OPENAI' || model_id.startsWith('gpt-')
            ? 'GPT'
            : null;
    const used_percent = used_percent_of(model.weeklyQuotaInfo);
    const resets_at = parse_datetime(model.weeklyQuotaInfo.resetTime);
    if (scope_label === null || used_percent === null || resets_at === null) continue;
    const previous = windows.get(scope_label);
    if (previous === undefined || used_percent > previous.used_percent)
      windows.set(
        scope_label,
        usage_window({
          id: `${scope_label.toLowerCase()}-weekly`,
          kind: 'seven_day',
          used_percent,
          resets_at,
          model_scope: scope_label,
        }),
      );
  }
  return [...windows.entries()];
}
