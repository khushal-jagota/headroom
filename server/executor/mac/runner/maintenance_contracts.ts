/** Cancelable maintenance and permanent shutdown share the command settlement owner. */
import type { Quiescence, WorkingTurn } from '../../wire.generated.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import type { MacCommandServiceContract } from './command_contracts.ts';
import type { MacRetirementContract, MacRetirementDependencies } from './retirement_contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';

export interface MacMaintenanceDependencies extends MacRetirementDependencies {
  /** Composition resolves the cycle before admission opens. No placeholder command owner. */
  readonly commands: () => MacCommandServiceContract;
  readonly retirement: MacRetirementContract;
  readonly outbox: Pick<OutboxStore, 'pending_count'>;
  /** UUID hex source for Python's update_stop_ identity. */
  new_update_id(): string;
  /** Permanent lifetime belongs to the final runner. The component fixture uses its actual native owner. */
  permanently_stopped(): boolean;
}
export interface MacMaintenanceContract {
  readonly planned_update_replay: boolean;
  working_turns(): readonly WorkingTurn[];
  quiesce(signal: AbortSignal): Promise<Quiescence>;
  resume_after_cancelled_update(): void;
  begin_planned_replay(): void;
  planned_replay_finished(signal: AbortSignal): Promise<void>;
  finish_planned_replay(): void;
  prepare_update(mode: 'now' | 'idle', request_id: string, signal: AbortSignal): Promise<boolean>;
}
export interface MacMaintenanceOperations {
  /** Capture actual operations synchronously before native cleanup can release their continuations.
   * Release replay waiters, join every admitted operation, and retain raw failures in the result. */
  finish_operations(options: StopOptions): Promise<readonly unknown[]>;
  settled_operations(): Promise<readonly unknown[]>;
}
