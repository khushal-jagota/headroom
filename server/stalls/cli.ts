/** One-shot command for Unit 21's timer and Unit 20's CLI dispatcher. */
import { statSync } from 'node:fs';
import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';
import { loadConfig } from '../core/config.ts';
import { assert_test_database_path } from '../core/test_paths.ts';
import { backend_io_is_test } from '../conversation/snapshot/environment.ts';
import { load_apns_config } from '../notifications/config.ts';
import { message_chief } from './alarms.ts';
import { run_watchdog, watchdog_summary } from './watchdog.ts';

export interface WatchdogArguments {
  readonly db: string;
  readonly state_dir: string;
}
export async function watchdog_command(
  args: readonly string[],
  env: NodeJS.ProcessEnv = process.env,
): Promise<string> {
  const { values } = parseArgs({
    args: [...args],
    options: {
      db: { type: 'string' },
      'state-dir': { type: 'string' },
    },
    strict: true,
  });
  const db_path = values.db;
  const state_dir = values['state-dir'];
  if (!db_path) throw new Error('Missing option --db');
  if (!state_dir) throw new Error('Missing option --state-dir');
  return watchdog_handler({ db: db_path, state_dir }, env);
}
export async function watchdog_handler(
  args: WatchdogArguments,
  env: NodeJS.ProcessEnv,
  home?: string,
): Promise<string> {
  const admit_path = (path: string) => assert_test_database_path(path, env);
  const db_path = admit_path(args.db),
    state_dir = admit_path(args.state_dir);
  if (!statSync(db_path).isFile()) throw new Error('--db must name an existing database file');
  const config = loadConfig(undefined, env, { home, admit_path });
  // A test-mode command must never instantiate a provider transport.
  if (config.test_mode || backend_io_is_test())
    throw new Error('watchdog provider command is unavailable in test mode');
  const { StandardsWebPushAdapter } = await import('../notifications/web_push.ts');
  const { TokenAPNsAdapter, canonical_server_origin } = await import('../notifications/apns.ts');
  const origin = canonical_server_origin(config.trusted_ingress_canonical_origin);
  const apns_config = load_apns_config(env);
  const apns = apns_config && origin ? new TokenAPNsAdapter(apns_config) : null;
  try {
    const run = await run_watchdog(db_path, state_dir, {
      now_ms: Date.now(),
      web_push: new StandardsWebPushAdapter(),
      apns,
      push_origin: origin,
      message_chief: (text) => message_chief(text, `http://127.0.0.1:${config.port}`),
    });
    return watchdog_summary(run);
  } finally {
    await apns?.close();
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  let args = process.argv.slice(2);
  if (args[0] === 'stalls') args = args.slice(1);
  if (args[0] !== 'watchdog') {
    console.error('Usage: stalls watchdog --db PATH --state-dir PATH');
    process.exitCode = 2;
  } else {
    try {
      console.log(await watchdog_command(args.slice(1)));
    } catch (error) {
      console.error((error as Error).message);
      process.exitCode = 1;
    }
  }
}
