/** Export-only ordinary-app proof. The driver supplies a Python-created database. */
import { createApp } from '../../../app.ts';
import { loadConfig } from '../../../core/config.ts';
import { createDatabase } from '../../../core/database/index.ts';
import { routes } from '../routes.ts';
interface Spec {
  readonly db_path: string;
  readonly urls: readonly string[];
}
export async function run(spec: Spec): Promise<unknown> {
  const database = createDatabase(spec.db_path);
  const snapshot = () =>
    database.read((sql) => ({
      catalog: sql.all(
        'SELECT * FROM conversation_history_catalog ORDER BY owner_kind,owner_id,conversation_id',
      ),
      revisions: sql.all('SELECT * FROM resource_source_versions ORDER BY source_kind,source_id'),
    }));
  const before = snapshot();
  const app = await createApp(
    loadConfig(undefined, {
      PLAN_TEST_MODE: '1',
      PLAN_DB_PATH: spec.db_path,
    }),
    { database, areas: [{ prefix: '/api', routes }] },
  );
  try {
    const answers = [];
    for (const url of spec.urls) {
      const response = await app.inject({ method: 'GET', url });
      const body = response.json() as { entries?: { label: string }[] };
      answers.push({
        status: response.statusCode,
        body,
        codepoints:
          body.entries?.map((entry) =>
            Array.from(entry.label.split(' · ')[0], (value) => value.codePointAt(0)),
          ) ?? [],
      });
    }
    return { answers, unchanged: JSON.stringify(before) === JSON.stringify(snapshot()) };
  } finally {
    await app.close();
    database.close();
  }
}
