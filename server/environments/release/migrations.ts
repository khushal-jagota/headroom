/** Existing databases must reach the candidate frontier through Python before cutover. */
import type { ReadSql } from '../../core/database/contracts.ts';
import { AppValidationError } from './contracts.ts';
import type { MigrationGraph } from './contracts.ts';
import { createHash } from 'node:crypto';
import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';

function validate_migration_topology(revisions: MigrationGraph['revisions']): void {
  const visiting = new Set<string>(),
    visited = new Set<string>();
  const visit = (revision: string): void => {
    if (visiting.has(revision))
      throw new AppValidationError('invalid generated migration graph: cycle');
    if (visited.has(revision)) return;
    visiting.add(revision);
    for (const parent of revisions[revision]!) {
      if (parent === revision)
        throw new AppValidationError('invalid generated migration graph: self-parent');
      if (!Object.hasOwn(revisions, parent))
        throw new AppValidationError('invalid generated migration graph: dangling parent');
      visit(parent);
    }
    visiting.delete(revision);
    visited.add(revision);
  };
  for (const revision of Object.keys(revisions)) visit(revision);
  const heads = new Set(Object.keys(revisions));
  for (const parents of Object.values(revisions))
    for (const parent of parents) heads.delete(parent);
  if (heads.size !== 1)
    throw new AppValidationError('invalid generated migration graph: expected exactly one head');
}

/** Build input, bound to raw migration bytes without a runtime Python parser. */
export async function read_migration_graph(root: string): Promise<MigrationGraph> {
  let graph: unknown;
  try {
    graph = JSON.parse(
      new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(
        await readFile(join(root, 'server/environments/release/migration_graph.json')),
      ),
    );
  } catch (error) {
    if (error instanceof SyntaxError || error instanceof TypeError)
      throw new AppValidationError('invalid generated migration graph');
    throw error;
  }
  if (
    graph === null ||
    typeof graph !== 'object' ||
    Array.isArray(graph) ||
    Object.keys(graph).sort().join(',') !== 'revisions,source_digest,version' ||
    !('version' in graph) ||
    graph.version !== 1 ||
    !('source_digest' in graph) ||
    typeof graph.source_digest !== 'string' ||
    !/^[0-9a-f]{64}$/.test(graph.source_digest) ||
    !('revisions' in graph) ||
    graph.revisions === null ||
    typeof graph.revisions !== 'object' ||
    Array.isArray(graph.revisions) ||
    !Object.values(graph.revisions).every(
      (parents) => Array.isArray(parents) && parents.every((parent) => typeof parent === 'string'),
    )
  )
    throw new AppValidationError('invalid generated migration graph');
  const directory = join(root, 'src/planner/core/migrations/versions');
  const digest = createHash('sha256');
  for (const name of (await readdir(directory))
    .filter((name) => name.endsWith('.py'))
    .sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)))) {
    digest.update(name + '\0');
    digest.update(await readFile(join(directory, name)));
    digest.update('\0');
  }
  if (digest.digest('hex') !== graph.source_digest)
    throw new AppValidationError(
      'migration graph differs from the current Python migration source',
    );
  validate_migration_topology((graph as MigrationGraph).revisions);
  return graph as MigrationGraph;
}

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
