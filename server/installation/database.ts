/** The admitted candidate owns fresh database initialization, never its caller's checkout. */
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, isAbsolute, join } from 'node:path';
import type { ReleaseExecution } from '../environments/release/contracts.ts';
import { assert_release_execution, run_release_command } from '../environments/release/process.ts';
import { HOME_SERVICE_COMMAND_TIMEOUT_SECONDS } from './configuration.ts';

/** Run one database entry of the admitted app, in a private home with no caller settings. */
async function run_candidate_database_entry(
  app: string,
  entry: string,
  args: readonly string[],
  execution: ReleaseExecution,
  node_executable: string,
  owner_time_zone?: string,
): Promise<void> {
  assert_release_execution(execution);
  if ([app, ...args, node_executable].some((value) => !isAbsolute(value) || value.includes('\0')))
    throw new Error('Home database commands require absolute paths');
  const state = await mkdtemp(join(tmpdir(), 'panels-home-database-'));
  try {
    await run_release_command(
      [node_executable, join(app, 'server/installation', entry), ...args],
      {
        cwd: app,
        timeout_seconds: HOME_SERVICE_COMMAND_TIMEOUT_SECONDS,
        environment: {
          HOME: state,
          TMPDIR: state,
          PATH: dirname(node_executable) + ':/usr/local/bin:/usr/bin:/bin',
          LANG: 'C.UTF-8',
          ...(owner_time_zone === undefined
            ? {}
            : { PLAN_INSTALL_OWNER_TIME_ZONE: owner_time_zone }),
        },
      },
      execution,
    );
  } finally {
    // The shared command returns only after its actual child and capture cleanup settle.
    await rm(state, { recursive: true, force: true });
  }
}
export async function initialize_candidate_home_database(
  app: string,
  path: string,
  execution: ReleaseExecution,
  node_executable: string,
  machine_root?: string,
  owner_time_zone?: string,
): Promise<void> {
  await run_candidate_database_entry(
    app,
    'database_main.ts',
    [path, ...(machine_root ? [machine_root] : [])],
    execution,
    node_executable,
    owner_time_zone,
  );
}
/** A moved database must pass its integrity test and the installed app's schema admission. */
export async function check_moved_home_database(
  app: string,
  path: string,
  execution: ReleaseExecution,
  node_executable: string,
): Promise<void> {
  await run_candidate_database_entry(
    app,
    'move_database_main.ts',
    [path],
    execution,
    node_executable,
  );
}
