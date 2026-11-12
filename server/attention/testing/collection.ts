/** Direct attention collector proof on populated, exclusively synthetic state. */
import { tmpdir } from 'node:os';
import { relative, isAbsolute, resolve } from 'node:path';
import { createReadAccess, createDatabase } from '../../core/database/index.ts';
import { collect_daily } from '../collection.ts';
import type { HistoryWindow } from '../../measure_sources/contracts.ts';
interface Spec {
  db_path: string;
  now: number;
  dataset_ids: Record<string, string>;
  measure_ids: Record<string, string>;
  histories: (HistoryWindow | null)[];
}
export async function run(spec: Spec) {
  const child = relative(resolve(tmpdir()), resolve(spec.db_path));
  if (child.startsWith('..') || isAbsolute(child))
    throw new Error('collector proof requires a database inside tmpdir');
  const results = [];
  for (const history of spec.histories) {
    const output: unknown[] = [];
    await collect_daily(spec.db_path, spec.dataset_ids, spec.measure_ids, {
      context: { frozen_at: spec.now, history },
      record: (id, date, value, options) => {
        output.push({ kind: 'scalar', id, date, value, ...options });
      },
      record_dataset: (id, date, rows, options) => {
        output.push({ kind: 'dataset', id, date, rows, ...options });
      },
    });
    results.push(output);
  }
  const access = createReadAccess(spec.db_path);
  let readonly = false,
    no_write_capability = false;
  try {
    access.read((sql) => {
      no_write_capability = !('run' in sql);
      try {
        sql.get('UPDATE attention_history_metadata SET started_at=0 RETURNING started_at');
      } catch {
        readonly = true;
      }
    });
  } finally {
    access.close();
  }
  const database = createDatabase(spec.db_path);
  let rejected_writable = false;
  try {
    try {
      await collect_daily(database as unknown as string, spec.dataset_ids, spec.measure_ids, {
        context: { frozen_at: spec.now, history: null },
        record: () => {},
        record_dataset: () => {},
      });
    } catch (error) {
      rejected_writable = error instanceof TypeError;
    }
  } finally {
    database.close();
  }
  if (!readonly || !no_write_capability || !rejected_writable)
    throw new Error('attention collector accepted writable capability');
  return { results, readonly, no_write_capability, rejected_writable, path_inside_temp: true };
}
