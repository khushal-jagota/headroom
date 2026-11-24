import { expect, test } from 'vitest';
import { record_native_processes } from './native_process_births.ts';
import type { WorkerProcess } from '../../worker_supervision/contracts.ts';

test('tracks surviving descendants and replacement services across a real PID change', () => {
  const child = (pid: number, birth: string, parent = 1): WorkerProcess => ({
    pid,
    birth,
    parent,
    group: pid,
    session: pid,
    zombie: false,
  });
  const table = (...entries: WorkerProcess[]) => new Map(entries.map((p) => [p.pid, p]));
  const seen = new Map<string, WorkerProcess>();
  record_native_processes(
    seen,
    table(child(33, 'old'), child(44, 'child', 33)),
    [33],
    new Set([33]),
  );
  record_native_processes(
    seen,
    table(
      child(33, 'unrelated'),
      child(35, 'foreign', 33),
      child(44, 'child'),
      child(54, 'orphaned-descendant', 44),
      child(66, 'new'),
    ),
    [66],
    new Set([66]),
  );
  expect([...seen.keys()].sort()).toEqual([
    '33/old',
    '44/child',
    '54/orphaned-descendant',
    '66/new',
  ]);
  expect(() => record_native_processes(seen, table(), [77], new Set([77]))).toThrow('77');
  record_native_processes(seen, table(child(33, 'next-generation')), [33], new Set([33]));
  expect(seen.has('33/old') && seen.has('33/next-generation')).toBe(true);
});
