/** Production machine I/O stays outside card and route policy. */
import { spawn } from 'node:child_process';
import { accessSync, constants, realpathSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join, resolve, delimiter, normalize } from 'node:path';
import type { BackendProbeEnvironment, CommandOutcome } from './contracts.ts';
export function backend_io_is_test(): boolean {
  return (
    process.env.PLAN_TEST_MODE === '1' ||
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
  async run(
    argv: readonly string[],
    options: { timeout_seconds: number; environment_overrides?: Readonly<Record<string, string>> },
  ): Promise<CommandOutcome> {
    require_production_io();
    return new Promise((resolve_result) => {
      const output: Buffer[] = [];
      const error: Buffer[] = [];
      let timed_out = false;
      const child = spawn(argv[0], argv.slice(1), {
        stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...process.env, ...options.environment_overrides },
        detached: true,
      });
      const timer = setTimeout(() => {
        timed_out = true;
        try {
          if (child.pid !== undefined) process.kill(-child.pid, 'SIGKILL');
        } catch {
          child.kill('SIGKILL');
        }
      }, options.timeout_seconds * 1000);
      child.stdout.on('data', (chunk: Buffer) => output.push(chunk));
      child.stderr.on('data', (chunk: Buffer) => error.push(chunk));
      child.once('error', (failure) => {
        clearTimeout(timer);
        resolve_result({ exit_code: -1, standard_output: '', standard_error: failure.message });
      });
      child.once('close', (code) => {
        clearTimeout(timer);
        resolve_result(
          timed_out
            ? {
                exit_code: -1,
                standard_output: '',
                standard_error: `${argv[0]} did not answer within ${options.timeout_seconds}s`,
              }
            : {
                exit_code: code ?? -1,
                standard_output: Buffer.concat(output).toString('utf8'),
                standard_error: Buffer.concat(error).toString('utf8'),
              },
        );
      });
    });
  }
  async latest_released_version(
    package_name: string,
    options: { timeout_seconds: number },
  ): Promise<string | null> {
    require_production_io();
    try {
      const response = await fetch(`https://registry.npmjs.org/${package_name}/latest`, {
        signal: AbortSignal.timeout(options.timeout_seconds * 1000),
      });
      if (!response.ok) return null;
      const value: unknown = await response.json();
      if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
      const version = (value as Record<string, unknown>).version;
      return typeof version === 'string' && version ? version : null;
    } catch {
      return null;
    }
  }
}
