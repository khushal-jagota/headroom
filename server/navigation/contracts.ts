import type { ConversationAttention } from '../conversation/contracts.ts';
import type { CommitStep, ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type { NavigationRecord, RecordKey, CurrentAuthority, ViewRequest, ViewContent } from '../../web/src/lib/navigation/contracts.ts';
export type NavigationKey = readonly [kind: string, id: string];
/** A domain owns its roots and declares every source row that can invalidate them. */
export interface NavigationSource {
  kind: string;
  roots(sql: ReadSql): readonly string[];
  affected(sql: ReadSql, dirty: NavigationKey): readonly string[];
  /** The owning domain supplies its bounded section windows and their record closures. */
  view?(sql: ReadSql, request: ViewRequest): Partial<ViewContent>;
  derive(sql: ReadSql, id: string): { records: readonly NavigationRecord[]; dependencies: readonly NavigationKey[] };
}
/** Runtime knowledge comes from the conversation contract, never from persisted rows. */
export interface NavigationAuthority {
  capture(): CurrentAuthority;
  validate(authority: CurrentAuthority): void;
  watch(authority: CurrentAuthority, invalidate: () => void): () => void;
}
export interface NavigationRegistry {
  sources: readonly NavigationSource[];
  finalize(tx: TransactionSql): void;
  rebuild(tx: TransactionSql): void;
}
export type { NavigationRecord, RecordKey };

/** The conversation dependency supplies facts synchronously in the source transaction. */
export type PublicationFactory = (publish: (tx: TransactionSql, conversationId: string, attention: ConversationAttention) => undefined) => CommitStep;
