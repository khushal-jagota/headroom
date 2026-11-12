import {
  mkdirSync,
  realpathSync,
  existsSync,
  statSync,
  writeFileSync,
  renameSync,
  unlinkSync,
} from 'node:fs';
import { join, dirname, basename } from 'node:path';
import { randomUUID } from 'node:crypto';
import { PlannerError } from '../core/contracts.ts';
import { filesRoot, inside, symlink } from './logic/paths.ts';
import type { OwnerKind } from './contracts.ts';
export function ownerRoot(
  db: string,
  kind: OwnerKind,
  owner: string,
  create: boolean,
): string | null {
  const family = filesRoot(db, kind),
    root = join(family, owner),
    label = kind === 'manager' ? 'Manager' : 'goal';
  const invalid = () => new PlannerError('validation', `unsafe ${label} file path`, {});
  if (symlink(family)) throw invalid();
  if (create) {
    if (symlink(root)) throw invalid();
    mkdirSync(root, { recursive: true });
    if (symlink(root)) throw invalid();
  } else if (!existsSync(root) || !statSync(root).isDirectory() || symlink(root)) return null;
  return realpathSync(root);
}
function targetPath(root: string, path: string, label: string): string {
  const invalid = () => new PlannerError('validation', `unsafe ${label} file path`, {});
  if (
    !path ||
    path.startsWith('/') ||
    path.includes('\\') ||
    path.split('/').some((p) => !p || p === '.' || p === '..')
  )
    throw invalid();
  const target = join(root, path);
  let parent = dirname(target);
  try {
    // Path.resolve(strict=False) resolves each existing ancestor.
    while (!existsSync(parent)) parent = dirname(parent);
    inside(root, realpathSync(parent));
    parent = dirname(target);
    while (parent !== root) {
      if (symlink(parent)) throw invalid();
      parent = dirname(parent);
    }
  } catch {
    throw invalid();
  }
  return target;
}
export function writeText(root: string, path: string, content: string, label: string): void {
  const target = targetPath(root, path, label);
  mkdirSync(dirname(target), { recursive: true });
  const temporary = join(
    dirname(target),
    `.${basename(target)}.${randomUUID().replaceAll('-', '')}.tmp`,
  );
  try {
    writeFileSync(temporary, content, 'utf8');
    renameSync(temporary, target);
  } finally {
    if (existsSync(temporary)) unlinkSync(temporary);
  }
}
export function deleteFile(root: string | null, path: string, label: string): void {
  const missing = () => new PlannerError('not_found', `${label} file not found`, {});
  if (root === null) throw missing();
  const target = targetPath(root, path, label);
  if (!existsSync(target) || !statSync(target).isFile() || symlink(target)) throw missing();
  unlinkSync(target);
}
