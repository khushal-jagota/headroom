/** Reuse the Ticket host; its baseline predates the presence migration. */
import { readFileSync } from 'node:fs';
import type { Database } from '../../core/database/contracts.ts';
const schema = JSON.parse(
  readFileSync(new URL('../../core/database/application_schema.json', import.meta.url), 'utf8'),
) as { statements: string[] };
export function add_inbox_presence_table(database: Database): void {
  const statement = schema.statements.find((sql) =>
    sql.startsWith('CREATE TABLE mac_executor_presence '),
  );
  if (statement === undefined) throw new Error('Presence schema is absent');
  database.write((tx) => tx.run(statement));
}
