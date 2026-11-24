/** The database owns server authority. A copy preserves this identity and its move floor. */
import { randomUUID } from 'node:crypto';
import type { Database, ReadSql } from '../core/database/contracts.ts';
import { createReadAccess } from '../core/database/index.ts';
import type { ServerLocation } from './contracts.ts';
import { parse_server_location, validate_server_address } from './logic.ts';

export function read_server_location(sql: ReadSql): ServerLocation | null {
  const row = sql.get<ServerLocation>(
    'SELECT server_id, move_number, server_url, state FROM server_link WHERE singleton = 1',
  );
  return row ? parse_server_location(row) : null;
}
export function initialize_server_location(database: Database): ServerLocation {
  const existing = database.read(read_server_location);
  if (existing) return existing;
  return database.write((tx) => {
    tx.run(
      "INSERT OR IGNORE INTO server_link(singleton,server_id,move_number,server_url,state) VALUES(1,?,0,NULL,'active')",
      [randomUUID()],
    );
    return read_server_location(tx)!;
  });
}
/** Startup checks this read-only before update recovery or record reconciliation. */
export function read_persisted_server_location(path: string): ServerLocation | null {
  const reader = createReadAccess(path);
  try {
    return reader.read((sql) => {
      if (!sql.get("SELECT 1 FROM sqlite_master WHERE type='table' AND name='server_link'"))
        return null;
      return read_server_location(sql);
    });
  } finally {
    reader.close();
  }
}
/** The caller supplies its retained HTTPS transport. No credentials enter the address check. */
export async function set_server_address(
  database: Database,
  value: string,
  probe: (origin: string) => Promise<unknown>,
): Promise<ServerLocation> {
  const address = validate_server_address(value);
  const answer = parse_server_location(await probe(address));
  return database.write((tx) => {
    const current = read_server_location(tx);
    if (!current) throw new Error('The server identity is not initialized.');
    if (
      answer.server_id !== current.server_id ||
      answer.move_number !== current.move_number ||
      answer.state !== 'active' ||
      current.state !== 'active'
    )
      throw new Error('The address does not answer for the current active server.');
    tx.run('UPDATE server_link SET server_url=? WHERE singleton=1', [address]);
    return { ...current, server_url: address };
  });
}
