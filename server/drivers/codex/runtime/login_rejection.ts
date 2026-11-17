import type { Database } from '../../../core/database/contracts.ts';

/** Remember only the nonsecret generation refused by a real Codex turn. */
export class DurableCodexLoginRejection {
  private readonly database: Database;
  private readonly now: () => Date;
  constructor(database: Database, now: () => Date = () => new Date()) {
    this.database = database;
    this.now = now;
  }
  remember(
    generation:
      import('../../../conversation/snapshot/provider_contracts.ts').CodexLoginGeneration | null,
  ): void {
    if (generation === null) return;
    this.database.write((tx) =>
      tx.run(
        'INSERT INTO codex_login_rejection(id,account_id,last_refresh,rejected_at) ' +
          'VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET account_id=excluded.account_id,' +
          'last_refresh=excluded.last_refresh,rejected_at=excluded.rejected_at',
        [generation.account_id, generation.last_refresh, Math.trunc(this.now().getTime() / 1000)],
      ),
    );
  }
  forget(
    generation:
      import('../../../conversation/snapshot/provider_contracts.ts').CodexLoginGeneration | null,
  ): void {
    if (generation === null) return;
    this.database.write((tx) =>
      tx.run('DELETE FROM codex_login_rejection WHERE account_id=? AND last_refresh=?', [
        generation.account_id,
        generation.last_refresh,
      ]),
    );
  }
  applies_to(
    generation:
      import('../../../conversation/snapshot/provider_contracts.ts').CodexLoginGeneration | null,
  ): boolean {
    if (generation === null) return false;
    return this.database.read(
      (sql) =>
        sql.get('SELECT 1 FROM codex_login_rejection WHERE account_id=? AND last_refresh=?', [
          generation.account_id,
          generation.last_refresh,
        ]) !== undefined,
    );
  }
}

/** Use the same durable generation memory for cards, sign-in, and backend factories. */
export function create_durable_codex_login_rejection(
  database: Database,
  now: () => Date = () => new Date(),
): import('../../../conversation/snapshot/provider_contracts.ts').CodexLoginRejectionMemory {
  return new DurableCodexLoginRejection(database, now);
}
