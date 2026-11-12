import type { ConversationSystem } from '../conversation/contracts.ts';
import type { Database } from '../core/database/contracts.ts';
import { read_agent_conversation } from '../runtime/conversation_start.ts';
import { require_manager_can_delete, delete_manager_rows } from './data.ts';
import type { QuarantinedFiles } from '../files/lifecycle.ts';
import {
  quarantine_manager_files,
  restore_quarantined_files,
  purge_quarantined_files,
} from '../files/lifecycle.ts';
/** Serialize Manager lifecycle operations across asynchronous conversation calls. */
class LifecycleLock {
  private tail: Promise<void> = Promise.resolve();
  async run<T>(operation: () => Promise<T>): Promise<T> {
    const previous = this.tail;
    let release!: () => void;
    this.tail = new Promise<void>((resolve) => {
      release = resolve;
    });
    await previous;
    try {
      return await operation();
    } finally {
      release();
    }
  }
}
const locks = new Map<string, LifecycleLock>();
export function manager_lifecycle_lock(id: string): LifecycleLock {
  let lock = locks.get(id);
  if (!lock) {
    lock = new LifecycleLock();
    locks.set(id, lock);
  }
  return lock;
}

export async function delete_manager(
  system: ConversationSystem,
  database: Database,
  id: string,
  db_path: string,
): Promise<{ manager_id: string; title: string }> {
  return manager_lifecycle_lock(id).run(async () => {
    for (;;) {
      const item = database.read((sql) => require_manager_can_delete(sql, id));
      const current = read_agent_conversation(database, item.manager_agent_key);
      if (current !== null) {
        await system.kill(current);
        database.write((tx) =>
          tx.run('UPDATE agents SET conversation_id=NULL WHERE agent_key=? AND conversation_id=?', [
            item.manager_agent_key,
            current,
          ]),
        );
      }
      let quarantined: QuarantinedFiles | null = null;
      let outcome: { manager_id: string; title: string } | null;
      try {
        outcome = database.write((tx) => {
          require_manager_can_delete(tx, id);
          if (
            tx.get<{ conversation_id: string | null }>(
              'SELECT conversation_id FROM agents WHERE agent_key=?',
              [item.manager_agent_key],
            )?.conversation_id != null
          )
            return null;
          quarantined = quarantine_manager_files(db_path, id);
          return delete_manager_rows(tx, id);
        });
      } catch (error) {
        restore_quarantined_files(quarantined);
        throw error;
      }
      if (outcome) {
        purge_quarantined_files(quarantined);
        return outcome;
      }
    }
  });
}
