import { constants } from 'node:fs';
import { unlink, open, lstat, readdir, rmdir, readFile } from 'node:fs/promises';
import type { FileHandle } from 'node:fs/promises';
import { basename, dirname, join, relative, resolve, sep } from 'node:path';
import { path_is_within } from '../../core/path_containment.ts';
import { OWNERSHIP_FILE, WORKTREE_DIRECTORY } from '../contracts.ts';
import type {
  WorkspaceMarker,
  WorkspaceOptions,
  WorkspaceState,
  WorkspaceSafetyInspection,
} from '../contracts.ts';
import { remove_darwin_workspace } from './darwin_removal.ts';
import {
  admit,
  checkouts,
  entry_stat,
  git,
  json,
  ticket_match,
  ticket_mentions,
} from './shared.ts';
export async function recorded_checkout_problem(
  root: string,
  options: WorkspaceOptions,
): Promise<string | null> {
  const marker_path = join(root, OWNERSHIP_FILE);
  const info = await entry_stat(marker_path);
  if (info === null) return null;
  try {
    if (info.isSymbolicLink() || !info.isFile()) return 'workspace ownership evidence is invalid';
    const marker = await json<WorkspaceMarker>(marker_path);
    if (
      (marker.checkout_kind !== 'clone' && marker.repository == null) ||
      marker.worktree === undefined
    )
      return null;
    const checkout = join(root, WORKTREE_DIRECTORY),
      metadata = await entry_stat(join(checkout, '.git'));
    if ((await entry_stat(checkout))?.isSymbolicLink() || !metadata || metadata.isSymbolicLink())
      return 'recorded Git checkout or its metadata is missing or invalid';
    const top = await git(checkout, ['rev-parse', '--show-toplevel'], options);
    if (top.returncode || resolve(top.stdout.trim()) !== resolve(checkout))
      return 'recorded Git checkout or its metadata is missing or invalid';
  } catch (error) {
    return `cannot inspect recorded Git checkout ownership: ${String(error)}`;
  }
  return null;
}
export async function workspace_ticket_owners(
  root: string,
  options: WorkspaceOptions = {},
): Promise<string[]> {
  root = admit(root, options);
  const owners = new Set<string>();
  for (const checkout of await checkouts(root)) {
    for (const part of [basename(root), ...relative(root, checkout).split(sep)]) {
      const match = ticket_match.exec(part);
      if (match) owners.add(match[1]!);
    }
    const branch = await git(checkout, ['symbolic-ref', '--short', 'HEAD'], options);
    for (const match of branch.stdout.matchAll(ticket_mentions)) owners.add(match[0]);
  }
  return [...owners].sort();
}
/** `discard` is for a deleted Ticket: its own work goes without proof that origin holds it.
 * Only the checks that protect other Tickets' work remain: mixed ownership, a live Ticket's
 * checkout, and a worktree outside the folder. */
export type OwnWork = 'protect' | 'discard';
export async function reason_to_keep(
  root: string,
  states?: ReadonlyMap<string, WorkspaceState>,
  active_ticket_ids: ReadonlySet<string> = new Set(),
  options: WorkspaceOptions = {},
  own_work: OwnWork = 'protect',
): Promise<string | null> {
  return (await inspect_workspace_safety(root, states, active_ticket_ids, options, own_work))
    .reason;
}

/** Keep the remote evidence from the same safety check for a move's branch proof. */
export async function inspect_workspace_safety(
  root: string,
  states?: ReadonlyMap<string, WorkspaceState>,
  active_ticket_ids: ReadonlySet<string> = new Set(),
  options: WorkspaceOptions = {},
  own_work: OwnWork = 'protect',
): Promise<WorkspaceSafetyInspection> {
  const advertised_refs = new Map<string, ReadonlyMap<string, string>>();
  const reason = await workspace_safety_problem(
    root,
    states,
    active_ticket_ids,
    options,
    advertised_refs,
    own_work,
  );
  return { reason, advertised_refs };
}

async function workspace_safety_problem(
  root: string,
  states: ReadonlyMap<string, WorkspaceState> | undefined,
  active_ticket_ids: ReadonlySet<string>,
  options: WorkspaceOptions,
  advertised_refs: Map<string, ReadonlyMap<string, string>>,
  own_work: OwnWork,
): Promise<string | null> {
  const protect = own_work === 'protect';
  root = admit(root, options);
  if (process.platform !== 'linux' && process.platform !== 'darwin')
    return 'safe removal is not supported on this platform yet';
  if (
    (await entry_stat(root))?.isSymbolicLink() ||
    (await entry_stat(dirname(root)))?.isSymbolicLink()
  )
    return 'workspace path is a symlink';
  const recorded = protect ? await recorded_checkout_problem(root, options) : null;
  if (recorded) return recorded;
  let found: string[];
  try {
    found = await checkouts(root);
  } catch (error) {
    return `cannot inspect workspace: ${String(error)}`;
  }
  const match = ticket_match.exec(root.split(sep).at(-1)!);
  // A checkout that names another Ticket can be that Ticket's work, live on another machine.
  if (match)
    try {
      if ((await workspace_ticket_owners(root, options)).some((owner) => owner !== match[1]))
        return 'mixed Ticket ownership requires owner review';
    } catch (error) {
      return `cannot inspect workspace ownership: ${String(error)}`;
    }
  for (const checkout of found)
    try {
      if (protect) {
        const tracked = await git(checkout, ['ls-files', '--stage'], options);
        if (tracked.returncode) return `Git cannot inspect tracked work in ${checkout}`;
        if (tracked.stdout.split('\n').some((line) => line.startsWith('160000 ')))
          return `submodules require owner review before cleanup: ${checkout}`;
      }
      if (states) {
        const owners: string[] = [];
        for (const part of [basename(root), ...relative(root, checkout).split(sep)]) {
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
      if (protect) {
        const status = await git(
          checkout,
          ['status', '--porcelain', '--untracked-files=normal', '--ignore-submodules=none'],
          options,
        );
        if (status.returncode) return `Git cannot read ${checkout}`;
        if (status.stdout.trim()) return `uncommitted work in ${checkout}`;
      }
      const main = (await entry_stat(checkout + '/.git'))?.isDirectory();
      if (main) {
        const linked = await git(checkout, ['worktree', 'list', '--porcelain'], options);
        if (linked.returncode) return `Git cannot inspect linked worktrees for ${checkout}`;
        for (const line of linked.stdout.split('\n'))
          if (line.startsWith('worktree ') && !path_is_within(root, line.slice(9)))
            return `repository still owns an external worktree: ${line.slice(9)}`;
      }
      if (!protect) continue;
      const remotes = await git(checkout, ['remote'], options);
      if (remotes.returncode || !remotes.stdout.trim().split('\n').includes('origin'))
        return `no origin copy can be verified for ${checkout}`;
      const hashes = new Set<string>();
      {
        const advertised = await git(
          checkout,
          ['ls-remote', '--heads', '--tags', 'origin'],
          options,
        );
        if (advertised.returncode) return `remote verification failed for ${checkout}`;
        const refs = new Map<string, string>();
        advertised_refs.set(checkout, refs);
        for (const line of advertised.stdout.trim().split('\n').filter(Boolean)) {
          const [revision, ref] = line.split(/\s+/);
          if (revision && ref) refs.set(ref, revision);
        }
        const revisions = [...new Set(refs.values())];
        if (revisions.length) {
          // Single-branch partial clones lack unrelated advertised commits. Fetch them together
          // before object inspection, so cat-file does not lazily fetch each missing ref.
          // This proof uses the current advertisement, never stale remote-tracking refs.
          const fetched = await git(
            checkout,
            ['fetch', '--filter=blob:none', '--no-tags', 'origin', ...revisions],
            options,
          );
          if (fetched.returncode) return `remote object verification failed for ${checkout}`;
          // Peeling tags and excluding unavailable objects retains the same ancestry proof.
          const checked = await git(
            checkout,
            ['cat-file', '--batch-check=%(objectname) %(objecttype)'],
            options,
            revisions.map((revision) => `${revision}^{commit}\n`).join(''),
          );
          const objects = checked.stdout.trim().split('\n');
          if (checked.returncode || objects.length !== revisions.length)
            return `remote object verification failed for ${checkout}`;
          for (const [index, line] of objects.entries())
            if (/^[0-9a-f]{40,64} commit$/.test(line)) hashes.add(revisions[index]!);
        }
      }
      if (!hashes.size) return `no remote copy can be verified for ${checkout}`;
      const refs = ['HEAD'];
      const marker_root = basename(root) === WORKTREE_DIRECTORY ? dirname(root) : root;
      const marker_path = join(marker_root, OWNERSHIP_FILE);
      if (
        checkout === join(marker_root, WORKTREE_DIRECTORY) &&
        (await entry_stat(marker_path))?.isFile()
      ) {
        const marker = await json<WorkspaceMarker>(marker_path);
        if (marker.branch) refs.push(`refs/heads/${marker.branch}`);
      }
      const unpublished = await git(
        checkout,
        ['rev-list', '-n', '1', ...refs, '--not', ...[...hashes].sort()],
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
    own_work?: OwnWork;
  } = {},
): Promise<string | null> {
  root = admit(root, options);
  const root_identity = await lstat(root),
    parent_identity = await lstat(dirname(root));
  const reason = await reason_to_keep(
    root,
    options.states,
    options.active_ticket_ids,
    options,
    options.own_work,
  );
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
