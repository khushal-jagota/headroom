/** What the server hands every driver. Replaces the per-backend dependency bags. */
import type { Database } from '../../core/database/contracts.ts';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import type { WorkerSupervisionRuntime } from '../../worker_supervision/runtime.ts';

export interface DriverHost {
  readonly database: Database;
  readonly environment: Readonly<Record<string, string | undefined>>;
  readonly panels_server_url: string;
  /** The canonical, resolved Worker process root. */
  readonly worker_process_root: string;
  readonly child_processes: OwnedChildProcesses;
  readonly one_shot_children: OwnedOneShotChildren;
  readonly worker_supervision: WorkerSupervisionRuntime;
  now(): Date;
  /** The first executable of that name on the host PATH, or the bare name when none is found. */
  executable_on_path(name: string): string;
}
