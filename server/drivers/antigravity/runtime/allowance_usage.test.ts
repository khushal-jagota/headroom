/** The usage adapter's mapping and token reuse need no network: the POST function is recorded. */
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { AntigravityUsageAdapter, type UsageHttpPost } from './allowance_usage.ts';

const NOW = new Date('2026-11-17T10:00:00Z');
const bucket = (bucketId: string, window: string, remainingFraction: number, extra = {}) => ({
  bucketId,
  window,
  remainingFraction,
  resetTime: '2026-11-17T12:30:00Z',
  ...extra,
});
const SUMMARY = {
  groups: [
    {
      displayName: 'Gemini',
      buckets: [
        bucket('gemini-5h', '5h', 0.75),
        bucket('gemini-weekly', 'weekly', 0.5),
        bucket('gemini-off-5h', '5h', 0.1, { disabled: true }),
      ],
    },
    {
      displayName: 'Claude and GPT',
      buckets: [bucket('3p-5h', '5h', 1), bucket('3p-daily', 'daily', 0.2)],
    },
  ],
};

function credential(content: string | null): string {
  const path = join(mkdtempSync(join(tmpdir(), 'antigravity-usage-')), 'acp_token.json');
  if (content !== null) writeFileSync(path, content);
  return path;
}
const SIGNED_IN = JSON.stringify({ client_id: 'id', client_secret: 'secret', refresh_token: 'rt' });

function adapter(path: string, post: UsageHttpPost) {
  return new AntigravityUsageAdapter({ credential_path: path, http_post: post, now: () => NOW });
}

test('quota summary maps to windows by family and window, skipping disabled and unknown buckets', async () => {
  const urls: string[] = [];
  const result = await adapter(credential(SIGNED_IN), async (url) => {
    urls.push(url);
    if (url.includes('oauth2'))
      return { status_code: 200, body: { access_token: 't', expires_in: 3600 } };
    return { status_code: 200, body: SUMMARY };
  }).refresh();
  expect(result.outcome).toBe('succeeded');
  expect(result.windows).toEqual([
    {
      kind: 'five_hour',
      used_percent: 25,
      resets_at: new Date('2026-11-17T12:30:00Z'),
      model_scope: null,
      scope_label: 'Gemini',
    },
    {
      kind: 'seven_day',
      used_percent: 50,
      resets_at: new Date('2026-11-17T12:30:00Z'),
      model_scope: null,
      scope_label: 'Gemini',
    },
    {
      kind: 'five_hour',
      used_percent: 0,
      resets_at: new Date('2026-11-17T12:30:00Z'),
      model_scope: null,
      scope_label: 'Claude and GPT',
    },
  ]);
  expect(urls.filter((url) => url.includes('retrieveUserQuotaSummary'))).toEqual([
    'https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary',
    'https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary',
  ]);
});

test('a missing credential file is unauthenticated and never reaches the network', async () => {
  const result = await adapter(credential(null), async () => {
    throw new Error('no network');
  }).refresh();
  expect(result.outcome).toBe('unauthenticated');
  expect(result.detail).toContain('Sign in to Antigravity from Panels');
});

test('invalid_grant from the token endpoint is unauthenticated', async () => {
  const result = await adapter(credential(SIGNED_IN), async () => ({
    status_code: 400,
    body: { error: 'invalid_grant' },
  })).refresh();
  expect(result.outcome).toBe('unauthenticated');
  expect(JSON.stringify(result)).not.toContain('secret');
});

test('the access token is reused across refreshes until near expiry', async () => {
  let token_posts = 0;
  const refreshing = adapter(credential(SIGNED_IN), async (url) => {
    if (url.includes('oauth2')) {
      token_posts++;
      return { status_code: 200, body: { access_token: 't', expires_in: 3600 } };
    }
    return { status_code: 200, body: SUMMARY };
  });
  await refreshing.refresh();
  await refreshing.refresh();
  expect(token_posts).toBe(1);
});
