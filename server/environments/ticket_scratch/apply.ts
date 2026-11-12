import { readdir, rm, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { MERGE_TARGET, NOT_DESCENDED, STATUS_OPTIONS } from './configuration.ts';
import type {
  ScratchLayout,
  ScratchPassOptions,
  ScratchResult,
  TicketScratch,
  TicketStates,
  Worktree,
} from './contracts.ts';
import {
  collect_ticket_scratch,
  entry_stat,
  git,
  list_worktrees,
  read_ticket_states_from,
} from './collect.ts';
import {
  compare_paths,
  finished,
  inside,
  ticket_id_of_branch,
  ticket_id_of_path,
} from './logic.ts';

async function* git_checkouts(root: string, options: ScratchPassOptions): AsyncGenerator<string> {
  options.signal?.throwIfAborted();
  let entries;
  try {
    entries = await readdir(root, { withFileTypes: true });
  } catch (error) {
    if (error instanceof Error && 'code' in error) return; // os.walk ignores directory read errors.
    throw error;
  }
  if (entries.some((entry) => entry.name === '.git')) yield root;
  for (const entry of entries) {
    if (entry.isDirectory() && !NOT_DESCENDED.has(entry.name))
      yield* git_checkouts(join(root, entry.name), options);
  }
}
async function reason_to_keep(
  entry: string,
  worktrees: Worktree[],
  states: TicketStates,
  now: number,
  options: ScratchPassOptions,
): Promise<string | null> {
  const info = await entry_stat(entry);
  if (!info?.isDirectory() || info.isSymbolicLink()) return null;
  const registered = worktrees.filter((worktree) => inside(worktree.path, entry));
  for (const worktree of registered) {
    for (const owner of [ticket_id_of_path(worktree.path), ticket_id_of_branch(worktree.branch)]) {
      if (owner !== null && !finished(owner, states, now))
        return `holds the worktree ${worktree.path} of a live Ticket`;
    }
  }
  const checkouts = new Set(registered.map((worktree) => worktree.path));
  for await (const checkout of git_checkouts(entry, options)) checkouts.add(checkout);
  for (const checkout of [...checkouts].sort(compare_paths)) {
    const status = await git(checkout, ['status', '--porcelain', ...STATUS_OPTIONS], options);
    if (status.returncode !== 0) return `git cannot read the checkout ${checkout}`;
    if (status.stdout.trim()) return `uncommitted changes in ${checkout}`;
    const own_refs = (await entry_stat(join(checkout, '.git'), true))?.isDirectory()
      ? '--all'
      : 'HEAD';
    const unpushed = await git(
      checkout,
      ['rev-list', '-n', '1', own_refs, '--not', '--remotes'],
      options,
    );
    if (unpushed.returncode !== 0) return `git cannot compare ${checkout} with its remotes`;
    if (unpushed.stdout.trim()) return `commits that no remote has in ${checkout}`;
  }
  return null;
}
export async function remove_ticket_scratch(
  layout: ScratchLayout,
  scratches: Iterable<TicketScratch>,
  states: TicketStates,
  now: number,
  options: ScratchPassOptions = {},
): Promise<ScratchResult> {
  options.signal?.throwIfAborted();
  const result: ScratchResult = {
    removed: [],
    deleted_branches: [],
    kept_branches: [],
    skipped: [],
  };
  const inventory = [...scratches],
    worktrees = await list_worktrees(layout.repository, options);
  for (const scratch of inventory) {
    if (!finished(scratch.ticket_id, states, now)) continue;
    for (const entry of scratch.entries) {
      options.signal?.throwIfAborted();
      const info = await entry_stat(entry);
      if (info === null) continue;
      const reason = await reason_to_keep(entry, worktrees, states, now, options);
      if (reason !== null) {
        result.skipped.push({ path: entry, reason });
        continue;
      }
      options.signal?.throwIfAborted();
      try {
        if (info.isDirectory() && !info.isSymbolicLink()) await rm(entry, { recursive: true });
        else await unlink(entry);
      } catch (error) {
        if (!(error instanceof Error) || !('code' in error)) throw error;
        result.skipped.push({ path: entry, reason: `removal failed: ${error.message}` });
        continue;
      }
      result.removed.push(entry);
    }
  }
  await git(layout.repository, ['worktree', 'prune'], options);
  const checked_out = new Set(
    (await list_worktrees(layout.repository, options)).map((worktree) => worktree.branch),
  );
  for (const scratch of inventory) {
    for (const branch of scratch.branches) {
      if (checked_out.has(branch)) continue;
      const merged = await git(
        layout.repository,
        ['merge-base', '--is-ancestor', branch, MERGE_TARGET],
        options,
      );
      if (
        merged.returncode === 0 &&
        (await git(layout.repository, ['branch', '-D', branch], options)).returncode === 0
      )
        result.deleted_branches.push(branch);
      else result.kept_branches.push(branch);
    }
  }
  return result;
}
export async function clean_finished_ticket_scratch(
  layout: ScratchLayout,
  db_path: string,
  now: number,
  already_kept: Set<string>,
  options: ScratchPassOptions = {},
): Promise<ScratchResult> {
  options.signal?.throwIfAborted();
  const states = await read_ticket_states_from(db_path);
  options.signal?.throwIfAborted();
  const scratches = (await collect_ticket_scratch(layout, states, now, options)).map((scratch) => ({
    ...scratch,
    entries: scratch.entries.filter((entry) => !already_kept.has(entry)),
  }));
  const result = await remove_ticket_scratch(layout, scratches, states, now, options);
  options.signal?.throwIfAborted();
  for (const item of result.skipped) already_kept.add(item.path);
  return result;
}
