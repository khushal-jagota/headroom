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
