/** Recoverable managed-file quarantine shared by Manager and goal deletion. */
import { existsSync, mkdirSync, renameSync, rmSync, lstatSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { filesRoot, symlink } from './logic/paths.ts';
export interface QuarantinedFiles {
  original: string;
  quarantine: string;
}
function quarantine(db: string, owner: string, kind: 'manager' | 'goal'): QuarantinedFiles | null {
  if (!db) throw new Error('managed file lifecycle needs a file-backed database');
  const root = filesRoot(db, kind),
    label = kind === 'manager' ? 'Manager' : 'goal';
  if (symlink(root)) throw new Error(`managed ${label} file root is a symlink`);
  const original = join(root, owner);
  if (!existsSync(original) && !symlink(original)) return null;
  const quarantines = join(
    dirname(root),
    kind === 'manager' ? '.sprint-item-quarantine' : '.goal-quarantine',
  );
  mkdirSync(quarantines, { recursive: true });
  const target = join(quarantines, owner + '.' + randomUUID().replaceAll('-', ''));
  renameSync(original, target);
  return { original, quarantine: target };
}
export const quarantine_manager_files = (db: string, owner: string): QuarantinedFiles | null =>
  quarantine(db, owner, 'manager');
export const quarantine_goal_files = (db: string, owner: string): QuarantinedFiles | null =>
  quarantine(db, owner, 'goal');
export function restore_quarantined_files(files: QuarantinedFiles | null): void {
  if (files) renameSync(files.quarantine, files.original);
}
export function purge_quarantined_files(files: QuarantinedFiles | null): void {
  if (!files) return;
  try {
    const info = lstatSync(files.quarantine);
    rmSync(files.quarantine, {
      recursive: info.isDirectory() && !info.isSymbolicLink(),
      force: true,
    });
  } catch {
    /* Canonical deletion already committed. The quarantined path is unreachable. */
  }
}
