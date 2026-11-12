/** Small source-reference mutations used by the incremental projection writer. */
import type { ViewRowKind } from './contracts.ts';
export type LookupKind =
  'tool' | 'permission' | 'user_input' | 'sender_message' | 'credential_request';
export interface EventEffect {
  row_kind: ViewRowKind | null;
  focus_visible: boolean;
  lookup_kind: LookupKind | null;
  lookup_id: string | null;
  settlement: boolean;
  opens_turn: boolean;
  closes_turn: boolean;
  changes_plan: boolean;
  sender_message_id: string | null;
  reconciles_sequence: number | null;
}
