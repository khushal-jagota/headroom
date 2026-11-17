/** Bounded, read-only evidence from this synthetic executor, never credential files. */
import Database from 'better-sqlite3';
import { readdir, readFile, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

export async function native_failure_snapshot(root: string, evidence: string): Promise<void> {
  const result: Record<string, unknown> = {};
  for (const [name, table] of [
    ['outbox.sqlite3', 'items'],
    ['commands.sqlite3', 'commands'],
  ] as const) {
    let database: Database.Database | undefined;
    try {
      database = new Database(join(root, 'spool', name), {
        readonly: true,
        fileMustExist: true,
        timeout: 1000,
      });
      const rows = database.prepare(`SELECT * FROM ${table} ORDER BY rowid LIMIT 100`).all();
      result[name] = {
        rows: rows.map((row) =>
          Object.fromEntries(
            Object.entries(row as Record<string, unknown>).map(([key, value]) => [
              key,
              Buffer.isBuffer(value) ? value.toString('utf8') : value,
            ]),
          ),
        ),
      };
    } catch (error) {
      result[name] = { error: String(error) };
    } finally {
      database?.close();
    }
  }
  try {
    const logs: Record<string, unknown> = {};
    for (const name of (await readdir(join(root, 'logs'))).slice(0, 10)) {
      const path = join(root, 'logs', name),
        info = await stat(path);
      if (info.isFile() && info.size <= 1024 * 1024) logs[name] = await readFile(path, 'utf8');
    }
    result.logs = logs;
  } catch (error) {
    result.logs = { error: String(error) };
  }
  await writeFile(join(evidence, 'executor-state.json'), JSON.stringify(result, null, 2) + '\n');
}
