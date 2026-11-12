/** Production machine I/O stays outside card and route policy. */
import { BackendCommands } from './commands.ts';
import type { BackendCommandOptions } from './commands.ts';
import { accessSync, constants, realpathSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join, resolve, delimiter, normalize } from 'node:path';
import type { BackendProbeEnvironment, CommandOutcome } from './contracts.ts';
import { testModeFromEnvironment } from '../../core/config.ts';
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
const expand = (value: string) =>
  value === '~' ? homedir() : value.startsWith('~/') ? join(homedir(), value.slice(2)) : value;
function file(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}
function which(name: string): string | null {
  const choices = name.includes('/')
    ? [expand(name)]
    : (process.env.PATH ?? '').split(delimiter).map((directory) => join(directory || '.', name));
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
  private readonly commands = new BackendCommands();
  async shutdown(): Promise<void> {
    await this.commands.shutdown();
  }
  constructor() {
    require_production_io();
  }
  executable_path(name: string): string | null {
    require_production_io();
    if (name === 'claude' && process.env.PLAN_CLAUDE_EXECUTABLE !== undefined) {
      const selected = expand(process.env.PLAN_CLAUDE_EXECUTABLE);
      const app_root = process.env.PLAN_APP_ROOT;
      const legacy =
        app_root === undefined
          ? null
          : join(app_root, 'agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe');
      if (legacy === null || normalize(selected) !== normalize(legacy))
        return file(selected) ? selected : null;
    }
    return which(name);
  }
  configured_executable_path(name: string): string | null {
    require_production_io();
    if (name !== 'hermes') return null;
    const python = expand(
      process.env.PLAN_HERMES_PYTHON || '~/.hermes/hermes-agent/venv/bin/python',
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
    return join(homedir(), '.local');
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
    return this.commands.run(argv, options);
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
