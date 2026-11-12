/** Export-only proofs over synthetic paths supplied by the direct driver. */
import { lstat, readFile, unlink, writeFile } from 'node:fs/promises';
import { setTimeout as pause } from 'node:timers/promises';
import { register_test_root } from '../../../core/test_paths.ts';
import { clean_finished_ticket_scratch, remove_ticket_scratch } from '../apply.ts';
import {
  collect_ticket_scratch,
  entry_stat,
  git,
  layout_for,
  layout_under,
  read_ticket_states_from,
} from '../collect.ts';
import { finished, ticket_id_of_branch, ticket_id_of_name } from '../logic.ts';
import { create_ticket_scratch_runtime } from '../runtime.ts';
import type { ScratchResult, ScratchRuntimeOptions } from '../contracts.ts';
import type { ScratchProbeRequest } from './contracts.ts';

const empty = (): ScratchResult => ({
  removed: [],
  deleted_branches: [],
  kept_branches: [],
  skipped: [],
});
export async function probe_scratch_rules(request: ScratchProbeRequest) {
  const unregister = register_test_root(request.root);
  try {
    const states = new Map(request.states);
    return {
      layout: await layout_under(request.root, request.home),
      gates: await Promise.all(
        (request.gates ?? []).map(
          async (gate) =>
            (await layout_for(gate.root, gate.database, gate.db_path))?.repository ?? null,
        ),
      ),
      states: [...(await read_ticket_states_from(request.database))],
      names: (request.names ?? []).map(ticket_id_of_name),
      branches: (request.branches ?? []).map(ticket_id_of_branch),
      finished: [...states.keys(), 't_zzzzzzzz'].map((id) => [
        id,
        finished(id, states, request.now),
      ]),
    };
  } finally {
    unregister();
  }
}
export async function probe_scratch_git(request: ScratchProbeRequest) {
  const unregister = register_test_root(request.root);
  try {
    const layout = request.layout!,
      states = new Map(request.states);
    const inventory = await collect_ticket_scratch(layout, states, request.now);
    await writeFile(request.late_dirty!, 'changed after inventory\n');
    const applied = await remove_ticket_scratch(layout, inventory, states, request.now);
    for (const path of request.later_clean ?? []) await unlink(path);
    const already_kept = new Set(applied.skipped.map((item) => item.path));
    const remembered = await clean_finished_ticket_scratch(
      layout,
      request.database,
      request.now,
      already_kept,
    );
    const daily = await remove_ticket_scratch(
      layout,
      await collect_ticket_scratch(layout, states, request.now),
      states,
      request.now,
    );
    const remaining: Record<string, string | null> = {};
    for (const path of request.watch_paths ?? []) {
      const info = await entry_stat(path);
      remaining[path] =
        info === null
          ? null
          : info.isSymbolicLink()
            ? 'link'
            : info.isDirectory()
              ? 'directory'
              : 'file';
    }
    return {
      inventory,
      applied,
      remembered,
      daily,
      already_kept: [...already_kept].sort(),
      remaining,
      branches: (
        await git(layout.repository, ['for-each-ref', '--format=%(refname:short)', 'refs/heads'])
      ).stdout
        .split('\n')
        .filter(Boolean),
    };
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
    clean_finished_ticket_scratch,
  };
  const held = deferred();
  const seen: string[][] = [];
  let owned = true;
  const runtime = create_ticket_scratch_runtime({
    ...base,
    assert_database_owned: () => {
      if (!owned) throw new Error('ownership ended');
    },
    clean_finished_ticket_scratch: async (_layout, _db, _now, already_kept, options) => {
      seen.push([...already_kept]);
      if (seen.length === 1) {
        already_kept.add('kept-on-first-pass');
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
    await runtime.close({ deadline: start / 1000 + 0.03 });
    const stop_ms = performance.now() - start;
    owned = false;
    held.release();
    await pause(0);
    const after_stop = scheduled.size;
    const before_disabled = timers.length;
    for (const options of [
      { ...base, test_mode: true },
      { ...base, layout: null },
    ]) {
      const disabled = create_ticket_scratch_runtime(options);
      disabled.start();
      await disabled.close({ deadline: performance.now() / 1000 });
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
    await actual.close({ deadline: git_start / 1000 + 0.1 });
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
    await runtime.close({ deadline: performance.now() / 1000 });
    await actual?.close({ deadline: performance.now() / 1000 });
    for (const timer of scheduled.keys()) clearTimeout(timer);
    globalThis.setTimeout = original_timeout;
    unregister();
  }
}
