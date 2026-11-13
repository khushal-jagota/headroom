/** Permanent custody joins the cancelable controller and every producer before storage closes. */
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type { MacCommandServiceContract } from './command_contracts.ts';
import type { MacMaintenanceOperations } from './maintenance_contracts.ts';
import type { MacRetirementContract, MacRetirementDependencies } from './retirement_contracts.ts';

export interface MacRunnerLifetimeDependencies extends MacRetirementDependencies {
  readonly commands: () => MacCommandServiceContract;
  readonly maintenance: MacMaintenanceOperations;
  readonly retirement: MacRetirementContract;
  on_failure(error: Error): void;
}
export interface MacRunnerLifetimeContract {
  readonly permanently_stopped: boolean;
  /** Resource faults seal admission before the host starts its owned cleanup. */
  seal(): void;
  stop(options: StopOptions): Promise<void>;
  settled(): Promise<void>;
}
