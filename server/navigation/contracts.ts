import type { TransactionSql } from '../core/database/contracts.ts';
import type {
  NavigationRecord,
  RecordKey,
  CurrentAuthority,
} from '../../web/src/lib/navigation/contracts.ts';
export type NavigationKey = readonly [kind: string, id: string];
/** Runtime knowledge comes from the conversation contract, never from persisted rows. */
export interface NavigationAuthority {
  capture(): CurrentAuthority;
  validate(authority: CurrentAuthority): void;
  watch(authority: CurrentAuthority, invalidate: () => void): () => void;
}
export interface NavigationRegistry {
  finalize(tx: TransactionSql): void;
  rebuild(tx: TransactionSql): void;
}
export type { NavigationRecord, RecordKey };
