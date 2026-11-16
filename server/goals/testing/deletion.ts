/** Exercise the canonical Goal deletion over synthetic databases and private file trees. */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { createDatabase } from '../../core/database/index.ts';
import { delete_goal } from '../actions.ts';
interface Spec {
  now: number;
  goal_id: string;
  cases: { scenario: string; db_path: string }[];
  tables: string[];
}
export function run(spec: Spec) {
  return spec.cases.map((c) => {
    const database = createDatabase(c.db_path);
    try {
      let outcome: unknown;
      try {
        outcome = delete_goal(
          database,
          c.db_path,
          spec.goal_id,
          { kind: 'owner', id: 'owner' },
          spec.now,
        );
      } catch (e) {
        outcome = { error: e instanceof Error ? e.message : String(e) };
      }
      return {
        scenario: c.scenario,
        outcome,
        tables: database.read((sql) =>
          Object.fromEntries(
            spec.tables.map((table) => [table, sql.all(`SELECT * FROM ${table} ORDER BY rowid`)]),
          ),
        ),
        files: file_tree(join(dirname(c.db_path), 'files')),
        foreign_keys: database.read((sql) => sql.all('SELECT * FROM pragma_foreign_key_check')),
      };
    } finally {
      database.close();
    }
  });
}
function file_tree(root: string): { directories: string[]; files: Record<string, string> } {
  const directories: string[] = [],
    files: Record<string, string> = {};
  const walk = (folder: string) => {
    for (const name of readdirSync(folder).sort()) {
      const path = join(folder, name),
        key = relative(root, path);
      if (statSync(path).isDirectory()) {
        directories.push(key);
        walk(path);
      } else files[key] = readFileSync(path).toString('hex');
    }
  };
  walk(root);
  return { directories: directories.sort(), files };
}
