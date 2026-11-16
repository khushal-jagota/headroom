/** Legacy layout discovery and owned workspace cleanup scheduling. */
import type { ComposedBackgroundService, OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import type { ChangeSignal } from '../../core/changes/index.ts';

export interface ScratchLayout {
  readonly repository: string;
  readonly folders: readonly string[];
}
/** One pass can refuse further operation dispatch after lifecycle shutdown. */
export interface ScratchPassOptions {
  signal?: AbortSignal;
  admit_path?: (path: string) => string;
  git_execution?: {
    readonly children: OwnedOneShotChildren;
    readonly environment: Readonly<NodeJS.ProcessEnv>;
  };
}
export interface GitResult {
  returncode: number;
  stdout: string;
  stderr: string;
}
export interface ScratchRuntimeOptions {
  readonly changes?: Pick<ChangeSignal, 'subscribe'>;
  readonly layout: ScratchLayout | null;
  readonly db_path: string;
  readonly test_mode: boolean;
  now_unix(): number;
  assert_database_owned(): void;
  /** Composition supplies the real pass. A held pass also proves lifecycle fencing. */
  clean_workspaces(
    db_path: string,
    now: number,
    options: ScratchPassOptions,
  ): Promise<import('../../ticket_workspaces/contracts.ts').CleanupResult>;
}
export type ScratchRuntime = ComposedBackgroundService;
