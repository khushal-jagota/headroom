/** Request delivery uses the existing concrete core and its record transactions. */
import type { Database } from '../../core/database/contracts.ts';
import type { SqliteProcessConversationSystem } from '../../conversation/system.ts';
import type { ConversationHttpWriteScope } from '../../conversation/http/writes.ts';
export interface CredentialRequestRuntime {
  readonly database: Database;
  readonly core: SqliteProcessConversationSystem;
  readonly writes: ConversationHttpWriteScope;
  readonly key_path: string;
  now(): number;
}
