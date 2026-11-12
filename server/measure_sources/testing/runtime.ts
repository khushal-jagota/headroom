/** Execute the stored JavaScript peer through the real child and completion door. */
import { createDatabase } from '../../core/database/index.ts';
import type { ReadSql } from '../../core/database/contracts.ts';
import { Runtime } from '../runtime/supervisor.ts';
import { SourceDispatcher } from '../dispatcher.ts';
import { ChangeSignal } from '../../core/changes/index.ts';
import { TestClock } from '../../core/clock.ts';
import { loadConfig } from '../../core/config.ts';
import { status } from '../views.ts';
import { read_source, read_run_detail } from '../data.ts';

interface Spec {
  db_path: string;
  key_path: string;
  source_id: string;
  run_id: string;
  now: number;
  tables: Record<string, string>;
}
export async function run(spec: Spec) {
  const changes = new ChangeSignal();
  const database = createDatabase(spec.db_path, { onChange: () => changes.emit() });
  const dispatcher = new SourceDispatcher({
    context: {
      database,
      changes,
      clock: new TestClock(new Date(spec.now * 1000).toISOString()),
      config: loadConfig('/missing-unit13-config', {
        PLAN_DB_PATH: spec.db_path,
        PLAN_TEST_MODE: '1',
        PLAN_CREDENTIAL_KEY_PATH: spec.key_path,
      }),
      assert_database_owned() {},
    },
    runtime: new Runtime(),
  });
  let unsubscribe = () => {};
  const finished = new Promise<void>((resolve) => {
    unsubscribe = changes.subscribe(() => {
      const status = database.read((sql) => read_run_detail(sql, spec.run_id).run.status);
      if (status !== 'pending' && status !== 'running') resolve();
    });
  });
  try {
    dispatcher.start();
    if (!dispatcher.poll_once().includes(spec.run_id))
      throw new Error('pending run was not claimed');
    await finished;
    await dispatcher.stop({ deadline: performance.now() / 1000 + 5 });
    const completed = database.read((sql) => read_run_detail(sql, spec.run_id));
    return database.read((sql) => ({
      completed,
      source: read_source(sql, spec.source_id),
      status: status(sql, spec.source_id, { now: spec.now }),
      tables: rows(sql, spec.tables),
    }));
  } finally {
    unsubscribe();
    await dispatcher.stop({ deadline: performance.now() / 1000 + 5 });
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
