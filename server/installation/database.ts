/** The admitted candidate owns fresh database initialization, never its caller's checkout. */
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, isAbsolute, join } from 'node:path';
import type { ReleaseExecution } from '../environments/release/contracts.ts';
import { assert_release_execution, run_release_command } from '../environments/release/process.ts';
import { HOME_SERVICE_COMMAND_TIMEOUT_SECONDS } from './configuration.ts';

export async function initialize_candidate_home_database(
  app: string,
  path: string,
  execution: ReleaseExecution,
  node_executable: string,
  machine_root?: string,
): Promise<void> {
  assert_release_execution(execution);
  if ([app, path, node_executable].some((value) => !isAbsolute(value) || value.includes('\0')))
    throw new Error('Fresh home database initialization requires absolute paths');
  const state = await mkdtemp(join(tmpdir(), 'panels-home-database-'));
  try {
    await run_release_command(
      [
        node_executable,
        join(app, 'server/installation/database_main.ts'),
        path,
        ...(machine_root ? [machine_root] : []),
      ],
      {
        cwd: app,
        timeout_seconds: HOME_SERVICE_COMMAND_TIMEOUT_SECONDS,
        environment: {
          HOME: state,
          TMPDIR: state,
          PATH: dirname(node_executable) + ':/usr/local/bin:/usr/bin:/bin',
          LANG: 'C.UTF-8',
        },
      },
      execution,
    );
  } finally {
    // The shared command returns only after its actual child and capture cleanup settle.
    await rm(state, { recursive: true, force: true });
  }
}
