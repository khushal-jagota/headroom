/** Production machine I/O stays outside card and route policy. */
import { BackendCommands } from './commands.ts';
import { cli_search_folders } from './cli_locations.ts';
import { copy_environment } from '../../core/environment.ts';
import type { OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import type { BackendCommandOptions } from './commands.ts';
import { accessSync, constants, realpathSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { isAbsolute, join, resolve, delimiter } from 'node:path';
import type { BackendProbeEnvironment, CommandOutcome } from './contracts.ts';
import { testModeFromEnvironment } from '../../core/config.ts';
import { BUILT_IN_MANIFESTS } from '../../drivers/built_in_manifests.ts';
import type { DriverManifest } from '../../drivers/core/manifest.ts';
export function backend_io_is_test(): boolean {
  return (
    testModeFromEnvironment() ||
    process.env.VITEST === 'true' ||
    process.env.PYTEST_CURRENT_TEST !== undefined
  );
}
function require_production_io(): void {
  if (backend_io_is_test()) {
    throw new Error('Production backend I/O is unavailable in tests. Inject recording adapters.');
  }
}
const expand = (value: string, environment: Readonly<NodeJS.ProcessEnv> = process.env) => {
  const home = environment.HOME ?? homedir();
  return value === '~' ? home : value.startsWith('~/') ? join(home, value.slice(2)) : value;
};
/** The executable an override variable names, unless it names the ignored path under PLAN_APP_ROOT. */
export function executable_override(
  executable: DriverManifest['executable'],
  environment: Readonly<NodeJS.ProcessEnv>,
  expand_path: (path: string) => string,
): string | undefined {
  if (executable.override_environment_name === undefined) return undefined;
  const override = environment[executable.override_environment_name];
  if (override === undefined) return undefined;
  const app_root = environment.PLAN_APP_ROOT;
  const ignored =
    app_root === undefined || executable.ignored_override_path === undefined
      ? null
      : (app_root === '' ? '' : app_root.replace(/\/$/, '') + '/') +
        executable.ignored_override_path;
  const expanded = expand_path(override);
  return expanded === ignored ? undefined : expanded;
}
/** The built-in manifest executable of that name. */
function built_in_executable(name: string): DriverManifest['executable'] | undefined {
  return BUILT_IN_MANIFESTS.find((driver) => driver.manifest.executable.name === name)?.manifest
    .executable;
}
function file(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}
function executable_file(path: string): boolean {
  try {
    if (!file(path)) return false;
    accessSync(path, constants.X_OK);
    return true;
  } catch {
    return false; // Not an executable file.
  }
}
/**
 * The command on PATH. When PATH misses it, the owner's own Node tool folders (see
 * `cli_locations.ts`), and only a file the running account owns.
 */
export function backend_executable_path(
  name: string,
  environment: Readonly<NodeJS.ProcessEnv>,
): string | null {
  const choices = name.includes('/')
    ? [expand(name, environment)]
    : (environment.PATH ?? '').split(delimiter).map((directory) => join(directory || '.', name));
  for (const path of choices)
    if (executable_file(path)) return isAbsolute(path) ? path : resolve(path);
  if (name.includes('/')) return null;
  const uid = process.geteuid?.();
  for (const folder of cli_search_folders(environment.HOME ?? homedir())) {
    const path = join(folder, name);
    if (executable_file(path) && statSync(path).uid === uid) return path;
  }
  return null;
}
export class SubprocessBackendProbeEnvironment implements BackendProbeEnvironment {
  private readonly commands: BackendCommands;
  private readonly environment: Readonly<NodeJS.ProcessEnv>;
  private readonly on_path: (name: string) => string | null;
  async shutdown(): Promise<void> {
    await this.commands.shutdown();
  }
  constructor(
    children: OwnedOneShotChildren,
    environment: Readonly<NodeJS.ProcessEnv> = process.env,
    executable_path?: (name: string) => string | null,
  ) {
    require_production_io();
    this.commands = new BackendCommands(children);
    this.environment = { ...environment };
    this.on_path = executable_path ?? ((name) => backend_executable_path(name, this.environment));
  }
  executable_path(name: string): string | null {
    require_production_io();
    const executable = built_in_executable(name);
    const override =
      executable === undefined
        ? undefined
        : executable_override(executable, this.environment, (value) =>
            expand(value, this.environment),
          );
    if (override !== undefined) return file(override) ? override : null;
    return this.on_path(name);
  }
  real_path(path: string): string {
    require_production_io();
    try {
      return realpathSync(path);
    } catch {
      return resolve(path);
    }
  }
  modified_time_ms(path: string): number | null {
    require_production_io();
    try {
      return statSync(path).mtimeMs;
    } catch {
      return null;
    }
  }
  user_local_npm_prefix(): string {
    return join(this.environment.HOME ?? homedir(), '.local');
  }
  prefix_is_owned_and_writable(prefix: string): boolean {
    require_production_io();
    const uid = process.geteuid?.();
    if (uid === undefined) return false;
    try {
      for (const path of [prefix, join(prefix, 'lib'), join(prefix, 'lib', 'node_modules')]) {
        const info = statSync(path);
        if (!info.isDirectory() || info.uid !== uid) return false;
        accessSync(path, constants.W_OK | constants.X_OK);
      }
      return true;
    } catch {
      return false;
    }
  }
  async run(argv: readonly string[], options: BackendCommandOptions): Promise<CommandOutcome> {
    require_production_io();
    return this.commands.run(argv, {
      ...options,
      environment_overrides: {
        ...copy_environment(this.environment),
        ...options.environment_overrides,
      },
    });
  }
  async latest_released_version(
    package_name: string,
    options: { timeout_seconds: number; signal?: AbortSignal },
  ): Promise<string | null> {
    require_production_io();
    try {
      const response = await fetch(`https://registry.npmjs.org/${package_name}/latest`, {
        redirect: 'manual',
        signal: options.signal
          ? AbortSignal.any([options.signal, AbortSignal.timeout(options.timeout_seconds * 1000)])
          : AbortSignal.timeout(options.timeout_seconds * 1000),
      });
      if (!response.ok) {
        console.warn(
          `backend snapshot: ${package_name} latest version answered HTTP ${response.status}`,
        );
        return null;
      }
      const value: unknown = await response.json();
      if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
      const version = (value as Record<string, unknown>).version;
      return typeof version === 'string' && version ? version : null;
    } catch (error) {
      options.signal?.throwIfAborted();
      console.warn(`backend snapshot: ${package_name} latest version could not be fetched`, error);
      return null;
    }
  }
}
