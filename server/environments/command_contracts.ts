/** Plain operator functions. Unit 20 owns argv parsing and process IO. */
import type { Config } from '../core/config.ts';
import type { EnvironmentRequest } from './registry/contracts.ts';
import type { StatusOptions } from './vps_status/contracts.ts';

export interface EnvironmentCommandResult {
  readonly exit_code: 0 | 1;
  readonly stdout: string;
  readonly stderr: string;
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
export interface HostEnvironmentCommandDependencies {
  load_config(): Config;
  readonly status_options: StatusOptions;
  now_unix(): number;
}
/** Part 4's validator reports only these source-equivalent operational failures. */
export class AppManifestValidationFailure extends Error {
  readonly category: 'os' | 'value' | 'runtime';
  constructor(category: 'os' | 'value' | 'runtime', message: string) {
    super(message);
    this.category = category;
    this.name =
      category === 'os' ? 'OSError' : category === 'value' ? 'ValueError' : 'RuntimeError';
  }
}
export interface CurrentAppCommandDependencies {
  /** No default. Unexpected rejections propagate instead of becoming warnings. */
  validate_app_manifest(manifest_path: string): Promise<void>;
}
export type EnvironmentCommandName = keyof EnvironmentCommandArguments;
export type EnvironmentCommandDependencies<Name extends EnvironmentCommandName> = Name extends
  'status' | 'cleanup'
  ? HostEnvironmentCommandDependencies
  : Name extends 'backup-current'
    ? CurrentAppCommandDependencies
    : undefined;
export type EnvironmentCommandHandler<Name extends EnvironmentCommandName> = (
  arguments_: EnvironmentCommandArguments[Name],
  dependencies: EnvironmentCommandDependencies<Name>,
) => Promise<EnvironmentCommandResult>;
export interface EnvironmentCommandOption {
  readonly name: string;
  readonly destination?: string;
  readonly type: 'string' | 'integer' | 'boolean' | 'path' | 'choice';
  readonly required?: boolean;
  readonly multiple?: boolean;
  readonly choices?: readonly string[];
  readonly default?: string | number | boolean | null | readonly string[];
  readonly path?: {
    readonly exists?: boolean;
    readonly file_okay?: boolean;
    readonly dir_okay?: boolean;
  };
}
export type EnvironmentCommandDefinition<Name extends EnvironmentCommandName> = {
  readonly path: readonly ['environment', Name];
  readonly options: readonly EnvironmentCommandOption[];
  readonly handler: EnvironmentCommandHandler<Name>;
};
export type EnvironmentCommandTable = {
  readonly [Name in EnvironmentCommandName]: EnvironmentCommandDefinition<Name>;
};
