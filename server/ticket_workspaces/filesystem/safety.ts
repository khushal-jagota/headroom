import { constants } from 'node:fs';
import { unlink, open, lstat, readdir, rmdir, readFile } from 'node:fs/promises';
import type { FileHandle } from 'node:fs/promises';
import { basename, dirname, join, resolve, sep } from 'node:path';
import { path_is_within } from '../../core/path_containment.ts';
import type { WorkspaceOptions, WorkspaceState } from '../contracts.ts';
import { remove_darwin_workspace } from './darwin_removal.ts';
import { admit, checkouts, entry_stat, git, ticket_match, ticket_mentions } from './shared.ts';
export async function workspace_ticket_owners(
  root: string,
  options: WorkspaceOptions = {},
): Promise<string[]> {
  root = admit(root, options);
  const owners = new Set<string>();
  for (const checkout of await checkouts(root)) {
    for (const part of checkout.split(sep)) {
      const match = ticket_match.exec(part);
      if (match) owners.add(match[1]!);
    }
    const branch = await git(checkout, ['symbolic-ref', '--short', 'HEAD'], options);
    for (const match of branch.stdout.matchAll(ticket_mentions)) owners.add(match[0]);
  }
  return [...owners].sort();
}
export async function reason_to_keep(
  root: string,
  states?: ReadonlyMap<string, WorkspaceState>,
  active_ticket_ids: ReadonlySet<string> = new Set(),
  options: WorkspaceOptions = {},
): Promise<string | null> {
  root = admit(root, options);
  if (process.platform !== 'linux' && process.platform !== 'darwin')
    return 'safe removal is not supported on this platform yet';
  if (
    (await entry_stat(root))?.isSymbolicLink() ||
    (await entry_stat(dirname(root)))?.isSymbolicLink()
  )
    return 'workspace path is a symlink';
  let found: string[];
  try {
    found = await checkouts(root);
  } catch (error) {
    return `cannot inspect workspace: ${String(error)}`;
  }
  const match = ticket_match.exec(root.split(sep).at(-1)!);
  if (match)
    try {
      if ((await workspace_ticket_owners(root, options)).some((owner) => owner !== match[1]))
        return 'mixed Ticket ownership requires owner review';
    } catch (error) {
      return `cannot inspect workspace ownership: ${String(error)}`;
    }
  for (const checkout of found)
    try {
      const tracked = await git(checkout, ['ls-files', '--stage'], options);
      if (tracked.returncode) return `Git cannot inspect tracked work in ${checkout}`;
      if (tracked.stdout.split('\n').some((line) => line.startsWith('160000 ')))
        return `submodules require owner review before cleanup: ${checkout}`;
      if (states) {
        const owners: string[] = [];
        for (const part of checkout.split(sep)) {
          const match = ticket_match.exec(part);
          if (match) owners.push(match[1]!);
        }
        const branch = await git(checkout, ['symbolic-ref', '--short', 'HEAD'], options);
        owners.push(...[...branch.stdout.matchAll(ticket_mentions)].map((match) => match[0]));
        if (
          owners.some(
            (owner) =>
              active_ticket_ids.has(owner) ||
              (states.has(owner) && states.get(owner)!.ended_at === null),
          )
        )
          return `holds a live Ticket worktree in ${checkout}`;
      }
      const status = await git(
        checkout,
        ['status', '--porcelain', '--untracked-files=normal', '--ignore-submodules=none'],
        options,
      );
      if (status.returncode) return `Git cannot read ${checkout}`;
      if (status.stdout.trim()) return `uncommitted work in ${checkout}`;
      const main = (await entry_stat(checkout + '/.git'))?.isDirectory();
      if (main) {
        const linked = await git(checkout, ['worktree', 'list', '--porcelain'], options);
        if (linked.returncode) return `Git cannot inspect linked worktrees for ${checkout}`;
        for (const line of linked.stdout.split('\n'))
          if (line.startsWith('worktree ') && !path_is_within(root, line.slice(9)))
            return `repository still owns an external worktree: ${line.slice(9)}`;
      }
      const remotes = await git(checkout, ['remote'], options);
      if (remotes.returncode || !remotes.stdout.trim())
        return `no remote copy can be verified for ${checkout}`;
      const hashes = new Set<string>();
      for (const remote of remotes.stdout.trim().split('\n')) {
        const fetched = await git(checkout, ['fetch', '--prune', remote], options),
          advertised = await git(checkout, ['ls-remote', '--heads', '--tags', remote], options);
        if (fetched.returncode || advertised.returncode)
          return `remote verification failed for ${checkout}`;
        for (const line of advertised.stdout.trim().split('\n').filter(Boolean))
          hashes.add(line.split(/\s+/)[0]!);
      }
      if (!hashes.size) return `no remote copy can be verified for ${checkout}`;
      const unpublished = await git(
        checkout,
        ['rev-list', '-n', '1', main ? '--all' : 'HEAD', '--not', ...[...hashes].sort()],
        options,
      );
      if (unpublished.returncode) return `Git cannot compare remote work for ${checkout}`;
      if (unpublished.stdout.trim()) return `unpushed work in ${checkout}`;
    } catch (error) {
      return `Git inspection failed for ${checkout}: ${String(error)}`;
    }
  return null;
}

/** Linux procfs supplies fd-relative names without another native binding.
 * Every directory is opened without following its final component. Descendant
 * names resolve through that held descriptor even if an ancestor is replaced.
 */
async function remove_anchored(
  root: string,
  signal?: AbortSignal,
  identity?: { dev: number; ino: number },
  parent_identity?: { dev: number; ino: number },
  ancestors?: ReadonlyMap<string, { dev: number; ino: number }>,
): Promise<void> {
  if (process.platform === 'darwin') {
    await remove_darwin_workspace(root, signal, identity, parent_identity, ancestors);
    return;
  }
  const flags = constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW;
  const fd_path = (handle: FileHandle) => `/proc/self/fd/${handle.fd}`;
  let parent = await open('/', flags);
  try {
    let cursor = '/';
    for (const part of dirname(resolve(root)).split('/').filter(Boolean)) {
      signal?.throwIfAborted();
      const next = await open(`${fd_path(parent)}/${part}`, flags);
      await parent.close();
      parent = next;
      cursor = join(cursor, part);
      const expected = ancestors?.get(cursor);
      if (expected) {
        const actual = await parent.stat();
        if (actual.dev !== expected.dev || actual.ino !== expected.ino)
          throw new Error('Workspace ancestor directory changed before removal');
      }
    }
    async function remove_entry(
      parent: FileHandle,
      name: string,
      identity?: { dev: number; ino: number },
    ): Promise<void> {
      signal?.throwIfAborted();
      const path = `${fd_path(parent)}/${name}`;
      const expected = await lstat(path);
      if (identity && (expected.dev !== identity.dev || expected.ino !== identity.ino))
        throw new Error('Workspace directory identity changed before removal');
      if (!expected.isDirectory()) {
        // unlink never follows the final component, including a replacement link.
        await unlink(path);
        return;
      }
      const directory = await open(path, flags);
      try {
        const opened = await directory.stat();
        if (opened.dev !== expected.dev || opened.ino !== expected.ino)
          throw new Error('Workspace directory changed before removal');
        for (const child of await readdir(fd_path(directory))) await remove_entry(directory, child);
        const attached = await lstat(path);
        if (!attached.isDirectory() || attached.dev !== opened.dev || attached.ino !== opened.ino)
          throw new Error('Workspace directory changed during removal');
        // rmdir does not follow a final symlink or recurse into a replacement.
        await rmdir(path);
      } finally {
        await directory.close();
      }
    }
    if (parent_identity) {
      const attached_parent = await parent.stat();
      if (
        attached_parent.dev !== parent_identity.dev ||
        attached_parent.ino !== parent_identity.ino
      )
        throw new Error('Workspace parent directory changed before removal');
    }
    await remove_entry(parent, basename(root), identity);
  } finally {
    await parent.close();
  }
}

export async function cleanup_ticket_folder(
  root: string,
  options: WorkspaceOptions & {
    states?: ReadonlyMap<string, WorkspaceState>;
    active_ticket_ids?: ReadonlySet<string>;
    before_remove?: (path: string) => string | null | Promise<string | null>;
  } = {},
): Promise<string | null> {
  root = admit(root, options);
  const root_identity = await lstat(root),
    parent_identity = await lstat(dirname(root));
  const reason = await reason_to_keep(root, options.states, options.active_ticket_ids, options);
  if (reason !== null) return reason;
  if (options.before_remove) {
    const reason = await options.before_remove(root);
    if (reason !== null) return reason;
  }
  const attached_root = await lstat(root),
    attached_parent = await lstat(dirname(root));
  if (
    attached_root.dev !== root_identity.dev ||
    attached_root.ino !== root_identity.ino ||
    attached_parent.dev !== parent_identity.dev ||
    attached_parent.ino !== parent_identity.ino
  )
    return 'Workspace directory changed before removal';
  const custody = new Map([
    [root, root_identity],
    [dirname(root), parent_identity],
  ]);
  for (const checkout of (await checkouts(root)).reverse())
    if ((await entry_stat(checkout + '/.git'))?.isFile()) {
      const common = await git(
        checkout,
        ['rev-parse', '--path-format=absolute', '--git-common-dir'],
        options,
      );
      if (common.returncode) return `Git cannot locate the repository for ${checkout}`;
      const metadata = await git(checkout, ['rev-parse', '--absolute-git-dir'], options);
      if (metadata.returncode) return `Git cannot locate worktree metadata for ${checkout}`;
      const admin = resolve(metadata.stdout.trim()),
        common_root = resolve(common.stdout.trim());
      if (dirname(admin) !== join(common_root, 'worktrees'))
        return `Git cannot verify worktree metadata for ${checkout}`;
      if (await entry_stat(join(admin, 'locked'))) return `Git worktree is locked: ${checkout}`;
      const admin_identity = await lstat(admin);
      const backlink = await readFile(join(admin, 'gitdir'), 'utf8');
      if (!admin_identity.isDirectory() || resolve(backlink.trim()) !== resolve(checkout, '.git'))
        return `Git cannot verify worktree metadata for ${checkout}`;
      const checkout_identity = await lstat(checkout);
      // Git's own worktree remove recursively deletes by pathname. Delete the
      // checkout and its exact validated registration through held descriptors.
      await remove_anchored(checkout, options.signal, checkout_identity, undefined, custody);
      await remove_anchored(admin, options.signal, admin_identity);
    }
  await remove_anchored(root, options.signal, root_identity, parent_identity);
  return null;
}
