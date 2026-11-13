/** Immutable evidence of turns interrupted by a deployment or process restart. */
export const CAPTURE_VERSION = 1;
/** The file contract's value refusals remain distinct from IO and unexpected failures. */
export class CaptureValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ValueError';
  }
}
export type CaptureState = 'captured' | 'unavailable';
export type RecoveryState =
  'awaiting_action' | 'resumed' | 'unable_to_resume' | 'closed' | 'historical';
export type OwnerKind = 'ticket' | 'manager' | 'chief' | 'unknown';
export interface CapturedTurn {
  readonly conversation_id: string;
  readonly turn_number: number;
  readonly occurrence_id: string | null;
  readonly runtime_epoch: string | null;
  readonly original_status: 'running' | 'intent';
  readonly display_name: string;
  readonly owner_kind: OwnerKind;
  readonly owner_id: string | null;
  readonly claim_revision: number | null;
  /** Raw persisted evidence. Manifest publication validates its allowed values. */
  readonly claim: string | null;
}
export interface CaptureManifest {
  readonly deployment_id: string;
  readonly capture_id: string;
  readonly captured_at: number;
  readonly state: CaptureState;
  readonly detail: string | null;
  readonly turns: readonly CapturedTurn[];
  readonly version: typeof CAPTURE_VERSION;
}
export interface InterruptionEntry {
  readonly interruption_id: string;
  readonly deployment_id: string;
  readonly conversation_id: string | null;
  readonly display_name: string;
  readonly owner_kind: OwnerKind;
  readonly owner_id: string | null;
  readonly original_status: string;
  readonly recovery_state: RecoveryState;
  readonly detail: string;
}
export interface InterruptionAccount {
  readonly deployment_id: string;
  capture_state: CaptureState;
  detail: string | null;
  readonly entries: InterruptionEntry[];
}
export interface InterruptionSummary {
  readonly accounts: InterruptionAccount[];
}
