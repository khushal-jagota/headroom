/** Production machine I/O stays outside card and route policy. */
import { BackendCommands } from './commands.ts';
import type { OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import type { BackendCommandOptions } from './commands.ts';
import { accessSync, constants, realpathSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join, resolve, delimiter } from 'node:path';
import { pure_posix_path } from '../../core/python_path.ts';
import type { BackendProbeEnvironment, CommandOutcome } from './contracts.ts';
import { testModeFromEnvironment, provider_launch_settings } from '../../core/config.ts';
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
/** Ignore the retired bundled Claude path in both native starts and machine discovery. */
export function claude_executable_override(
  environment: Readonly<NodeJS.ProcessEnv>,
  expand_path: (path: string) => string,
): string | undefined {
  const settings = provider_launch_settings(environment);
  const override = settings.claude_executable;
  if (override === undefined) return undefined;
  const legacy =
    settings.app_root === undefined
      ? null
      : pure_posix_path(
          (settings.app_root === '' ? '' : settings.app_root.replace(/\/$/, '') + '/') +
            'agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe',
        );
  return legacy !== null && pure_posix_path(expand_path(override)) === legacy
    ? undefined
    : expand_path(override);
}
function file(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}
function which(name: string, environment: Readonly<NodeJS.ProcessEnv>): string | null {
  const choices = name.includes('/')
    ? [expand(name, environment)]
    : (environment.PATH ?? '').split(delimiter).map((directory) => join(directory || '.', name));
  for (const path of choices) {
    try {
      if (!file(path)) continue;
      accessSync(path, constants.X_OK);
      return isAbsolute(path) ? path : resolve(path);
    } catch {
      /* This PATH candidate is not executable. */
    }
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
    this.on_path = executable_path ?? ((name) => which(name, this.environment));
  }
  executable_path(name: string): string | null {
    require_production_io();
    const override =
      name === 'claude'
        ? claude_executable_override(this.environment, (value) => expand(value, this.environment))
        : undefined;
    if (override !== undefined) return file(override) ? override : null;
    return this.on_path(name);
  }
  configured_executable_path(name: string): string | null {
    require_production_io();
    if (name !== 'hermes') return null;
    const python = expand(
      provider_launch_settings(this.environment).hermes_python,
      this.environment,
    );
    const executable = join(dirname(python), 'hermes');
    return file(executable) ? executable : null;
  }
  real_path(path: string): string {
    require_production_io();
    try {
      return realpathSync(path);
    } catch {
      return resolve(path);
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
        ...Object.fromEntries(
          Object.entries(this.environment).filter(
            (entry): entry is [string, string] => entry[1] !== undefined,
          ),
        ),
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
      if (!response.ok) return null;
      const value: unknown = await response.json();
      if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
      const version = (value as Record<string, unknown>).version;
      return typeof version === 'string' && version ? version : null;
    } catch {
      options.signal?.throwIfAborted();
      return null;
    }
  }
}
