/** An explicit move starts a new conversation from the same canonical work record. */
export type WorkMoveOwner =
  { kind: 'ticket'; id: string } | { kind: 'manager'; id: string } | { kind: 'chief' };
export interface WorkMoveRequest {
  owner: WorkMoveOwner;
  machine_id: string;
}
export interface WorkMoveProof {
  published_branch: string | null;
  published_revision: string | null;
}
export interface WorkMoveResult {
  owner: WorkMoveOwner;
  title: string;
  machine_id: string;
  moved: boolean;
  error: string | null;
}
export interface WorkMoveBatchResult {
  results: WorkMoveResult[];
}

export interface WorkMoveStatus {
  move_everything_machine_id: string | null;
}

/** Browser operations retain progress independently of the request and screen. */
export type WorkMovePhase =
  | 'queued'
  | 'stopping'
  | 'verifying'
  | 'recording'
  | 'recovering'
  | 'starting'
  | 'complete'
  | 'failed';
export interface WorkMovePhaseTime {
  phase: WorkMovePhase;
  started_at_ms: number;
  finished_at_ms: number | null;
}
export interface WorkMoveItem {
  conversation_id: string | null;
  owner: WorkMoveOwner;
  title: string;
  phase: WorkMovePhase;
  phases: WorkMovePhaseTime[];
  result: WorkMoveResult | null;
}
export interface WorkMoveOperation {
  operation_id: string;
  machine_id: string;
  everything: boolean;
  started_at_ms: number;
  finished_at_ms: number | null;
  items: WorkMoveItem[];
}
export interface WorkMoveOperationsResponse {
  operations: WorkMoveOperation[];
}
