/** Credential reads and an explicitly injected HTTP adapter are the only external doors. */
import { readFileSync } from 'node:fs';
import type { BackendUsageAdapter, BackendUsageResult, UsageHttpGet } from './contracts.ts';
import { parse_claude_windows } from './logic/windows.ts';
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
  async refresh(): Promise<BackendUsageResult> {
    const result = (
      outcome: BackendUsageResult['outcome'],
      detail: string,
    ): BackendUsageResult => ({
      backend_key: 'claude',
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
      );
    } catch {
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
      backend_key: 'claude',
      outcome: 'succeeded',
      detail: null,
      observed_at: this.now(),
      windows,
    };
  }
}
