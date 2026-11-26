/** The usage adapter's login reading, renewal and mapping need no network: the POST is recorded. */
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test } from 'vitest';
import { AntigravityUsageAdapter, type UsageHttpPost } from './allowance_usage.ts';

const NOW = new Date('2026-11-18T01:35:00Z');
const RESET = '2026-11-25T00:44:37Z';
const bucket = (
  bucketId: string,
  window: string,
  remainingFraction: number,
  resetTime: string | null = RESET,
) => ({
  bucketId,
  displayName: 'Limit Remaining',
  window,
  ...(resetTime === null ? {} : { resetTime }),
  remainingFraction,
});
/**
 * The groups `retrieveUserQuotaSummary` returned on the VPS on 18 November 2026. The daily host
 * names no reset for an untouched bucket, as it did on 26 November; the prod host names one.
 */
const summary = (gemini_weekly_remaining: number, untouched_reset: string | null = RESET) => ({
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
      buckets: [
        bucket('3p-weekly', 'weekly', 1, untouched_reset),
        bucket('3p-5h', '5h', 1, untouched_reset),
      ],
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

// Risk: the real quota groups map to the wrong windows, ids or labels, the prod host's reading
// replaces the daily host's (an untouched bucket without a reset took the prod host's reset
// time), or the request lacks the user agent the API requires.
test('the quota summary maps to Gemini, Claude and GPT, and Gemini 4 Argon windows', async () => {
  const requests: { url: string; headers: Readonly<Record<string, string>>; body: string }[] = [];
  const result = await adapter(login_file('cli-token').path, async (url, headers, body) => {
    requests.push({ url, headers, body });
    const daily = url.includes('daily');
    return { status_code: 200, body: daily ? summary(0.5, null) : summary(1) };
  }).refresh();
  expect(result.outcome).toBe('succeeded');
  const window = (
    id: string,
    used_percent: number,
    label: string,
    resets_at: Date | null = new Date(RESET),
  ) => ({
    id,
    kind: id.endsWith('5h') ? 'five_hour' : 'seven_day',
    label,
    used_percent,
    resets_at,
    window_duration_mins: id.endsWith('5h') ? 300 : 10080,
    model_scope: label.split(' · ')[1],
  });
  expect(result.windows).toEqual([
    window('gemini-weekly', 50, '7 days · Gemini'),
    window('gemini-5h', 25, '5 hours · Gemini'),
    window('3p-weekly', 0, '7 days · Claude and GPT', null),
    window('3p-5h', 0, '5 hours · Claude and GPT', null),
    window('gemini-argon-wtu-weekly', 75, '7 days · Gemini 4 Argon'),
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
