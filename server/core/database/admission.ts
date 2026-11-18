/** An existing database must match this application's committed schema frontier. */
import { readFileSync } from 'node:fs';
import { createReadAccess } from './index.ts';
import type { ApplicationSchemaSeed, ReadAccess } from './contracts.ts';

/** This application's seed. Its revision is the only one startup admits. */
export const application_schema_seed = JSON.parse(
  readFileSync(new URL('./application_schema.json', import.meta.url), 'utf8'),
) as ApplicationSchemaSeed;

/** The alembic_version rows through a reader that never writes. Null when the table is missing. */
export function read_schema_revisions(database_path: string): string[] | null {
  let reader: ReadAccess | undefined;
  try {
    reader = createReadAccess(database_path, { busyTimeoutMs: 0 });
    return reader.read((sql) => {
      if (!sql.get("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"))
        return null;
      return sql
        .all<{ version_num: string }>(
          'SELECT version_num FROM alembic_version ORDER BY version_num',
        )
        .map((row) => String(row.version_num));
    });
  } finally {
    reader?.close();
  }
}

/** Refuse a database at any revision other than `expected`. */
export function require_schema_revision(database_path: string, expected: string): void {
  let actual: string[] | null;
  try {
    actual = read_schema_revisions(database_path);
  } catch (cause) {
    throw new Error(
      `Database schema admission failed: actual=<unreadable>; expected=${JSON.stringify([expected])}`,
      { cause },
    );
  }
  if (actual?.length !== 1 || actual[0] !== expected)
    throw new Error(
      `Database schema admission failed: actual=${actual === null ? '<missing alembic_version>' : JSON.stringify(actual)}; expected=${JSON.stringify([expected])}`,
    );
}

export function require_application_schema(database_path: string): void {
  require_schema_revision(database_path, application_schema_seed.alembicRevision);
}
