import { dirname, join, extname, relative, resolve } from 'node:path';
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
  function resolve_alias(target: string, depth = 0): string {
    if (depth > 40) throw new Error('Too many symbolic links');
    let cursor = '/';
    const parts = resolve(target).split('/').filter(Boolean);
    for (let index = 0; index < parts.length; index++) {
      cursor = join(cursor, parts[index]!);
      try {
        if (lstatSync(cursor).isSymbolicLink())
          return resolve_alias(
            resolve(dirname(cursor), readlinkSync(cursor), ...parts.slice(index + 1)),
            depth + 1,
          );
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'ENOENT')
          return join(cursor, ...parts.slice(index + 1));
        throw error;
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
function htmlOwner(href: string): { kind: OwnerKind; owner: string; path: string } {
  if (/[\x00-\x1f\\?#]/.test(href) || !href.startsWith('/files/') || unsafeEncoded.test(href))
    throw new Error('unsafe managed URL');
  const parts = /^\/files\/(tickets|managers|sprint-items|goals)\/([^/]+)\/(.+)$/.exec(
    decodeURIComponent(href),
  );
  if (!parts) throw new Error('unsafe managed URL');
  const kind: OwnerKind =
    parts[1] === 'tickets' ? 'ticket' : parts[1] === 'goals' ? 'goal' : 'manager';
  const owner = parts[2]!,
    path = parts[3]!;
  safeId(owner);
  safeRelative(path);
  if (path.includes('\0') || !['.html', '.htm'].includes(extname(path).toLowerCase()))
    throw new Error('not an HTML artifact');
  return { kind, owner, path };
}
export function resolveHtml(db: string, rootHref: string, candidateHref: string): string {
  const root = htmlOwner(rootHref),
    candidate = htmlOwner(candidateHref);
  if (root.kind !== candidate.kind || root.owner !== candidate.owner)
    throw new Error('different managed owner');
  const logicalRoot = dirname(root.path);
  inside(logicalRoot, candidate.path);
  const artifactRoot = realpathSync(join(filesRoot(db, root.kind), root.owner, logicalRoot));
  inside(artifactRoot, resolveFile(db, root.kind, root.owner, root.path));
  const target = resolveFile(db, candidate.kind, candidate.owner, candidate.path);
  inside(artifactRoot, target);
  return target;
}
