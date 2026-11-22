/** A driver's executor half: what a Mac or Linux executor machine needs to run it. */
import { copy_environment } from '../../core/environment.ts';
import type { BackendChildFactory } from '../../conversation/backends/contracts.ts';
import type { ModelCatalogCleanupFailed } from '../../conversation/backends/catalog_cleanup.ts';
import type {
  OwnedChildProcesses,
  OwnedOneShotChildren,
  StopOptions,
} from '../../lifecycle/contracts.ts';
import type { WorkerSupervisionRuntime } from '../../worker_supervision/runtime.ts';
import type { BackendUsageAdapter } from '../../conversation/backend_usage/contracts.ts';
import type { DriverSignInController } from './sign_in.ts';
import type { DriverStatus } from './status.ts';

/** What an executor machine hands each driver. It has no Panels database. */
export interface ExecutorDriverHost {
  /** The machine's provider environment, with every driver's paths resolved for this machine. */
  readonly environment: Readonly<Record<string, string | undefined>>;
  readonly panels_server_url: string;
  readonly child_processes: OwnedChildProcesses;
  readonly one_shot_children: OwnedOneShotChildren;
  readonly worker_supervision: WorkerSupervisionRuntime;
  /** The executable this driver launches: its override, else the PATH match, else the bare name. */
  readonly executable: string;
  /** The resolved home directory of this driver's manifest. */
  readonly home: string | null;
}
/** The machine catalog's custody, shared by every driver's model list reads. */
export interface ExecutorCatalogCustody {
  /** One owned read: refused once the catalog stops, cancelled by settle and stop. */
  probe<T>(read: (signal: AbortSignal) => Promise<T>): Promise<T>;
  /** Keep a cleanup failure for the catalog's stop, and return the error to throw. */
  retain(error: ModelCatalogCleanupFailed): Error;
  /** A background failure of a source this driver owns. */
  log(error: unknown): void;
  /** The options the catalog's stop received, once it has received them. */
  stop_options(): StopOptions | undefined;
}
/** A driver's model list and card on an executor machine. */
export interface ExecutorCatalogSource {
  readonly sign_in?: DriverSignInController;
  readonly allowance_usage?: BackendUsageAdapter;
  readonly status: DriverStatus;
  /** Cancel reads this source owns outside custody.probe. */
  cancel?(reason: unknown): Promise<void>;
  /** Release what this source owns. */
  shutdown?(): Promise<void>;
}
export interface ExecutorDriver {
  /** A fresh child factory for one start. */
  child_factory(host: ExecutorDriverHost): BackendChildFactory;
  catalog(host: ExecutorDriverHost, custody: ExecutorCatalogCustody): ExecutorCatalogSource;
}
/** A launch whose child inherits the machine's environment before its own overrides. */
export function with_machine_environment<
  T extends { readonly environment_overrides?: readonly (readonly [string, string])[] },
>(launch: T, host: ExecutorDriverHost): T {
  return {
    ...launch,
    environment_overrides: [
      ...Object.entries(copy_environment(host.environment)),
      ...(launch.environment_overrides ?? []),
    ],
  };
}
