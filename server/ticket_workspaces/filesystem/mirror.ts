import { createHash } from 'node:crypto';
import { closeSync, constants, futimesSync, openSync } from 'node:fs';
import { constants as os_constants } from 'node:os';
import { mkdir, readdir, rename, rm, stat } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { flock } from '../../core/locks/flock.ts';
import { MIRROR_RETENTION_SECONDS } from '../contracts.ts';
import type { WorkspaceOptions } from '../contracts.ts';
import { entry_stat, git, token } from './shared.ts';

/** Project repository mirrors live in the cache folder, which Panels does not back up or sync. */
export const git_mirror_root = (db_path: string) => join(dirname(db_path), 'cache', 'git-mirrors');

const REFSPEC = ['+refs/heads/*:refs/heads/*', '--prune'];

function mirror_name(repository_url: string): string {
  return createHash('sha256').update(repository_url).digest('hex').slice(0, 24);
}
function open_lock(path: string): number {
  return openSync(path, constants.O_RDWR | constants.O_CREAT | constants.O_NOFOLLOW, 0o600);
}
async function refresh(mirror: string, repository_url: string, options: WorkspaceOptions) {
  if (await entry_stat(mirror)) {
    const fetched = await git(mirror, ['fetch', '--no-tags', 'origin', ...REFSPEC], options);
    if (!fetched.returncode) return true;
    await rm(mirror, { recursive: true, force: true });
  }
  const partial = `${mirror}.${token()}.tmp`;
  try {
    for (const args of [
      ['init', '--bare', '--', partial],
      ['-C', partial, 'remote', 'add', 'origin', repository_url],
    ]) {
      if ((await git(dirname(mirror), args, options)).returncode) return false;
    }
    if ((await git(partial, ['fetch', '--no-tags', 'origin', ...REFSPEC], options)).returncode)
      return false;
    await rename(partial, mirror);
    return true;
  } finally {
    await rm(partial, { recursive: true, force: true });
  }
}
/**
 * Run `use` with a freshly fetched local mirror of the repository, or with null when the mirror
 * cannot be made. The mirror lock stays held while `use` runs, so cleanup cannot remove the mirror
 * under a clone. The mirror only saves network time, so every mirror failure means null.
 */
export async function with_mirror<T>(
  repository_url: string,
  options: WorkspaceOptions,
  use: (mirror: string | null) => Promise<T>,
): Promise<T> {
  if (options.mirror_root === undefined) return use(null);
  let descriptor: number | null = null;
  let mirror: string | null = null;
  try {
    await mkdir(options.mirror_root, { recursive: true });
    const name = mirror_name(repository_url);
    descriptor = open_lock(join(options.mirror_root, `${name}.lock`));
    while (flock(descriptor, 'try-exclusive')) {
      options.signal?.throwIfAborted();
      await sleep(500);
    }
    const path = join(options.mirror_root, `${name}.git`);
    if (await refresh(path, repository_url, options)) {
      mirror = path;
      futimesSync(descriptor, new Date(), new Date());
    }
  } catch {
    options.signal?.throwIfAborted();
    mirror = null;
  }
  try {
    return await use(mirror);
  } finally {
    if (descriptor !== null) closeSync(descriptor);
  }
}
/** Remove mirrors unused for 30 days and orphaned partial mirrors. Skip any mirror in use. */
export async function clean_mirrors(now: number, options: WorkspaceOptions): Promise<string[]> {
  const root = options.mirror_root;
  if (root === undefined || !(await entry_stat(root))) return [];
  const removed: string[] = [];
  const names = await readdir(root);
  for (const lock of names.filter((name) => name.endsWith('.lock'))) {
    const name = lock.slice(0, -'.lock'.length);
    const descriptor = open_lock(join(root, lock));
    try {
      const error = flock(descriptor, 'try-exclusive');
      if (error === os_constants.errno.EAGAIN || error === os_constants.errno.EWOULDBLOCK) continue;
      if (error) throw new Error('Cannot lock Project mirror');
      for (const entry of names.filter((entry) => entry.startsWith(`${name}.git.`)))
        if (entry.endsWith('.tmp')) await rm(join(root, entry), { recursive: true, force: true });
      const mirror = join(root, `${name}.git`);
      const used = (await stat(join(root, lock))).mtimeMs / 1000;
      if ((await entry_stat(mirror)) && now - used >= MIRROR_RETENTION_SECONDS) {
        await rm(mirror, { recursive: true, force: true });
        removed.push(mirror);
      }
    } finally {
      closeSync(descriptor);
    }
  }
  return removed;
}
