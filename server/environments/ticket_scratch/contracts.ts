/** Legacy layout discovery and owned workspace cleanup scheduling. */
import type { OwnedOneShotChildren } from '../../lifecycle/contracts.ts';

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
