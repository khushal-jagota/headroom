/** The server keeps one identity across address changes and moves. */
import type { SchemaStep } from './contracts.ts';
export const server_link_step: SchemaStep = {
  revision: 'ts_0005_server_link',
  previous_revision: 'ts_0004_drop_ideas',
  apply(tx) {
    tx.run(
      "CREATE TABLE server_link (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), server_id TEXT NOT NULL, move_number INTEGER NOT NULL CHECK(move_number >= 0 AND move_number <= 9007199254740991), server_url TEXT, state TEXT NOT NULL CHECK(state IN ('active','moved')), CHECK(state != 'moved' OR server_url IS NOT NULL))",
    );
  },
};
