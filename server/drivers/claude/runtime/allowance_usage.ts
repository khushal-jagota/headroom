/** Credential reads and an explicitly injected HTTP adapter are the only external doors. */
import { parse_backend_instance_id } from '../../core/identity.ts';
import { readFileSync } from 'node:fs';
import type {
  BackendUsageAdapter,
  BackendUsageResult,
  BackendUsageWindow,
  BackendUsageWindowKind,
  UsageHttpGet,
} from '../../../conversation/backend_usage/contracts.ts';
import { is_record as object } from '../../../core/records.ts';
import { parse_datetime, percentage } from '../../../conversation/backend_usage/logic/windows.ts';
const CLAUDE_USAGE_URL = 'https://api.anthropic.com/api/oauth/usage';
const CLAUDE_USAGE_TIMEOUT_SECONDS = 20;
const CLAUDE_USAGE_BETA = 'oauth-2025-04-20';
export function read_claude_access_token(
  path: string,
  now: Date,
): readonly [string | null, boolean] {
  let document: unknown;
  try {
    document = JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return [null, false];
  }
  if (document === null || typeof document !== 'object' || Array.isArray(document))
    return [null, false];
  const oauth = (document as Record<string, unknown>).claudeAiOauth;
  if (oauth === null || typeof oauth !== 'object' || Array.isArray(oauth)) return [null, false];
  const values = oauth as Record<string, unknown>;
  const token = values.accessToken;
  if (typeof token !== 'string' || !token) return [null, false];
  const expiry = typeof values.expiresAt === 'number' ? new Date(values.expiresAt) : null;
  if (
    expiry !== null &&
    Number.isFinite(expiry.getTime()) &&
    expiry.getUTCFullYear() >= 1 &&
    expiry.getUTCFullYear() <= 9999 &&
    expiry <= now
  )
    return [null, true];
  return [token, false];
}
export class ClaudeUsageAdapter implements BackendUsageAdapter {
  private readonly credential_path: string;
  private readonly http_get: UsageHttpGet;
  private readonly now: () => Date;
  constructor(options: { credential_path: string; http_get: UsageHttpGet; now: () => Date }) {
    this.credential_path = options.credential_path;
    this.http_get = options.http_get;
    this.now = options.now;
  }
  async refresh(options: { signal?: AbortSignal } = {}): Promise<BackendUsageResult> {
    options.signal?.throwIfAborted();
    const result = (
      outcome: BackendUsageResult['outcome'],
      detail: string,
    ): BackendUsageResult => ({
      backend_key: parse_backend_instance_id('claude'),
      outcome,
      detail,
      observed_at: null,
      windows: [],
    });
    const [access_token, token_expired] = read_claude_access_token(
      this.credential_path,
      this.now(),
    );
    if (access_token === null)
      return result(
        'unauthenticated',
        token_expired
          ? "Claude's login has expired. Run `claude auth login` and try again."
          : 'Claude is not logged in. Run `claude auth login` and try again.',
      );
    let response;
    try {
      response = await this.http_get(
        CLAUDE_USAGE_URL,
        { authorization: `Bearer ${access_token}`, 'anthropic-beta': CLAUDE_USAGE_BETA },
        CLAUDE_USAGE_TIMEOUT_SECONDS,
        options,
      );
    } catch {
      options.signal?.throwIfAborted();
      return result('failed', 'Claude usage could not be reached. Try again.');
    }
    if (response.status_code === 401 || response.status_code === 403)
      return result(
        'unauthenticated',
        "Claude's login is no longer valid. Run `claude auth login` and try again.",
      );
    if (response.status_code === 429)
      return result('failed', 'Claude usage is rate-limited. Try again later.');
    if (response.status_code < 200 || response.status_code >= 300)
      return result('failed', 'Claude usage could not be refreshed. Try again.');
    const windows = parse_claude_windows(response.body);
    if (windows === null)
      return result('failed', 'Claude returned usage in a format Panels does not understand.');
    return {
      backend_key: parse_backend_instance_id('claude'),
      outcome: 'succeeded',
      detail: null,
      observed_at: this.now(),
      windows,
    };
  }
}

function claude_reset_time(value: Record<string, unknown>): Date | null {
  for (const key of ['resets_at', 'reset_at']) {
    const parsed = parse_datetime(value[key]);
    if (parsed !== null) return parsed;
  }
  return null;
}
function claude_limit_entry(value: unknown): BackendUsageWindow | null {
  if (!object(value)) return null;
  const used_percent = percentage(value.percent) ?? percentage(value.utilization);
  const resets_at = claude_reset_time(value);
  if (used_percent === null || resets_at === null) return null;
  const scope = object(value.scope) ? value.scope : null;
  const model = object(scope?.model) ? scope.model : null;
  let model_name = model?.display_name;
  if (typeof model_name !== 'string' || !model_name) model_name = model?.displayName;
  let kind: BackendUsageWindowKind;
  let model_scope: string | null;
  if (typeof model_name === 'string' && model_name) {
    kind = 'seven_day';
    model_scope = model_name;
  } else {
    if (value.group === 'session') kind = 'five_hour';
    else if (
      typeof value.group === 'string' &&
      ['weekly', 'week', 'seven_day'].includes(value.group)
    )
      kind = 'seven_day';
    else return null;
    model_scope = null;
  }
  return { kind, used_percent, resets_at, model_scope, scope_label: null };
}

export function parse_claude_windows(body: unknown): readonly BackendUsageWindow[] | null {
  if (!object(body)) return null;
  const scoped = Array.isArray(body.limits)
    ? body.limits
        .map(claude_limit_entry)
        .filter((value): value is BackendUsageWindow => value !== null)
    : [];
  if (scoped.length) return scoped;
  const windows: BackendUsageWindow[] = [];
  for (const [key, value] of Object.entries(body)) {
    if (!object(value)) continue;
    const used_percent = percentage(value.utilization);
    const resets_at = claude_reset_time(value);
    if (used_percent === null || resets_at === null) continue;
    const suffix = key.startsWith('seven_day_') ? key.slice('seven_day_'.length) : key;
    const title = suffix
      .replaceAll('_', ' ')
      .replace(
        /\p{L}[\p{L}\p{M}]*/gu,
        (word) => word[0].toUpperCase() + word.slice(1).toLowerCase(),
      );
    windows.push({
      kind: key === 'five_hour' ? 'five_hour' : 'seven_day',
      used_percent,
      resets_at,
      model_scope: key === 'five_hour' || key === 'seven_day' ? null : title,
      scope_label: null,
    });
  }
  return windows.length ? windows : null;
}
