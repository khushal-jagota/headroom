/** The selected executable must supply the native features that Panels consumes. */
import { BUILT_IN_MANIFESTS } from '../../drivers/built_in_manifests.ts';
import { realpath, stat } from 'node:fs/promises';
import type { OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import { OneShotChildren, stop_one_shot_child } from '../../lifecycle/one_shot_children.ts';

const VERSION_TIMEOUT_SECONDS = 10;

/** The kind's floor and label from the manifest door, or null when the kind has no floor. */
function requirement(kind: string) {
  const driver = BUILT_IN_MANIFESTS.find((candidate) => candidate.driver_kind === kind);
  const floor = driver?.manifest.cli_floor;
  return driver === undefined || floor === undefined ? null : { name: driver.client.label, floor };
}
function required(kind: string) {
  const found = requirement(kind);
  if (found === null) throw new Error(`${kind} has no CLI version floor`);
  return found;
}

export class BackendCliIncompatible extends Error {}

function repair(backend: string): string {
  const { name, floor } = required(backend);
  return `Update ${name} to at least ${floor.minimum_version}. Panels requires ${floor.feature}.`;
}

function too_old(backend: string, executable: string, version: string): BackendCliIncompatible {
  return new BackendCliIncompatible(
    `${required(backend).name} ${version} at ${executable} is too old. ${repair(backend)}`,
  );
}

export function cli_version_error(
  backend: string,
  executable: string,
  kind: 'check' | 'read',
): BackendCliIncompatible {
  return new BackendCliIncompatible(
    `Cannot ${kind} the ${required(backend).name} version at ${executable}. ${repair(backend)}`,
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

/** A definite floor verdict: null meets the floor, a string is the normalized too-old version. */
type CliVerdict = string | null;

/** Compare one reported version with the floor. Undefined means the text is not a version. */
function floor_verdict(backend: string, reported: string): CliVerdict | undefined {
  const parsed = version(reported);
  if (parsed === null) return undefined;
  const floor = required(backend)
    .floor.minimum_version.split('.')
    .map((part) => BigInt(part));
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
  return comparison < 0 || (comparison === 0 && parsed.before) ? parsed.normalized : null;
}

function output_verdict(
  backend: string,
  executable: string,
  stdout: string,
  exitCode: number | null,
): CliVerdict {
  const reported = required(backend).floor.version_pattern.exec(stdout.trim());
  const verdict = reported === null ? undefined : floor_verdict(backend, reported[1]!);
  if (exitCode !== 0 || verdict === undefined) throw cli_version_error(backend, executable, 'read');
  return verdict;
}

export function validate_cli_version(
  backend: string,
  executable: string,
  stdout: string,
  exitCode: number | null,
): void {
  if (requirement(backend) === null) return;
  const verdict = output_verdict(backend, executable, stdout, exitCode);
  if (verdict !== null) throw too_old(backend, executable, verdict);
}

/**
 * Definite verdicts by backend and executable file. A CLI update replaces the file or moves
 * its link, so the key changes and the next check reads the new version. Failed or unreadable
 * checks are never kept.
 */
const verdicts = new Map<string, CliVerdict>();

async function executable_key(backend: string, executable: string): Promise<string | null> {
  try {
    const path = await realpath(executable);
    const file = await stat(path, { bigint: true });
    return [backend, path, file.dev, file.ino, file.size, file.mtimeNs, file.ctimeNs].join('\0');
  } catch {
    // An unreadable file is a miss. The spawned check reports its usual error.
    return null;
  }
}

/**
 * Identify the executable before its `--version` output is read, then keep the definite
 * verdict that output gives. Unreadable output keeps nothing and throws nothing.
 */
export async function cli_version_recorder(
  backend: string,
  executable: string,
): Promise<(stdout: string, exitCode: number | null) => void> {
  if (requirement(backend) === null) return () => {};
  const key = await executable_key(backend, executable);
  return (stdout, exitCode) => {
    if (key === null) return;
    let verdict: CliVerdict;
    try {
      verdict = output_verdict(backend, executable, stdout, exitCode);
    } catch {
      return;
    }
    verdicts.set(key, verdict);
  };
}

/**
 * Enforce the floor on a version the running CLI reported in its handshake. A version below
 * the floor replaces the kept verdict and throws. A missing or unreadable version passes.
 */
export async function confirm_reported_cli_version(
  backend: string,
  executable: string,
  reported: unknown,
): Promise<void> {
  if (typeof reported !== 'string' || requirement(backend) === null) return;
  const verdict = floor_verdict(backend, reported);
  if (verdict === undefined || verdict === null) return;
  const key = await executable_key(backend, executable);
  if (key !== null) verdicts.set(key, verdict);
  throw too_old(backend, executable, verdict);
}

/** A kept verdict for the same executable file answers with only a stat. A miss runs `--version`. */
export async function ensure_cli_compatible(
  backend: string,
  executable: string,
  options: {
    environment?: Readonly<Record<string, string>>;
    working_directory?: string;
    children?: OwnedOneShotChildren;
    signal?: AbortSignal;
  } = {},
): Promise<void> {
  if (requirement(backend) === null) return;
  options.signal?.throwIfAborted();
  const key = await executable_key(backend, executable);
  const known = key === null ? undefined : verdicts.get(key);
  if (known !== undefined) {
    if (known !== null) throw too_old(backend, executable, known);
    return;
  }
  options.signal?.throwIfAborted();
  const children = options.children ?? new OneShotChildren();
  let release_closed!: () => void;
  const closed = new Promise<void>((resolve) => {
    release_closed = resolve;
  });
  try {
    const output = await new Promise<{ stdout: string; code: number | null }>((resolve, reject) => {
      let child;
      try {
        child = children.spawn(executable, ['--version'], {
          env:
            options.environment === undefined
              ? process.env
              : { ...process.env, ...options.environment },
          cwd: options.working_directory,
          stdio: ['ignore', 'pipe', 'pipe'],
        });
      } catch (cause) {
        release_closed();
        reject(
          new BackendCliIncompatible(cli_version_error(backend, executable, 'check').message, {
            cause,
          }),
        );
        return;
      }
      const chunks: Buffer[] = [];
      const abort = () => {
        stop_one_shot_child(child);
      };
      options.signal?.addEventListener('abort', abort, { once: true });
      if (options.signal?.aborted) abort();
      let timed_out = false;
      const timeout = setTimeout(() => {
        timed_out = true;
        stop_one_shot_child(child);
      }, VERSION_TIMEOUT_SECONDS * 1000);
      child.stdout!.on('data', (chunk: Buffer) => chunks.push(chunk));
      child.stderr!.resume();
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
        options.signal?.removeEventListener('abort', abort);
        release_closed();
        if (timed_out) reject(cli_version_error(backend, executable, 'check'));
        else resolve({ stdout: Buffer.concat(chunks).toString('utf8'), code });
      });
    });
    const verdict = output_verdict(backend, executable, output.stdout, output.code);
    if (key !== null) verdicts.set(key, verdict);
    if (verdict !== null) throw too_old(backend, executable, verdict);
  } finally {
    // Abort cancels the outcome, not actual ownership. Core/APP observes its own deadline.
    await closed;
    if (!options.children) {
      children.seal();
      await children.settled();
    }
  }
}
