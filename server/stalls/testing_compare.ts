/** Test-only direct comparison driver. Every external effect is a recording callback. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { createDatabase } from '../core/database/index.ts';
import type { APNsAdapter, WebPushAdapter } from '../notifications/contracts.ts';
import { StallChecker } from './checker.ts';
import type { TrackedWait } from './contracts.ts';
import { ProcessWaitRegistry } from './registry.ts';
import { run_watchdog, WATCHDOG_STATE_FILE_NAME, watchdog_summary } from './watchdog.ts';

interface Scenario {
  name: string;
  now_ms: number;
  waits: TrackedWait[];
  muted: boolean;
  check: boolean;
}
const input = JSON.parse(readFileSync(0, 'utf8')) as {
  db_path: string;
  state_dir: string;
  steps: Scenario[];
};
if (!input.db_path || !input.state_dir) throw new Error('comparison requires explicit test paths');
const database = createDatabase(input.db_path);
let now_ms = 0;
let waits: TrackedWait[] = [];
const checker = new StallChecker(
  database,
  new ProcessWaitRegistry(() => now_ms),
  () => [{ tracked_waits: () => waits }],
  () => now_ms,
);
const results: unknown[] = [];
try {
  for (const step of input.steps) {
    now_ms = step.now_ms;
    waits = step.waits;
    database.write((sql) => {
      sql.run(
        `UPDATE notification_preferences SET enabled=?
        WHERE subject_key='panels' AND notification_type='stall_alarm'`,
        [Number(!step.muted)],
      );
    });
    if (step.check) checker.check_once();
    const sends: unknown[] = [];
    const chief: string[] = [];
    const web_push: WebPushAdapter = {
      async send(subscription, intent, identity, options) {
        sends.push({ transport: 'web', subscription, intent, identity, subject: options.subject });
        return { delivered: true, expired: false, error: null };
      },
    };
    const apns: APNsAdapter = {
      async send(delivery, options) {
        sends.push({ transport: 'apns', delivery, ...options });
        return {
          delivered: true,
          dead_token: false,
          retryable: false,
          error: null,
          invalidated_at_ms: null,
        };
      },
      close() {},
    };
    const run = await run_watchdog(input.db_path, input.state_dir, {
      now_ms,
      web_push,
      apns,
      push_origin: 'https://stalls.comparison.invalid',
      message_chief(text) {
        chief.push(text);
        return true;
      },
    });
    results.push({
      name: step.name,
      run,
      summary: watchdog_summary(run),
      sends,
      chief,
      state: JSON.parse(readFileSync(join(input.state_dir, WATCHDOG_STATE_FILE_NAME), 'utf8')),
      rows: database.read((sql) => ({
        findings: sql.all('SELECT * FROM stall_findings ORDER BY finding_key'),
        heartbeat: sql.all('SELECT * FROM stall_checker_heartbeat ORDER BY singleton'),
      })),
    });
  }
  process.stdout.write(JSON.stringify(results));
} finally {
  await checker.close();
  database.close();
}
