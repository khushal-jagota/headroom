/** First installation keeps its request separate from native service effects. */
import type { AppManifest, ReleaseExecution } from '../environments/release/contracts.ts';
import type { ReleaseCommandResult } from '../environments/release/process.ts';

export interface HomeInstall {
  readonly root: string;
  readonly service: string;
  readonly port: number;
  readonly isolated_providers: boolean;
}
export interface HomeInstallRequest extends HomeInstall {
  readonly archive: string;
  readonly archive_sha256: string;
  readonly revision: string;
}
/** The installer's stdin request. Only the update token file receives the credential; services see the request without it. */
export interface HomeInstallSubmission extends HomeInstallRequest {
  readonly update_token: string;
}
export interface HomeInstallationFile {
  readonly path: string;
  readonly content: string;
  readonly mode: number;
  readonly format?: 'plist-json';
}
export interface HomeInstallationLink {
  readonly path: string;
  readonly target: string;
}
export type HomeCommand = (
  argv: readonly string[],
  execution: ReleaseExecution,
  timeout_seconds: number,
  allow_nonzero?: boolean,
) => Promise<ReleaseCommandResult>;

/** Every attempted activation remains owned even when the command returns an error. */
export interface HomeServicePlan {
  readonly files: readonly HomeInstallationFile[];
  readonly links: readonly HomeInstallationLink[];
  preflight(execution: ReleaseExecution): Promise<void>;
  start_home(execution: ReleaseExecution): Promise<void>;
  start_machine(execution: ReleaseExecution): Promise<void>;
  start_auxiliaries(execution: ReleaseExecution): Promise<void>;
  stop(execution: ReleaseExecution, assert_owned: (path: string) => Promise<void>): Promise<void>;
  finish_cleanup?(execution: ReleaseExecution): Promise<void>;
}
export interface HomeInstallHost {
  /** Canonical Node of the process that performs installation. */
  readonly node_executable: string;
  readonly system: 'linux' | 'darwin';
  readonly architecture: string;
  readonly account_home: string;
  readonly uid: number;
  readonly environment: Readonly<NodeJS.ProcessEnv>;
  readonly command: HomeCommand;
}
export interface HomeInstallDependencies extends HomeInstallHost {
  readonly execution: ReleaseExecution;
  readonly extract: (
    archive: string,
    destination: string,
    digest: string,
    revision: string,
    execution: ReleaseExecution,
  ) => Promise<AppManifest>;
  readonly admit: (
    app: string,
    manifest: AppManifest,
    execution: ReleaseExecution,
  ) => Promise<void>;
  readonly initialize_database: (
    app: string,
    path: string,
    execution: ReleaseExecution,
    machine_root?: string,
  ) => Promise<void>;
  readonly prepare_machine: (
    app: string,
    root: string,
    execution: ReleaseExecution,
  ) => Promise<void>;
  readonly machine_healthy: (
    request: HomeInstallRequest,
    execution: ReleaseExecution,
    started_ms: number,
  ) => Promise<void>;
  readonly healthy: (port: number, revision: string, signal: AbortSignal) => Promise<boolean>;
  readonly service: (
    request: HomeInstallRequest,
    host: HomeInstallHost,
  ) => Promise<HomeServicePlan>;
}
/** Stop and remove the home services of `root`, refusing any service that names another root. */
export type HomeServiceRemoval = (
  root: string,
  service: string,
  host: HomeInstallHost,
  execution: ReleaseExecution,
) => Promise<void>;
/**
 * The move mode installs a held server around copied state. It has no database creation, no
 * credential and no machine code, because the target machine already runs machine code.
 */
export interface HomeMoveDependencies extends HomeInstallHost {
  readonly execution: ReleaseExecution;
  readonly extract: HomeInstallDependencies['extract'];
  readonly admit: HomeInstallDependencies['admit'];
  readonly healthy: HomeInstallDependencies['healthy'];
  /** The home's service plan without its machine code service. */
  readonly service: HomeInstallDependencies['service'];
  /** Integrity test and schema admission, run by the installed app. */
  readonly check_database: (
    app: string,
    path: string,
    execution: ReleaseExecution,
  ) => Promise<void>;
  readonly remove_service: HomeServiceRemoval;
}
/** A bounded observer cannot release descriptors still used by a native cleanup. */
export class HomeCleanupUnsettled extends Error {
  readonly settled: Promise<void>;
  constructor(message: string, settled: Promise<void>, cause: unknown) {
    super(message, { cause });
    this.settled = settled;
  }
}
