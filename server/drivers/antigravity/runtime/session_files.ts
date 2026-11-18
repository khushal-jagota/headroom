/** Removes what a throwaway agy session left in agy's state. agy 1.5.10 leaves the session
 * database and its brain directory, and writes no session metadata file. */
import { rm } from 'node:fs/promises';
import { join } from 'node:path';
import { AGY_APP_DATA_DIRECTORY_NAME } from './configuration.ts';

/** agy names its sessions with UUIDs. Anything else is not a session this module removes. */
const SESSION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Call after the agy process has stopped, with the id its own `session/new` returned. */
export async function remove_antigravity_session_files(
  home: string,
  session_id: string,
): Promise<void> {
  if (!SESSION_ID.test(session_id)) return;
  const state = join(home, AGY_APP_DATA_DIRECTORY_NAME);
  const database = join(state, 'conversations', `${session_id}.db`);
  await Promise.all([
    ...['', '-wal', '-shm', '-journal'].map((suffix) => rm(database + suffix, { force: true })),
    rm(join(state, 'brain', session_id), { recursive: true, force: true }),
  ]);
}
