/** Boot-only cleanup. The caller holds runtime ownership and admits no transfers yet. */
import { lstat, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { resolve_path_sync } from '../core/path_resolution.ts';

export async function clear_abandoned_executor_uploads(database_path: string): Promise<void> {
  const root = join(dirname(resolve_path_sync(database_path)), 'executor-uploads');
  let info;
  try {
    info = await lstat(root);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return;
    throw error;
  }
  if (!info.isDirectory() || info.isSymbolicLink())
    throw new Error('Executor upload area must be a directory, not a link');
  // No row points here. Before the first route starts, every prior upload is abandoned.
  await rm(root, { recursive: true });
}
