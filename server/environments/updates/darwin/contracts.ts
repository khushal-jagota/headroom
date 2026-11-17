import type { ReleaseExecution } from '../../release/contracts.ts';
import type { ReleaseCommandResult } from '../../release/process.ts';

export interface DarwinHomeIdentity {
  readonly version: 2;
  readonly uid: number;
  readonly root: string;
  readonly label: string;
  readonly domain: string;
  readonly home_plist: string;
  readonly backup_plist: string;
  readonly installer_node: string;
}
export interface DarwinHomeProcess {
  readonly pid: number;
  readonly parent: number;
  readonly uid: number;
  readonly birth: string;
}
export type DarwinHomeCommand = (
  argv: readonly string[],
  execution: ReleaseExecution,
  timeout_seconds: number,
  allow_nonzero?: boolean,
) => Promise<ReleaseCommandResult>;
export interface DarwinHomeIO {
  readonly execution: ReleaseExecution;
  readonly environment: Readonly<NodeJS.ProcessEnv>;
  readonly uid: number;
  readonly account_home: string;
  readonly command: DarwinHomeCommand;
  readonly process: (pid: number) => DarwinHomeProcess | null;
  readonly processes: (deadline: number) => ReadonlyMap<number, DarwinHomeProcess>;
}
export interface DarwinHomeJob {
  readonly [key: string]: unknown;
  readonly Label: string;
  readonly ProgramArguments: readonly string[];
  readonly WorkingDirectory: string;
  readonly EnvironmentVariables: Readonly<Record<string, string>>;
}
