/** Immutable releases share a filesystem with the service's stable app link. */
import { lstatSync, readlinkSync, renameSync, symlinkSync, unlinkSync } from 'node:fs';
import { basename, join } from 'node:path';
import { fsync_directory_sync } from '../../../core/fsync.ts';
import { exchange_names } from '../../../core/locks/flock.ts';

export function release_path(root: string, sha: string): string {
  if (!/^[0-9a-f]{40}$/.test(sha)) throw new Error('Invalid installed release revision');
  return join(root, '.release-' + sha);
}
export function selected_release(root: string): string {
  const app = join(root, 'app');
  if (!lstatSync(app, { throwIfNoEntry: false })?.isSymbolicLink()) return app;
  const target = readlinkSync(app);
  if (!/^\.release-[0-9a-f]{40}$/.test(target) || !lstatSync(join(root, target)).isDirectory())
    throw new Error('Invalid installed release selection');
  return join(root, target);
}
/** Before the exchange the link names itself. After the exchange it names the old directory. */
export function pin_existing(root: string, sha: string): string {
  const destination = release_path(root, sha);
  if (selected_release(root) === destination) return destination;
  const app = join(root, 'app');
  if (!lstatSync(app).isDirectory()) throw new Error('The selected release changed');
  const existing = lstatSync(destination, { throwIfNoEntry: false });
  if (!existing) symlinkSync(basename(destination), destination);
  else if (!existing.isSymbolicLink() || readlinkSync(destination) !== basename(destination))
    throw new Error('The previous release path already exists');
  exchange_names(app, destination);
  fsync_directory_sync(root);
  return destination;
}
export function select_release(root: string, sha: string): void {
  const destination = release_path(root, sha);
  if (!lstatSync(destination).isDirectory()) throw new Error('Missing retained release');
  const next = join(root, '.app-select');
  if (lstatSync(next, { throwIfNoEntry: false })?.isSymbolicLink()) unlinkSync(next);
  symlinkSync(basename(destination), next);
  renameSync(next, join(root, 'app'));
  fsync_directory_sync(root);
}
