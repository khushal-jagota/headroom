/** Export-only proofs over synthetic paths supplied by the direct driver. */
import { lstat, readFile, writeFile } from 'node:fs/promises';
import { setTimeout as pause } from 'node:timers/promises';
import { register_test_root } from '../../../core/test_paths.ts';
import { entry_stat, git, layout_for, layout_under } from '../collect.ts';
import { read_workspace_states } from '../../../ticket_workspaces/data.ts';
import { cleanup_local_workspaces } from '../../../ticket_workspaces/filesystem.ts';
import { createReadAccess } from '../../../core/database/index.ts';
import {
  snapshot_ticket_scratch,
  prune_ticket_scratch,
} from '../../../conversation/worker_scratch.ts';
import { path_is_within } from '../../../core/path_containment.ts';
import { lstatSync, realpathSync } from 'node:fs';
import { join, dirname } from 'node:path';
function guard(root: string, path: string): string {
  const canonical = realpathSync(root);
  let ancestor = path;
  while (true) {
    try {
      lstatSync(ancestor);
      break;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      ancestor = dirname(ancestor);
    }
  }
  if (!path_is_within(canonical, path) || !path_is_within(canonical, realpathSync(ancestor)))
    throw new Error('outside synthetic root');
  return path;
}
import { create_ticket_scratch_runtime } from '../runtime.ts';
import type { ScratchRuntimeOptions } from '../contracts.ts';
import type { ScratchProbeRequest } from './contracts.ts';
import { ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';

const empty = () => ({ removed: [], skipped: [] });
export async function probe_scratch_rules(request: ScratchProbeRequest) {
  const unregister = register_test_root(request.root);
  try {
    const reader = createReadAccess(request.database);
    const states = reader.read(read_workspace_states);
    reader.close();
    return {
      layout: await layout_under(request.root, request.home),
      gates: await Promise.all(
        (request.gates ?? []).map(
          async (gate) =>
            (await layout_for(gate.root, gate.database, gate.db_path))?.repository ?? null,
        ),
      ),
      states: [...states],
    };
  } finally {
    unregister();
  }
}
export async function probe_scratch_git(request: ScratchProbeRequest) {
  const unregister = register_test_root(request.root);
  try {
    const root = request.root,
      tickets = guard(root, request.tickets!);
    const result = await cleanup_local_workspaces(tickets, new Map(request.states), request.now, {
      admit_path: (path) => guard(root, path),
      snapshot_ticket_scratch: (id) => snapshot_ticket_scratch(id, request.home),
      prune_ticket_scratch: (id, names) => prune_ticket_scratch(id, names, request.home),
      before_remove: async (path) => {
        if (path === request.paths!['late-dirty'])
          await writeFile(guard(root, join(path, 'worktree/late.txt')), 'fresh change');
        return null;
      },
    });
    const manifest = JSON.parse(
      await readFile(join(dirname(tickets), 'ticket-workspaces-cleanup.json'), 'utf8'),
    );
    const remaining: Record<string, boolean> = {};
    for (const [kind, path] of Object.entries(request.paths!))
      remaining[kind] = (await entry_stat(path)) !== null;
    return { result, manifest, remaining };
  } finally {
    unregister();
  }
}

function deferred() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}
export async function probe_scratch_runtime(request: ScratchProbeRequest) {
  const unregister = register_test_root(request.root);
  const original_timeout = globalThis.setTimeout;
  const scheduled = new Map<NodeJS.Timeout, () => void>();
  const timers: number[] = [];
  globalThis.setTimeout = ((...[callback, delay, ...args]: Parameters<typeof setTimeout>) => {
    if (delay !== 600_000) return original_timeout(callback, delay, ...args);
    timers.push(delay);
    const timer = original_timeout(() => {}, delay);
    scheduled.set(timer, () => callback(...args));
    return timer;
  }) as typeof setTimeout;
  const tick = async () => {
    const next = scheduled.entries().next().value;
    if (!next) throw new Error('no cleanup timer');
    scheduled.delete(next[0]);
    clearTimeout(next[0]);
    next[1]();
    await pause(0);
  };
  const base: ScratchRuntimeOptions = {
    layout: request.layout!,
    db_path: request.database,
    test_mode: false,
    now_unix: () => request.now,
    assert_database_owned: () => {},
    clean_workspaces: async (_db, _now, options) => {
      await git(request.layout!.repository, ['status', '--porcelain'], options);
      return empty();
    },
  };
  const held = deferred();
  const seen: string[][] = [];
  let owned = true;
  const runtime = create_ticket_scratch_runtime({
    ...base,
    assert_database_owned: () => {
      if (!owned) throw new Error('ownership ended');
    },
    clean_workspaces: async (_db, _now, options) => {
      seen.push([]);
      if (seen.length === 1) {
        return empty();
      }
      await held.promise;
      options?.signal?.throwIfAborted();
      throw new Error('late cleanup escaped stop fence');
    },
  });
  let actual: ReturnType<typeof create_ticket_scratch_runtime> | undefined;
  try {
    runtime.start();
    const before_first = seen.length;
    await tick();
    await tick();
    const pending_timers = scheduled.size;
    const start = performance.now();
    await Promise.resolve(runtime.close({ deadline: start / 1000 + 0.03 })).then(
      () => {
        throw new Error('Held scratch work cannot finish shutdown');
      },
      (error: unknown) => {
        if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
      },
    );
    const stop_ms = performance.now() - start;
    let actual_done = false;
    const actual_drain = runtime.settled().then(() => {
      actual_done = true;
    });
    await pause(0);
    if (actual_done) throw new Error('Actual scratch settlement discarded a held pass');
    held.release();
    await actual_drain;
    owned = false;
    const after_stop = scheduled.size;
    const before_disabled = timers.length;
    for (const options of [{ ...base, test_mode: true }]) {
      const disabled = create_ticket_scratch_runtime(options);
      disabled.start();
      await disabled.close({ deadline: performance.now() / 1000 + 1 });
    }
    actual = create_ticket_scratch_runtime(base);
    actual.start();
    await tick();
    const started = performance.now();
    while ((await entry_stat(request.git_marker!)) === null) {
      if (performance.now() - started > 3000)
        throw new Error('synthetic Git did not reach its gate');
      await pause(5);
    }
    const pid = Number(await readFile(request.git_marker!, 'utf8'));
    const git_start = performance.now();
    await Promise.resolve(actual.close({ deadline: git_start / 1000 + 0.1 })).catch(
      (error: unknown) => {
        if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
      },
    );
    await actual.settled();
    const git_stop_ms = performance.now() - git_start;
    let reaped = false;
    try {
      process.kill(pid, 0);
    } catch (error) {
      if (error instanceof Error && 'code' in error && error.code === 'ESRCH') reaped = true;
      else throw error;
    }
    return {
      before_first,
      seen,
      pending_timers,
      actual_retained: true,
      stop_ms,
      after_stop,
      disabled_timers: timers.length - before_disabled - 1,
      timer_values: timers,
      git_stop_ms,
      owned_git_reaped: reaped,
      entry_preserved: (await lstat(request.watch_paths![0]!)).isDirectory(),
    };
  } finally {
    held.release();
    await Promise.resolve(runtime.close({ deadline: performance.now() / 1000 })).catch(
      (error: unknown) => {
        if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
      },
    );
    await runtime.settled();
    await Promise.resolve(actual?.close({ deadline: performance.now() / 1000 + 1 })).catch(
      (error: unknown) => {
        if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
      },
    );
    await actual?.settled();
    for (const timer of scheduled.keys()) clearTimeout(timer);
    globalThis.setTimeout = original_timeout;
    unregister();
  }
}
