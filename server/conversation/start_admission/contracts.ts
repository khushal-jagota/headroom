import type { Database } from '../../core/database/contracts.ts';
import type { TurnToken } from '../backends/contracts.ts';

export type AdmissionDatabase = Pick<Database, 'read' | 'write'>;
export type WorkClass = 'worker' | 'supervisor';
export type ReservationPhase = 'eligible' | 'preparing' | 'queued' | 'attempted' | 'failed';
export interface StartRequest {
  readonly identity: string;
  readonly work_class: WorkClass;
  readonly destination: string;
}
export interface StartSnapshot {
  readonly request: StartRequest;
  readonly state: 'active' | 'deferred' | 'queued' | 'uncertain';
  readonly turn: TurnToken | null;
  readonly permit_identity: string | null;
}
