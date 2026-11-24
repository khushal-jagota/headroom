/** Boot-only cleanup. The caller holds runtime ownership and admits no transfers yet. */
import { lstat, mkdir, mkdtemp, rename } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { resolve_path_sync } from '../core/path_resolution.ts';

export async function clear_abandoned_executor_uploads(database_path: string): Promise<void> {
  const server_root = dirname(resolve_path_sync(database_path));
  const root = join(server_root, 'executor-uploads');
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
  const archive = join(server_root, 'executor-upload-archive');
  try {
    await mkdir(archive, { mode: 0o700 });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
  }
  const archive_info = await lstat(archive);
  if (!archive_info.isDirectory() || archive_info.isSymbolicLink())
    throw new Error('Executor upload archive must be a directory, not a link');
  // Rename preserves every byte without a copy or deletion. EXDEV refuses a cross-disk move.
  const destination = await mkdtemp(join(archive, 'startup-'));
  await rename(root, destination);
}
