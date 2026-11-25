/** Shared update view contracts. These types have no server runtime dependencies. */
export type UpdateState =
  'not_checked' | 'check_unavailable' | 'up_to_date' | 'available' | 'updating' | 'failed';
export type UpdateAction = 'update' | 'retry';

export type UpdateMode = 'now' | 'idle';

export type UpdateAttemptState = 'waiting' | 'preparing' | 'installing' | 'done' | 'failed';

export interface MachineUpdateState {
  attempt_id: string | null;
  machine_id: string;
  name: string;
  platform: string;
  present: boolean;
  build_id: string | null;
  target_build_id: string | null;
  changed: boolean;
  state: 'current' | 'available' | 'waiting' | 'updating' | 'failed' | 'unavailable';
  request_id: string | null;
  detail: string | null;
  can_retry: boolean;
}
export interface UpdateInterruption {
  stops_agents: boolean;
  agents: readonly string[];
  machine_ids: readonly string[];
}
export interface UpdateAttemptView {
  attempt_id: string;
  from_sha: string;
  to_sha: string;
  mode: UpdateMode;
  state: UpdateAttemptState;
  completed_at?: string | null;
  detail: string | null;
  deadline_at: string | null;
  interruption: UpdateInterruption | null;
}
