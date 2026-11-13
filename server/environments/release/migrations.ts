/** Existing databases must reach the candidate frontier through Python before cutover. */
import type { ReadSql } from '../../core/database/contracts.ts';
import { AppValidationError } from './contracts.ts';
import type { MigrationGraph } from './contracts.ts';

export function migration_frontier(graph: MigrationGraph): ReadonlySet<string> {
  const heads = new Set(Object.keys(graph.revisions));
  for (const parents of Object.values(graph.revisions))
    for (const parent of parents) heads.delete(parent);
  return heads;
}
function database_revisions(sql: ReadSql): Set<string> {
  return new Set(
    sql
      .all<{ version_num: string }>('SELECT version_num FROM alembic_version')
      .map(({ version_num }) => String(version_num)),
  );
}
/** Preserve Python's existing v1 backup decision. It does not admit a v2 migration. */
export function schema_change_pending(sql: ReadSql, graph: MigrationGraph): boolean {
  const revisions = database_revisions(sql);
  if (
    !revisions.size ||
    [...revisions].some((revision) => !Object.hasOwn(graph.revisions, revision))
  )
    throw new AppValidationError("This release does not know the database's schema revision.");
  const heads = migration_frontier(graph);
  return revisions.size !== heads.size || [...revisions].some((revision) => !heads.has(revision));
}
export function require_node_database_frontier(sql: ReadSql, graph: MigrationGraph): void {
  const revisions = database_revisions(sql),
    heads = migration_frontier(graph);
  if (
    !revisions.size ||
    revisions.size !== heads.size ||
    [...revisions].some((revision) => !heads.has(revision))
  )
    throw new AppValidationError(
      'This release requires the database to be at exactly its schema revision. ' +
        'Install the Python release that carries the same migrations first.',
    );
}
