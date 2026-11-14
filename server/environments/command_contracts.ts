/** Plain operator functions. Unit 20 owns argv parsing and process IO. */
import type { Config } from '../core/config.ts';
import type { EnvironmentRequest } from './registry/contracts.ts';
import type { StatusOptions } from './vps_status/contracts.ts';
import type { CommandDefinition } from '../cli/contracts.ts';
import type { ScratchPassOptions } from './ticket_scratch/contracts.ts';
import type { AppValidationOptions } from './release/contracts.ts';
import type { AppManifest } from './updates/contracts.ts';
import type { WatchdogArguments } from '../stalls/cli.ts';

export interface EnvironmentCommandTranscript {
  readonly exit_code: 0 | 1;
  readonly stdout: string;
  readonly stderr: string;
}
export interface EnvironmentCommandFailure {
  readonly exit_code: 1;
  readonly stdout: '';
  readonly failure_message: string;
  readonly stderr_prefix: string;
}
export interface PreparedOperatorLaunch {
  readonly interrupted?: Promise<never>;
  output_flushed?(): void;
  execute(): never;
  close(): void | Promise<void>;
}
export type EnvironmentCommandResult = EnvironmentCommandTranscript | EnvironmentCommandFailure;
export interface OperatorPathInputs {
  expand_path(path: string): string;
  admit_path(path: string): string;
}
export interface EnvironmentCommandArguments {
  status: { readonly json: boolean };
  cleanup: { readonly apply: boolean; readonly json: boolean };
  backup: {
    readonly source_db: string;
    readonly backup_dir: string;
    readonly deployed_revision: string;
  };
  'backup-current': {
    readonly source_db: string;
    readonly backup_dir: string;
    readonly current_app: string;
  };
  'provision-skills': {
    readonly database_parent: string;
    readonly hermes_home: string;
    readonly codex_home: string;
    readonly claude_home: string;
  };
  restore: {
    readonly snapshot: string;
    readonly destination_db: string;
    readonly live_stopped: boolean;
  };
  prepare: EnvironmentRequest & { readonly json: boolean };
  inspect: EnvironmentRequest & { readonly json: boolean };
  reset: EnvironmentRequest & { readonly kind: 'staging'; readonly json: boolean };
  remove: EnvironmentRequest & { readonly kind: 'staging'; readonly json: boolean };
}
export interface HostEnvironmentResources {
  readonly status_options: StatusOptions;
  readonly scratch_options: ScratchPassOptions;
  readonly signal: AbortSignal;
  require_active(): void;
}
export interface HostEnvironmentCommandDependencies extends OperatorPathInputs {
  load_config(): Config;
  readonly home: string;
  with_resources<T>(action: (resources: HostEnvironmentResources) => Promise<T>): Promise<T>;
  now_unix(): number;
}
/** Part 4's validator reports only these source-equivalent operational failures. */
export class AppManifestValidationFailure extends Error {
  readonly category: 'os' | 'value' | 'runtime';
  constructor(category: 'os' | 'value' | 'runtime', message: string) {
    super(message);
    this.category = category;
    this.name = 'AppManifestValidationFailure';
  }
}
export interface CurrentAppCommandDependencies extends OperatorPathInputs {
  /** No default. Unexpected rejections propagate instead of becoming warnings. */
  validate_app_manifest(manifest_path: string): Promise<void>;
  /** Await before backup work. Without a sink, warnings stay in the returned transcript. */
  write_warning?: (text: string) => Promise<void>;
}
export type EnvironmentCommandName = keyof EnvironmentCommandArguments;
export type EnvironmentCommandDependencies<Name extends EnvironmentCommandName> = Name extends
  'status' | 'cleanup'
  ? HostEnvironmentCommandDependencies
  : Name extends 'backup-current'
    ? CurrentAppCommandDependencies
    : Name extends 'backup' | 'restore' | 'prepare' | 'reset' | 'remove' | 'provision-skills'
      ? OperatorPathInputs
      : undefined;
export type EnvironmentCommandHandler<Name extends EnvironmentCommandName> = (
  arguments_: EnvironmentCommandArguments[Name],
  dependencies: EnvironmentCommandDependencies<Name>,
) => Promise<EnvironmentCommandResult>;
export interface OperatorArguments extends EnvironmentCommandArguments {
  'app-build': {
    readonly source_root: string;
    readonly requested_sha: string;
    readonly candidate_app: string;
  };
  'app-identity': { readonly app: string };
  run: EnvironmentRequest & { readonly test_mode: boolean };
  serve: Record<string, never>;
  restart: Record<string, never>;
  'server-event-loop-tasks': { readonly json: boolean };
  watchdog: WatchdogArguments;
}
export type OperatorName = keyof OperatorArguments;
export type OperatorDependencies<Name extends OperatorName> = Name extends EnvironmentCommandName
  ? EnvironmentCommandDependencies<Name>
  : Name extends 'app-identity'
    ? { validate_app_manifest(path: string, options?: AppValidationOptions): Promise<AppManifest> }
    : Name extends 'app-build'
      ? { build_app(args: OperatorArguments['app-build']): Promise<AppManifest> }
      : Name extends 'run'
        ? {
            prepare_run(
              args: OperatorArguments['run'],
            ): Promise<{ launch: PreparedOperatorLaunch; stderr: string }>;
          }
        : Name extends 'serve'
          ? { run_supervisor(): Promise<0 | 1> }
          : Name extends 'restart'
            ? { request_restart(): Promise<void> }
            : Name extends 'watchdog'
              ? { run_watchdog(args: WatchdogArguments): Promise<string> }
              : undefined;
export type OperatorResult<Name extends OperatorName> =
  | EnvironmentCommandResult
  | (Name extends 'run'
      ? EnvironmentCommandTranscript & { readonly launch: PreparedOperatorLaunch }
      : never);
export type OperatorHandler<Name extends OperatorName> = (
  args: OperatorArguments[Name],
  deps: OperatorDependencies<Name>,
) => Promise<OperatorResult<Name>>;
export type OperatorTable = {
  readonly [Name in OperatorName]: {
    readonly command: CommandDefinition;
    readonly handler: OperatorHandler<Name>;
  };
};
/** Produced only after the owned operation joins its actual cleanup. */
export class OperatorInterrupted extends Error {
  readonly signal: 'SIGINT' | 'SIGTERM';
  readonly stderr_prefix: string;
  constructor(signal: 'SIGINT' | 'SIGTERM', stderr_prefix: string, cause: unknown) {
    super('The caller stopped the operation.', { cause });
    this.name = 'OperatorInterrupted';
    this.signal = signal;
    this.stderr_prefix = stderr_prefix;
  }
}
