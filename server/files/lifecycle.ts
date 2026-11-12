/** Recoverable managed-file quarantine around a database deletion. */
import { dirname, join } from 'node:path';
import { existsSync, lstatSync, mkdirSync, renameSync, rmSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
export interface QuarantinedFiles {
  original: string;
  quarantine: string;
}
export function quarantine_manager_files(db_path: string, id: string): QuarantinedFiles | null {
  const root = join(dirname(db_path), 'files', 'sprint-items');
  if (existsSync(root) && lstatSync(root).isSymbolicLink())
    throw new Error('managed Manager file root is a symlink');
  const original = join(root, id);
  try {
    lstatSync(original);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw error;
  }
  const quarantineRoot = join(dirname(root), '.sprint-item-quarantine');
  mkdirSync(quarantineRoot, { recursive: true });
  const quarantine = join(quarantineRoot, id + '.' + randomUUID().replaceAll('-', ''));
  renameSync(original, quarantine);
  return { original, quarantine };
}
export function restore_quarantined_files(value: QuarantinedFiles | null): void {
  if (value) renameSync(value.quarantine, value.original);
}
export function purge_quarantined_files(value: QuarantinedFiles | null): void {
  if (!value) return;
  try {
    rmSync(value.quarantine, {
      recursive: !lstatSync(value.quarantine).isSymbolicLink(),
      force: true,
    });
  } catch {
    /* Deletion committed; maintenance can remove this unreachable quarantine. */
  }
}
