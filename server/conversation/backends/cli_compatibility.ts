/** The selected executable must supply the native features that Panels consumes. */
import { spawn } from 'node:child_process';

export type Backend = 'codex' | 'claude';
export const VERSION_TIMEOUT_SECONDS = 10;
const requirements = {
  codex: {
    name: 'Codex',
    version: '0.148.0',
    reason: 'thread-aware app/read',
    output: /^codex-cli (\S+)$/,
  },
  claude: {
    name: 'Claude',
    version: '2.1.219',
    reason: 'queued message cancellation on Stop',
    output: /^(\S+) \(Claude Code\)$/,
  },
} as const;

export class BackendCliIncompatible extends Error {}

function repair(backend: Backend): string {
  const requirement = requirements[backend];
  return `Update ${requirement.name} to at least ${requirement.version}. Panels requires ${requirement.reason}.`;
}

export function cli_version_error(
  backend: Backend,
  executable: string,
  kind: 'check' | 'read',
): BackendCliIncompatible {
  return new BackendCliIncompatible(
    `Cannot ${kind} the ${requirements[backend].name} version at ${executable}. ${repair(backend)}`,
  );
}

/** PEP 440 normalization and comparison against a stable feature floor. */
function version(
  value: string,
): { normalized: string; release: bigint[]; epoch: bigint; before: boolean; post: boolean } | null {
  const match =
    /^v?(?:(\d+)!)?(\d+(?:\.\d+)*)(?:[-_.]?(alpha|a|beta|b|preview|pre|c|rc)[-_.]?(\d+)?)?(?:(?:-(\d+))|(?:[-_.]?(post|rev|r)[-_.]?(\d+)?))?(?:[-_.]?(dev)[-_.]?(\d+)?)?(?:\+([a-z0-9]+(?:[-_.][a-z0-9]+)*))?$/i.exec(
      value,
    );
  if (match === null) return null;
  const epoch = BigInt(match[1] ?? '0');
  const release = match[2]!.split('.').map((part) => BigInt(part));
  let normalized = `${epoch === 0n ? '' : `${epoch}!`}${release.join('.')}`;
  if (match[3] !== undefined) {
    const key = match[3].toLowerCase();
    const pre = ['alpha', 'a'].includes(key) ? 'a' : ['beta', 'b'].includes(key) ? 'b' : 'rc';
    normalized += `${pre}${BigInt(match[4] ?? '0')}`;
  }
  const post = match[5] !== undefined || match[6] !== undefined;
  if (post) normalized += `.post${BigInt(match[5] ?? match[7] ?? '0')}`;
  if (match[8] !== undefined) normalized += `.dev${BigInt(match[9] ?? '0')}`;
  if (match[10] !== undefined) {
    normalized += `+${match[10]
      .toLowerCase()
      .split(/[-_.]/)
      .map((part) => (/^\d+$/.test(part) ? String(BigInt(part)) : part))
      .join('.')}`;
  }
  return {
    normalized,
    release,
    epoch,
    before: match[3] !== undefined || (match[8] !== undefined && !post),
    post,
  };
}

export function validate_cli_version(
  backend: Backend,
  executable: string,
  stdout: string,
  exitCode: number | null,
): void {
  const reported = requirements[backend].output.exec(stdout.trim());
  const parsed = reported === null ? null : version(reported[1]!);
  if (exitCode !== 0 || parsed === null) throw cli_version_error(backend, executable, 'read');
  const floor = requirements[backend].version.split('.').map((part) => BigInt(part));
  let comparison = parsed.epoch === 0n ? 0 : 1;
  for (
    let index = 0;
    comparison === 0 && index < Math.max(floor.length, parsed.release.length);
    index += 1
  ) {
    const left = parsed.release[index] ?? 0n;
    const right = floor[index] ?? 0n;
    comparison = left < right ? -1 : left > right ? 1 : 0;
  }
  if (comparison < 0 || (comparison === 0 && parsed.before)) {
    throw new BackendCliIncompatible(
      `${requirements[backend].name} ${parsed.normalized} at ${executable} is too old. ${repair(backend)}`,
    );
  }
}

export async function ensure_cli_compatible(
  backend: Backend,
  executable: string,
  options: { environment?: Readonly<Record<string, string>>; working_directory?: string } = {},
): Promise<void> {
  const output = await new Promise<{ stdout: string; code: number | null }>((resolve, reject) => {
    let child;
    try {
      child = spawn(executable, ['--version'], {
        env:
          options.environment === undefined
            ? process.env
            : { ...process.env, ...options.environment },
        cwd: options.working_directory,
        stdio: ['ignore', 'pipe', 'pipe'],
      });
    } catch (cause) {
      reject(
        new BackendCliIncompatible(cli_version_error(backend, executable, 'check').message, {
          cause,
        }),
      );
      return;
    }
    const chunks: Buffer[] = [];
    let timed_out = false;
    const timeout = setTimeout(() => {
      timed_out = true;
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    }, VERSION_TIMEOUT_SECONDS * 1000);
    child.stdout.on('data', (chunk: Buffer) => chunks.push(chunk));
    child.stderr.resume();
    child.once('error', (cause) => {
      clearTimeout(timeout);
      reject(
        new BackendCliIncompatible(cli_version_error(backend, executable, 'check').message, {
          cause,
        }),
      );
    });
    child.once('close', (code) => {
      clearTimeout(timeout);
      if (timed_out) reject(cli_version_error(backend, executable, 'check'));
      else resolve({ stdout: Buffer.concat(chunks).toString('utf8'), code });
    });
  });
  validate_cli_version(backend, executable, output.stdout, output.code);
}
