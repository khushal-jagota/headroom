/** The usage adapter reads agy's `/usage` JSON and the account email from agy's own log. */
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test } from 'vitest';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import {
  AntigravityUsageAdapter,
  antigravity_usage_runner,
  read_antigravity_account_email,
} from './allowance_usage.ts';

const NOW = new Date('2026-11-26T21:00:00Z');
const GEMINI_WEEKLY_RESET = '2026-12-03T21:12:16Z';
const GEMINI_5H_RESET = '2026-11-27T02:12:16Z';
const EPOCH = '1970-01-01T00:00:00Z';
const bucket = (id: string, window: string, remaining_fraction: number, reset_time: string) => ({
  id,
  name: 'Limit Remaining',
  window,
  remaining_fraction,
  reset_time,
});
/** What `agy -p "/usage" --output-format json` printed on Mac 1 (agy 1.5.18), with Gemini used. */
const USAGE_OUTPUT = JSON.stringify({
  status: 'SUCCESS',
  response: 'ignored table text',
  usage: { total_tokens: 0 },
  command: {
    name: 'usage',
    data: {
      groups: [
        {
          name: 'Gemini Models',
          buckets: [
            bucket('gemini-weekly', 'weekly', 0.5, GEMINI_WEEKLY_RESET),
            bucket('gemini-5h', '5h', 0.75, GEMINI_5H_RESET),
          ],
        },
        {
          name: 'Claude and GPT models',
          buckets: [bucket('3p-weekly', 'weekly', 1, EPOCH), bucket('3p-5h', '5h', 1, EPOCH)],
        },
      ],
    },
  },
});

const folders: string[] = [];
const scratch = () => {
  const folder = mkdtempSync(join(tmpdir(), 'antigravity-usage-'));
  folders.push(folder);
  return folder;
};
afterEach(() => {
  for (const folder of folders.splice(0)) rmSync(folder, { recursive: true, force: true });
});
const adapter = (run_usage: () => Promise<string>) =>
  new AntigravityUsageAdapter({ run_usage, now: () => NOW });

// Risk: the `/usage` JSON maps to the wrong windows, ids or labels, or an untouched bucket's
// 1970-01-01 reset shows as a real reset time (a countdown to the Unix epoch).
test('the /usage output maps to Gemini and Claude and GPT windows; the epoch reset is no reset', async () => {
  const result = await adapter(async () => USAGE_OUTPUT).refresh();
  expect(result).toMatchObject({ outcome: 'succeeded', detail: null, observed_at: NOW });
  const window = (id: string, used_percent: number, label: string, resets_at: string | null) => ({
    id,
    kind: id.endsWith('5h') ? 'five_hour' : 'seven_day',
    label,
    used_percent,
    resets_at: resets_at === null ? null : new Date(resets_at),
    window_duration_mins: id.endsWith('5h') ? 300 : 10080,
    model_scope: label.split(' · ')[1],
  });
  expect(result.windows).toEqual([
    window('gemini-weekly', 50, '7 days · Gemini', GEMINI_WEEKLY_RESET),
    window('gemini-5h', 25, '5 hours · Gemini', GEMINI_5H_RESET),
    window('3p-weekly', 0, '7 days · Claude and GPT', null),
    window('3p-5h', 0, '5 hours · Claude and GPT', null),
  ]);
});

// Risk: output that is not a usage table reads as a success with no windows, or as a failure that
// hides the difference between agy not running and agy answering without limits.
test('a failed run is failed and an answer without limits is unavailable', async () => {
  expect(
    await adapter(async () => {
      throw new Error('agy is missing');
    }).refresh(),
  ).toMatchObject({ outcome: 'failed', windows: [] });
  expect(await adapter(async () => 'not json').refresh()).toMatchObject({
    outcome: 'unavailable',
    windows: [],
  });
  expect(
    await adapter(async () =>
      JSON.stringify({ status: 'SUCCESS', command: { data: {} } }),
    ).refresh(),
  ).toMatchObject({ outcome: 'unavailable', windows: [] });
});

// Risk: the runner starts agy with other arguments or another home than the usage command on
// agy's own login, or a missing agy escapes as an exception instead of a failed reading. A real
// child runs here, because the spawn boundary is what this proves.
test('the runner runs agy -p /usage on the agy home, and a missing agy fails', async () => {
  const folder = scratch();
  const agy = join(folder, 'agy');
  writeFileSync(
    agy,
    `#!/bin/sh\n[ "$*" = '-p /usage --output-format json' ] && [ "$GEMINI_HOME" = '${folder}' ] && cat '${join(folder, 'usage.json')}'\n`,
  );
  chmodSync(agy, 0o755);
  writeFileSync(join(folder, 'usage.json'), USAGE_OUTPUT);
  const children = new OneShotChildren();
  const options = { home: folder, panels_server_url: 'http://127.0.0.1:1' };
  const found = adapter(antigravity_usage_runner(options, () => agy, children));
  expect((await found.refresh()).outcome).toBe('succeeded');
  const missing = adapter(antigravity_usage_runner(options, () => join(folder, 'nope'), children));
  expect((await missing.refresh()).outcome).toBe('failed');
  children.seal();
  await children.settled();
});

function acp_log(directory: string, name: string, text: string) {
  mkdirSync(directory, { recursive: true });
  const path = join(directory, name);
  writeFileSync(path, text);
}
const SIGNED_IN = (email: string) =>
  `I1126 21:11:59.935010 44 server_oauth.go:226] OAuth: authenticated successfully as ${email}\n`;

// Risk: the card names no account (Usage cannot merge the account across machines), names a
// stale account from an older log, or reads an unbounded file. A missing or changed line is null.
test('the account email is the newest ACP log’s sign-in line, or null', () => {
  const logs = join(scratch(), 'log');
  expect(read_antigravity_account_email(logs)).toBeNull();
  acp_log(logs, 'acp-server-20261125_010000-1.log', SIGNED_IN('old@example.com'));
  acp_log(logs, 'cli-20261126_210000.log', SIGNED_IN('cli@example.com'));
  expect(read_antigravity_account_email(logs)).toBe('old@example.com');
  acp_log(logs, 'acp-server-20261126_211159-2.log', SIGNED_IN('owner@example.com'));
  expect(read_antigravity_account_email(logs)).toBe('owner@example.com');
  acp_log(logs, 'acp-server-20261126_220000-3.log', 'I1126 server started, no sign-in line\n');
  expect(read_antigravity_account_email(logs)).toBeNull();
});
