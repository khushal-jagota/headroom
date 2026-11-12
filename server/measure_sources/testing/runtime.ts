/** Execute the stored JavaScript peer through the real child and completion door. */
import { createDatabase } from '../../core/database/index.ts';
import type { ReadSql } from '../../core/database/contracts.ts';
import { Runtime } from '../runtime/supervisor.ts';
import { claim_run, complete_run } from '../run_actions.ts';
import { prepare_admission } from '../admission.ts';
import { status } from '../views.ts';
import { read_source } from '../data.ts';

interface Spec {
  db_path: string;
  key_path: string;
  source_id: string;
  run_id: string;
  now: number;
  tables: Record<string, string>;
}
export async function run(spec: Spec) {
  const database = createDatabase(spec.db_path);
  try {
    const claimed = database.write((sql) => claim_run(sql, spec.run_id, { now: spec.now }));
    if (!claimed) throw new Error('synthetic runtime run was not claimed');
    const [admission, credentials] = prepare_admission(database, claimed, {
      key_path: spec.key_path,
    });
    const result = await new Runtime().execute(admission, credentials);
    const completed = database.write((sql) =>
      complete_run(sql, claimed.id, result, { now: spec.now }),
    );
    return database.read((sql) => ({
      result,
      completed,
      source: read_source(sql, spec.source_id),
      status: status(sql, spec.source_id, { now: spec.now }),
      tables: rows(sql, spec.tables),
    }));
  } finally {
    database.close();
  }
}
function rows(sql: ReadSql, tables: Record<string, string>) {
  return Object.fromEntries(
    Object.entries(tables).map(([table, order]) => [
      table,
      sql.all(`SELECT * FROM ${table} ORDER BY ${order}`),
    ]),
  );
}
