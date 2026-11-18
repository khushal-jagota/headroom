/** The usage adapter's login reading, renewal and mapping need no network: the POST is recorded. */
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test } from 'vitest';
import { AntigravityUsageAdapter, type UsageHttpPost } from './allowance_usage.ts';

const NOW = new Date('2026-11-18T01:35:00Z');
const RESET = '2026-11-25T00:44:37Z';
const bucket = (bucketId: string, window: string, remainingFraction: number) => ({
  bucketId,
  displayName: 'Limit Remaining',
  window,
  resetTime: RESET,
  remainingFraction,
});
/** The groups `retrieveUserQuotaSummary` returned on the VPS on 18 November 2026. */
const summary = (gemini_weekly_remaining: number) => ({
  groups: [
    {
      displayName: 'Gemini Models',
      buckets: [
        bucket('gemini-weekly', 'weekly', gemini_weekly_remaining),
        bucket('gemini-5h', '5h', 0.75),
      ],
    },
    {
      displayName: 'Claude and GPT models',
      buckets: [bucket('3p-weekly', 'weekly', 1), bucket('3p-5h', '5h', 1)],
    },
    {
      displayName: 'Gemini 4 Argon',
      buckets: [bucket('gemini-argon-wtu-weekly', 'weekly', 0.25)],
    },
  ],
});

const folders: string[] = [];
afterEach(() => {
  for (const folder of folders.splice(0)) rmSync(folder, { recursive: true, force: true });
});
/** A CLI login file whose access token expires at `expiry`, or none. */
function login_file(access_token: string | null, expiry = '2026-11-18T02:44:30.743865589+01:00') {
  const folder = mkdtempSync(join(tmpdir(), 'antigravity-usage-'));
  folders.push(folder);
  const path = join(folder, 'antigravity-oauth-token');
  const write = (token: string, at: string) =>
    writeFileSync(
      path,
      JSON.stringify({
        token: { access_token: token, token_type: 'Bearer', refresh_token: 'r', expiry: at },
        auth_method: 'consumer',
      }),
    );
  if (access_token !== null) write(access_token, expiry);
  return { path, write };
}
function adapter(
  path: string,
  post: UsageHttpPost,
  refresh_login: () => Promise<void> = async () => {
    throw new Error('this test does not renew the login');
  },
) {
  return new AntigravityUsageAdapter({
    login_path: path,
    http_post: post,
    refresh_login,
    now: () => NOW,
  });
}

// Risk: the real quota groups map to the wrong windows or labels, the prod host's untouched
// reading replaces the daily host's, or the request lacks the user agent the API requires.
test('the quota summary maps to Gemini, Claude and GPT, and Gemini 4 Argon windows', async () => {
  const requests: { url: string; headers: Readonly<Record<string, string>>; body: string }[] = [];
  const result = await adapter(login_file('cli-token').path, async (url, headers, body) => {
    requests.push({ url, headers, body });
    return { status_code: 200, body: summary(url.includes('daily') ? 0.5 : 1) };
  }).refresh();
  expect(result.outcome).toBe('succeeded');
  const resets_at = new Date(RESET);
  const window = (kind: string, used_percent: number, scope_label: string) => ({
    kind,
    used_percent,
    resets_at,
    model_scope: null,
    scope_label,
  });
  expect(result.windows).toEqual([
    window('seven_day', 50, 'Gemini'),
    window('five_hour', 25, 'Gemini'),
    window('seven_day', 0, 'Claude and GPT'),
    window('five_hour', 0, 'Claude and GPT'),
    window('seven_day', 75, 'Gemini 4 Argon'),
  ]);
  expect(requests.map((request) => request.url)).toEqual([
    'https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary',
    'https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary',
  ]);
  expect(requests[0]).toMatchObject({
    headers: { authorization: 'Bearer cli-token' },
    body: '{}',
  });
  expect(requests[0]!.headers['user-agent']).toMatch(
    /^antigravity\/hub\/2\.24\.0 \(aidev_client; os_type=\w+; arch=\w+\)$/,
  );
});

// Risk: a signed-out home reaches the network or asks agy to renew a login that does not exist.
test('a missing CLI login is signed out and reaches neither the network nor agy', async () => {
  let renewals = 0;
  const result = await adapter(
    login_file(null).path,
    async () => {
      throw new Error('no network');
    },
    async () => {
      renewals++;
    },
  ).refresh();
  expect(result).toMatchObject({
    outcome: 'unauthenticated',
    detail: 'Antigravity is signed out. Sign in to Antigravity again.',
  });
  expect(renewals).toBe(0);
});

// Risk: an expired access token is sent as it is, or agy's renewal is not read back, or a login
// agy could not renew reads as anything but signed out.
test('an expired token is renewed by agy and read again; one agy cannot renew is signed out', async () => {
  const login = login_file('old-token', '2026-11-18T01:35:30Z');
  const bearers: string[] = [];
  const post: UsageHttpPost = async (_url, headers) => {
    bearers.push(headers.authorization!);
    return { status_code: 200, body: summary(0.5) };
  };
  const renewed = await adapter(login.path, post, async () =>
    login.write('new-token', '2026-11-18T02:35:00Z'),
  ).refresh();
  expect(renewed.outcome).toBe('succeeded');
  expect(new Set(bearers)).toEqual(new Set(['Bearer new-token']));

  bearers.length = 0;
  login.write('old-token', '2026-11-18T01:35:30Z');
  const not_renewed = await adapter(login.path, post, async () => {}).refresh();
  expect(not_renewed).toMatchObject({
    outcome: 'unauthenticated',
    detail: 'Antigravity is signed out. Sign in to Antigravity again.',
  });
  expect(bearers).toEqual([]);
});
