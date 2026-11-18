/** Credential reads and explicitly injected HTTP functions are the only external doors. */
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
import { parse_datetime } from '../../../conversation/backend_usage/logic/windows.ts';

const TOKEN_URL = 'https://oauth2.googleapis.com/token';
const DAILY_HOST = 'https://daily-cloudcode-pa.googleapis.com';
const PROD_HOST = 'https://cloudcode-pa.googleapis.com';
// Personal sign-in is metered on the daily host first. Panels signs in personally only.
const QUOTA_HOSTS = [DAILY_HOST, PROD_HOST];
const ANTIGRAVITY_USAGE_TIMEOUT_SECONDS = 20;
const TOKEN_REUSE_MARGIN_MS = 60_000;
const SIGN_IN_MESSAGE = 'Antigravity is not signed in. Sign in to Antigravity from Panels.';

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

interface AntigravityCredential {
  readonly client_id: string;
  readonly client_secret: string;
  readonly refresh_token: string;
  readonly project_id: string | null;
}

export function read_antigravity_credential(path: string): AntigravityCredential | null {
  let document: unknown;
  try {
    document = JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
  if (!object(document)) return null;
  const { client_id, client_secret, refresh_token, project_id } = document;
  if (
    typeof client_id !== 'string' ||
    !client_id ||
    typeof client_secret !== 'string' ||
    !client_secret ||
    typeof refresh_token !== 'string' ||
    !refresh_token
  )
    return null;
  return {
    client_id,
    client_secret,
    refresh_token,
    project_id: typeof project_id === 'string' && project_id ? project_id : null,
  };
}

function antigravity_user_agent(): string {
  return `antigravity/hub/2.24.0 (aidev_client; os_type=${process.platform}; arch=${process.arch}; cl=963137146)`;
}

class SignInRequired extends Error {}

export class AntigravityUsageAdapter implements BackendUsageAdapter {
  private readonly credential_path: string;
  private readonly http_post: UsageHttpPost;
  private readonly now: () => Date;
  private cached_token: { key: string; access_token: string; expires_at_ms: number } | null = null;
  constructor(options: { credential_path: string; http_post: UsageHttpPost; now: () => Date }) {
    this.credential_path = options.credential_path;
    this.http_post = options.http_post;
    this.now = options.now;
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
    const credential = read_antigravity_credential(this.credential_path);
    if (credential === null) {
      this.cached_token = null;
      return result('unauthenticated', SIGN_IN_MESSAGE);
    }
    let access_token: string;
    try {
      access_token = await this.access_token(credential, options);
    } catch (error) {
      options.signal?.throwIfAborted();
      if (error instanceof SignInRequired) return result('unauthenticated', error.message);
      return result('failed', 'Antigravity usage could not be reached. Try again.');
    }
    let reached = false;
    let signed_out = false;
    const query = async (operation: string, parse: (body: unknown) => Keyed[]) => {
      const readings = await Promise.all(
        QUOTA_HOSTS.map(async (host) => {
          try {
            const response = await this.http_post(
              `${host}/v1internal:${operation}`,
              {
                authorization: `Bearer ${access_token}`,
                'content-type': 'application/json',
                'user-agent': antigravity_user_agent(),
              },
              JSON.stringify(credential.project_id ? { project: credential.project_id } : {}),
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
        observed_at: this.now(),
        windows,
      };
    if (signed_out && !reached) {
      this.cached_token = null;
      return result('unauthenticated', SIGN_IN_MESSAGE);
    }
    if (!reached) return result('failed', 'Antigravity usage could not be reached. Try again.');
    return result('unavailable', 'Antigravity did not return usable quota information.');
  }

  private async access_token(
    credential: AntigravityCredential,
    options: { signal?: AbortSignal },
  ): Promise<string> {
    const key = `${credential.client_id}\0${credential.refresh_token}`;
    const now_ms = this.now().getTime();
    const cached = this.cached_token;
    if (
      cached !== null &&
      cached.key === key &&
      cached.expires_at_ms > now_ms + TOKEN_REUSE_MARGIN_MS
    )
      return cached.access_token;
    this.cached_token = null;
    const response = await this.http_post(
      TOKEN_URL,
      { 'content-type': 'application/x-www-form-urlencoded' },
      new URLSearchParams({
        client_id: credential.client_id,
        client_secret: credential.client_secret,
        refresh_token: credential.refresh_token,
        grant_type: 'refresh_token',
      }).toString(),
      ANTIGRAVITY_USAGE_TIMEOUT_SECONDS,
      options,
    );
    const body = object(response.body) ? response.body : {};
    if (
      response.status_code === 401 ||
      (response.status_code === 400 && body.error === 'invalid_grant')
    )
      throw new SignInRequired(
        'Antigravity sign-in has expired. Sign in to Antigravity from Panels.',
      );
    const { access_token, expires_in } = body;
    if (
      response.status_code < 200 ||
      response.status_code >= 300 ||
      typeof access_token !== 'string' ||
      !access_token ||
      typeof expires_in !== 'number' ||
      !(expires_in > 0)
    )
      throw new Error('token refresh failed');
    this.cached_token = { key, access_token, expires_at_ms: now_ms + expires_in * 1000 };
    return access_token;
  }
}

function used_percent_of(quota: Record<string, unknown>): number | null {
  const remaining = quota.remainingFraction;
  if (typeof remaining !== 'number' || !Number.isFinite(remaining)) return null;
  return (1 - Math.max(0, Math.min(1, remaining))) * 100;
}

type Keyed = [string, BackendUsageWindow];

/** `retrieveUserQuotaSummary` buckets, keyed by bucket id. */
function parse_summary_windows(body: unknown): Keyed[] {
  if (!object(body)) return [];
  const groups: { name: string | null; buckets: unknown[] }[] = [];
  if (Array.isArray(body.groups))
    for (const group of body.groups)
      if (object(group) && Array.isArray(group.buckets))
        groups.push({
          name: typeof group.displayName === 'string' ? group.displayName.trim() || null : null,
          buckets: group.buckets,
        });
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
      const resets_at = parse_datetime(bucket.resetTime);
      if (kind === null || used_percent === null || resets_at === null) continue;
      // Argon and Carbon are metered separately from the default Gemini buckets.
      const scope_label = bucket_id.startsWith('gemini-argon-')
        ? (group.name ?? 'Gemini Argon')
        : bucket_id.startsWith('gemini-carbon-')
          ? (group.name ?? 'Gemini Carbon')
          : bucket_id.startsWith('gemini-')
            ? 'Gemini'
            : bucket_id.startsWith('3p-')
              ? 'Claude and GPT'
              : (group.name ?? bucket_id);
      windows.push([bucket_id, { kind, used_percent, resets_at, model_scope: null, scope_label }]);
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
      windows.set(scope_label, {
        kind: 'seven_day',
        used_percent,
        resets_at,
        model_scope: null,
        scope_label,
      });
  }
  return [...windows.entries()];
}
