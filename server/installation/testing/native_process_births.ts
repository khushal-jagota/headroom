import assert from 'node:assert/strict';
import type { WorkerProcess } from '../../worker_supervision/contracts.ts';

/** Keep all generations while expanding ancestry only from identified live generations. */
export function record_native_processes(
  recorded: Map<string, WorkerProcess>,
  snapshot: ReadonlyMap<number, WorkerProcess>,
  roots: Iterable<number>,
  required: ReadonlySet<number>,
): void {
  const owned = new Set<number>();
  for (const pid of roots) {
    if (snapshot.has(pid)) owned.add(pid);
    else
      assert.ok(
        !required.has(pid),
        'Cannot capture the native birth of an installed process: ' + pid,
      );
  }
  for (const process of recorded.values()) {
    if (snapshot.get(process.pid)?.birth === process.birth) owned.add(process.pid);
  }
  let size: number;
  do {
    size = owned.size;
    for (const process of snapshot.values()) {
      if (owned.has(process.parent)) owned.add(process.pid);
    }
  } while (owned.size !== size);
  for (const pid of owned) {
    const process = snapshot.get(pid)!;
    recorded.set(pid + '/' + process.birth, process);
  }
}
