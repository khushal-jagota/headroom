/** An existing database must match this application's committed schema frontier. */
import { readFileSync } from 'node:fs';
import { createReadAccess } from './index.ts';
import type { ApplicationSchemaSeed, ReadAccess } from './contracts.ts';

const seed = JSON.parse(
  readFileSync(new URL('./application_schema.json', import.meta.url), 'utf8'),
) as ApplicationSchemaSeed;

export function require_application_schema(database_path: string): void {
  const expected = seed.alembicRevision;
  let reader: ReadAccess | undefined;
  let actual: string[] | null;
  try {
    reader = createReadAccess(database_path, { busyTimeoutMs: 0 });
    actual = reader.read((sql) => {
      if (!sql.get("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"))
        return null;
      return sql
        .all<{ version_num: string }>(
          'SELECT version_num FROM alembic_version ORDER BY version_num',
        )
        .map((row) => String(row.version_num));
    });
  } catch (cause) {
    throw new Error(
      `Database schema admission failed: actual=<unreadable>; expected=${JSON.stringify([expected])}`,
      { cause },
    );
  } finally {
    reader?.close();
  }
  if (actual?.length !== 1 || actual[0] !== expected)
    throw new Error(
      `Database schema admission failed: actual=${actual === null ? '<missing alembic_version>' : JSON.stringify(actual)}; expected=${JSON.stringify([expected])}`,
    );
}
