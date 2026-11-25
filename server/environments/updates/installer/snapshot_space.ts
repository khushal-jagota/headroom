/** Check the actual backup filesystem before we stop the home. The writer can still run out later. */
import { lstat, mkdir, readdir, realpath, statfs } from 'node:fs/promises';
import { dirname, join } from 'node:path';

export async function require_snapshot_space(
  database: string,
  destination: string,
  read_space: (path: string) => Promise<{ bsize: bigint; bavail: bigint }> = (path) =>
    statfs(path, { bigint: true }),
): Promise<void> {
  await mkdir(destination, { recursive: true });
  const space = await read_space(destination);
  const block = space.bsize;
  if (block <= 0n) throw new Error('Cannot determine available backup space');
  const allocation = (bytes: bigint) => (1n + bytes / block) * block;
  let needed = 3n * block; // Snapshot directory and metadata.
  const add_entry = async (path: string): Promise<void> => {
    const entry = await lstat(path, { bigint: true });
    needed += allocation(entry.size);
    if (entry.isDirectory()) {
      for (const name of await readdir(path)) await add_entry(join(path, name));
    }
  };
  await add_entry(database);
  for (const path of [
    database + '-wal',
    join(dirname(database), 'skills'),
    join(dirname(database), 'worker-settings'),
  ]) {
    let root: string;
    try {
      root = await realpath(path);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') continue;
      throw error;
    }
    await add_entry(root);
  }
  if (space.bavail * block < needed)
    throw new Error(
      'The required snapshot cannot fit in the backup directory. The home is still running.',
    );
}
