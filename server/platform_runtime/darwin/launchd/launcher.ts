/** The app is fixed; the Node and entrypoint belong to this Worker's release. */
import { access, readFile } from 'node:fs/promises';
import { constants } from 'node:fs';
import { WORKER_LAUNCHER_MODE, WORKER_LAUNCHER_FALLBACK_NOTICE } from './configuration.ts';
import type { WorkerJobStart } from './contracts.ts';

export async function usable_worker_launcher(path: string): Promise<string | null> {
  try {
    await access(path, constants.X_OK);
    return (await readFile(path)).includes(Buffer.from(`${WORKER_LAUNCHER_MODE}\0`)) ? path : null;
  } catch {
    return null;
  }
}

export async function start_worker_job<T>(options: WorkerJobStart<T>): Promise<T> {
  if (options.app !== null) {
    try {
      return await options.attempt([options.app, WORKER_LAUNCHER_MODE, ...options.command]);
    } catch {
      // The entry waits for explicit admission. It cannot start a provider in an
      // unsuccessful attempt, including the last instant before job removal.
      await options.remove_failed_attempt();
    }
  }
  try {
    options.notice(WORKER_LAUNCHER_FALLBACK_NOTICE);
  } catch {
    /* A warning cannot prevent bare Node startup. */
  }
  return options.attempt(options.command);
}
