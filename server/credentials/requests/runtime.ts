/** Request delivery uses the existing concrete core and its record transactions. */
import type { Database } from '../../core/database/contracts.ts';
import type { SqliteProcessConversationSystem } from '../../conversation/system.ts';
export interface CredentialRequestRuntime {
  readonly database: Database;
  readonly core: SqliteProcessConversationSystem;
  readonly key_path: string;
  now(): number;
}
