import type { RecordKind } from '../../../web/src/lib/navigation/contracts.ts';
export type Key = readonly [kind: string, id: string];
/** Navigation records arrive from the canonical source derivation, not request bodies. */
export type Fact = Record<string, any>;
export interface ProjectionMetadata {
  kind: string; id: string; content_hash: string;
  conversation_id: string | null; parent_kind: string | null; parent_id: string | null;
  owner_kind: string | null; owner_id: string | null; project_id: string | null;
  working: number; out_claim: number; review: number; unread: number; settled: number;
}
export interface Membership {
  section_key: string; profile: string; locale: string; kind: RecordKind; id: string;
  rank: number; title_key: Buffer; id_key: Buffer;
}
/** Intl exposes comparison rather than ICU sort-key bytes. Persist its complete operands. */
export interface SortOperand {
  mode: 'binary' | 'base' | 'variant';
  text: string;
  prefix: number | null;
}
export type Position = [number, Buffer, Buffer, string, string];
export type ReviewPosition = [number, ...Position];
export type WindowPosition = Position | ReviewPosition;
export interface ReviewMessage {
  conversation_id: string; address_id: string; owner_kind: string; owner_id: string;
  waiting_since: number;
}
