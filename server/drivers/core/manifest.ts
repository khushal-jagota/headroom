/** Server-side static data about a driver. Readable without loading the driver's runtime. */
import type { ProbeRecipe } from './status.ts';

/** One native tool a release admission runs to prove the tool starts on this system. */
export interface AdmissionCommand {
  readonly systems: readonly ('darwin' | 'linux')[];
  /** The Linux runtime contract versions that ship the tool. Absent: every version. */
  readonly runtime_contracts?: readonly (1 | 2)[];
  /** 'node': the release's node runs the path. 'direct': the path is executed. */
  readonly runner: 'node' | 'direct';
  /** Relative to the release root. */
  readonly path: string;
  readonly arguments: readonly string[];
}
export interface DriverManifest {
  readonly executable: {
    readonly name: string;
    /** An environment variable that names the executable instead of PATH. */
    readonly override_environment_name?: string;
    /** An override naming this path under PLAN_APP_ROOT is ignored: a retired bundled copy. */
    readonly ignored_override_path?: string;
  };
  /** The provider state home. */
  readonly home?: {
    readonly environment_name: string;
    /** Further variables that carry the same home to the provider. */
    readonly extra_environment_names?: readonly string[];
    /** May start with '~/'. */
    readonly default_path: string;
    /** The home's directory name in isolated provider state and release probe state. */
    readonly isolated_directory_name: string;
    /** The CLI option that names this home. A CLI contract: never renamed. */
    readonly cli_option: string;
    /** A relative path inside the home, one or more components. Default 'skills'. */
    readonly skills_directory?: string;
    /**
     * A macOS executor leaves the variable unset when the owner left it unset: any set value,
     * even the default, makes the provider read a different Keychain login.
     */
    readonly leave_unset_on_macos?: boolean;
  };
  /** Further variables the installed launcher passes to the server for this driver. */
  readonly launcher_environment_names: readonly string[];
  /** Variables with these prefixes are the driver's own and never stored per environment. */
  readonly reserved_environment_prefixes?: readonly string[];
  /** The oldest CLI that has a native feature Panels consumes. No floor: no check. */
  readonly cli_floor?: {
    readonly minimum_version: string;
    readonly feature: string;
    /** Matches one `--version` output line; group 1 is the version. */
    readonly version_pattern: RegExp;
  };
  readonly install?: {
    readonly npm_packages?: readonly string[];
    /** Variables the release probe sets for this driver's tools. */
    readonly admission_environment?: Readonly<Record<string, string>>;
    /** Repository paths a release must ship for this driver. */
    readonly release_files?: readonly string[];
    /** On macOS, the installed PATH also holds the directory of this executable. */
    readonly darwin_path_executable?: boolean;
    /** Release-root paths the Linux build removes before sealing. */
    readonly linux_pruned_paths?: readonly string[];
    readonly admission_commands?: readonly AdmissionCommand[];
  };
  readonly status_recipe: ProbeRecipe;
}
