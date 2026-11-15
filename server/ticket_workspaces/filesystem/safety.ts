import { rm, unlink } from 'node:fs/promises';
import { dirname, sep } from 'node:path';
import { path_is_within } from '../../core/path_containment.ts';
import type { WorkspaceOptions, WorkspaceState } from '../contracts.ts';
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
export async function cleanup_ticket_folder(
  root: string,
  options: WorkspaceOptions & {
    states?: ReadonlyMap<string, WorkspaceState>;
    active_ticket_ids?: ReadonlySet<string>;
    before_remove?: (path: string) => string | null | Promise<string | null>;
  } = {},
): Promise<string | null> {
  const reason = await reason_to_keep(root, options.states, options.active_ticket_ids, options);
  if (reason !== null) return reason;
  if (options.before_remove) {
    const reason = await options.before_remove(root);
    if (reason !== null) return reason;
  }
  for (const checkout of (await checkouts(root)).reverse())
    if ((await entry_stat(checkout + '/.git'))?.isFile()) {
      const common = await git(
        checkout,
        ['rev-parse', '--path-format=absolute', '--git-common-dir'],
        options,
      );
      if (common.returncode) return `Git cannot locate the repository for ${checkout}`;
      const removed = await git(
        common.stdout.trim(),
        ['worktree', 'remove', '--force', checkout],
        options,
      );
      if (removed.returncode)
        return `Git cannot remove the worktree ${checkout}: ${removed.stderr.trim()}`;
    }
  if ((await entry_stat(root))?.isDirectory()) await rm(root, { recursive: true });
  else await unlink(root);
  return null;
}
