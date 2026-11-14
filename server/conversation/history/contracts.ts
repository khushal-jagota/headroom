/** Shared public history contract. */
export type {
  ConversationHistoryEntry,
  ConversationHistoryPage,
  HistoryOwnerKind,
} from '../../../web/src/lib/catalogs/history.ts';
import type { HistoryOwnerKind } from '../../../web/src/lib/catalogs/history.ts';
export interface HistoryPageInput {
  readonly kind: HistoryOwnerKind;
  readonly owner_id: string;
  readonly offset: number;
  readonly limit: number;
  readonly selected: string | null;
  readonly prefix: string | null;
  readonly after: number;
  readonly locale: string;
  readonly timezone: string;
}
