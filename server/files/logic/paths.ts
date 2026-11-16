import { dirname, join, relative, isAbsolute } from 'node:path';
import { path_is_within } from '../../core/path_containment.ts';
import { lstatSync, realpathSync, readlinkSync, statSync } from 'node:fs';
import type { OwnerKind } from '../contracts.ts';
export const PRIVATE_TICKET_NAMES = new Set([
  'worktree',
  '.panels-workspace.json',
  '.panels-workspace.json.tmp',
]);
/** Atomic ownership writes use a UUID-shaped temporary filename. */
export function is_private_ticket_name(name: string): boolean {
  return (
    PRIVATE_TICKET_NAMES.has(name) || /^\.panels-workspace\.json\.[0-9a-f]{32}\.tmp$/.test(name)
  );
}
export function private_ticket_path(ownerRoot: string, path: string): boolean {
  if (is_private_ticket_name(path.split('/')[0]!)) return true;
  function resolve_alias(target: string, base = '/', depth = 0): string {
    if (depth > 40) throw new Error('Too many symbolic links');
    try {
      return realpathSync.native(isAbsolute(target) ? target : `${base}/${target}`);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
    let cursor = isAbsolute(target) ? '/' : base;
    // Process each symlink before the following '..'. Lexical normalization loses that order.
    for (const part of target.split('/')) {
      if (!part || part === '.') continue;
      if (part === '..') {
        cursor = dirname(cursor);
        continue;
      }
      const candidate = join(cursor, part);
      try {
        cursor = lstatSync(candidate).isSymbolicLink()
          ? resolve_alias(readlinkSync(candidate), cursor, depth + 1)
          : candidate;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
        cursor = candidate;
      }
    }
    return cursor;
  }
  try {
    const owner = resolve_alias(ownerRoot),
      target = resolve_alias(join(ownerRoot, path));
    return (
      path_is_within(owner, target) &&
      is_private_ticket_name(relative(owner, target).split('/')[0]!)
    );
  } catch {
    return true;
  }
}
export const unsafeEncoded = /%(?:25|2e|2f|5c)/i;
export function safeId(value: string): void {
  if (!value || /[^A-Za-z0-9_-]/.test(value)) throw new Error('unsafe owner id');
}
export function safeRelative(value: string): void {
  if (
    !value ||
    value.startsWith('/') ||
    value.includes('\\') ||
    unsafeEncoded.test(value) ||
    value.split('/').some((p) => !p || p === '.' || p === '..')
  )
    throw new Error('unsafe managed file path');
}
export function inside(root: string, target: string): void {
  if (!path_is_within(root, target)) throw new Error('managed file escapes managed root');
}
export function symlink(path: string): boolean {
  try {
    return lstatSync(path).isSymbolicLink();
  } catch {
    return false;
  }
}
export function filesRoot(db: string, kind: OwnerKind): string {
  return join(
    dirname(db),
    'files',
    { ticket: 'tickets', manager: 'sprint-items', goal: 'goals' }[kind],
  );
}
export function resolveFile(db: string, kind: OwnerKind, owner: string, path: string): string {
  safeId(owner);
  safeRelative(path);
  const rootPath = filesRoot(db, kind);
  if (kind === 'ticket' && private_ticket_path(join(rootPath, owner), path))
    throw new Error('private Ticket workspace');
  if (symlink(rootPath)) throw new Error('managed file root is a symlink');
  const root = realpathSync(rootPath);
  const ownerPath = join(root, owner);
  if (symlink(ownerPath)) throw new Error('managed entity directory is a symlink');
  const ownerRoot = realpathSync(ownerPath);
  const target = realpathSync(join(ownerRoot, path));
  inside(root, ownerRoot);
  inside(ownerRoot, target);
  if (!statSync(target).isFile()) throw new Error('managed file is not a regular file');
  return target;
}
