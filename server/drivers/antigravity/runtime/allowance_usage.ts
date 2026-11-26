/**
 * Antigravity's 5-hour and weekly windows, read from agy itself: `agy -p "/usage"` prints them as
 * JSON, signed in through agy's own login wherever agy keeps it (a file on headless Linux, the OS
 * keyring on macOS). Panels reads no token. The account email comes from agy's own log.
 */
import { closeSync, openSync, readSync, readdirSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parse_backend_instance_id } from '../../core/identity.ts';
import type {
  BackendUsageAdapter,
  BackendUsageResult,
  BackendUsageWindow,
  BackendUsageWindowKind,
} from '../../../conversation/backend_usage/contracts.ts';
import { stop_one_shot_child } from '../../../lifecycle/one_shot_children.ts';
import type { OwnedOneShotChildren } from '../../../lifecycle/contracts.ts';
import { is_record as object } from '../../../core/records.ts';
import { parse_datetime, usage_window } from '../../../conversation/backend_usage/logic/windows.ts';
import { acp_child_environment } from '../../acp/environment.ts';
import { antigravity_acp_launch } from './flavor.ts';
import type { AntigravityLaunchOptions } from './flavor.ts';

const ANTIGRAVITY_USAGE_TIMEOUT_SECONDS = 20;
const USAGE_OUTPUT_LIMIT_BYTES = 1_048_576;
const UNREACHABLE_MESSAGE = 'Antigravity usage could not be reached. Try again.';

/**
 * Runs `agy -p "/usage" --output-format json` and returns its standard output. It runs no model
 * turn. Throws when agy cannot be started, exits with an error, or does not answer in time.
 */
export type AntigravityUsageRunner = (signal?: AbortSignal) => Promise<string>;

/** The runner on agy's own home, in a scratch folder, through the one-shot child owner. */
export function antigravity_usage_runner(
  options: Omit<AntigravityLaunchOptions, 'executable'>,
  executable: () => string,
  children: OwnedOneShotChildren,
): AntigravityUsageRunner {
  return async (signal) => {
    signal?.throwIfAborted();
    const launch = antigravity_acp_launch({ ...options, executable: executable() });
    const cwd = await mkdtemp(join(tmpdir(), 'panels-antigravity-usage-'));
    try {
      return await new Promise<string>((resolve, reject) => {
        const child = children.spawn(launch.argv[0]!, ['-p', '/usage', '--output-format', 'json'], {
          cwd,
          env: acp_child_environment(
            launch.environment_overrides,
            { role_materials: null },
            launch.inherited_environment,
          ),
          stdio: ['ignore', 'pipe', 'pipe'],
        });
        const chunks: Buffer[] = [];
        let size = 0;
        let failure: Error | null = null;
        const fail = (error: Error) => {
          failure ??= error;
          stop_one_shot_child(child);
        };
        const abort = () => fail(new Error('Antigravity usage was cancelled.'));
        signal?.addEventListener('abort', abort, { once: true });
        const timeout = setTimeout(
          () => fail(new Error('agy did not answer /usage in time.')),
          ANTIGRAVITY_USAGE_TIMEOUT_SECONDS * 1000,
        );
        child.stdout!.on('data', (chunk: Buffer) => {
          size += chunk.length;
          if (size > USAGE_OUTPUT_LIMIT_BYTES) fail(new Error('agy /usage output was too large.'));
          else chunks.push(chunk);
        });
        child.stderr!.resume();
        child.once('error', (error) => {
          clearTimeout(timeout);
          signal?.removeEventListener('abort', abort);
          reject(error);
        });
        child.once('close', (code) => {
          clearTimeout(timeout);
          signal?.removeEventListener('abort', abort);
          if (failure !== null) reject(failure);
          else if (code !== 0) reject(new Error(`agy /usage exited with code ${code}.`));
          else resolve(Buffer.concat(chunks).toString('utf8'));
        });
      });
    } finally {
      await rm(cwd, { recursive: true, force: true }).catch(() => {});
    }
  };
}

/** agy's ACP server logs begin with the sign-in line, so this much of a log is enough. */
const LOG_HEAD_BYTES = 65_536;
const SIGNED_IN_LINE = /OAuth: authenticated successfully as (\S+@\S+)/g;

/**
 * The Google account agy signed in as, from the sign-in line of its newest ACP server log in
 * `log_directory`. Null when there is no log or no such line: agy's log text is not a contract, and
 * an older log is never used. Only the email leaves this reader.
 */
export function read_antigravity_account_email(log_directory: string): string | null {
  try {
    const newest = readdirSync(log_directory)
      .filter((name) => /^acp-server-.*\.log$/.test(name))
      .sort()
      .at(-1);
    if (newest === undefined) return null;
    const descriptor = openSync(join(log_directory, newest), 'r');
    try {
      const buffer = Buffer.alloc(LOG_HEAD_BYTES);
      const length = readSync(descriptor, buffer, 0, LOG_HEAD_BYTES, 0);
      const matches = [...buffer.toString('utf8', 0, length).matchAll(SIGNED_IN_LINE)];
      return matches.at(-1)?.[1] ?? null;
    } finally {
      closeSync(descriptor);
    }
  } catch {
    return null;
  }
}

export interface AntigravityUsageOptions {
  readonly run_usage: AntigravityUsageRunner;
  readonly now: () => Date;
}

export class AntigravityUsageAdapter implements BackendUsageAdapter {
  private readonly options: AntigravityUsageOptions;
  constructor(options: AntigravityUsageOptions) {
    this.options = options;
  }
  async refresh(options: { signal?: AbortSignal } = {}): Promise<BackendUsageResult> {
    options.signal?.throwIfAborted();
    const result = (
      outcome: BackendUsageResult['outcome'],
      detail: string,
    ): BackendUsageResult => ({
      backend_key: parse_backend_instance_id('antigravity'),
      outcome,
      detail,
      observed_at: null,
      windows: [],
    });
    let output: string;
    try {
      output = await this.options.run_usage(options.signal);
    } catch {
      options.signal?.throwIfAborted();
      return result('failed', UNREACHABLE_MESSAGE);
    }
    let body: unknown = null;
    try {
      body = JSON.parse(output);
    } catch {
      // The output names no windows below.
    }
    const windows = parse_usage_windows(body);
    if (!windows.length)
      return result('unavailable', 'Antigravity did not return usable quota information.');
    return {
      backend_key: parse_backend_instance_id('antigravity'),
      outcome: 'succeeded',
      detail: null,
      observed_at: this.options.now(),
      windows,
    };
  }
}

/**
 * A group's display name without its trailing "models": "Gemini Models" is Gemini, "Claude and
 * GPT models" is Claude and GPT, and "Gemini 4 Argon" stays as it is.
 */
function scope_label_of_group(display_name: unknown): string | null {
  if (typeof display_name !== 'string') return null;
  return display_name.trim().replace(/\s+models$/i, '') || null;
}
/** A bucket outside any named group, by its id: `gemini-5h` is Gemini, `3p-weekly` is Claude and GPT. */
function family_of_bucket(bucket_id: string): string {
  if (bucket_id.startsWith('3p-')) return 'Claude and GPT';
  if (bucket_id.startsWith('gemini-')) return 'Gemini';
  return bucket_id;
}

/** Quota buckets as agy's `/usage` JSON names them: `command.data.groups[].buckets[]`. */
function parse_usage_windows(body: unknown): BackendUsageWindow[] {
  if (!object(body) || !object(body.command) || !object(body.command.data)) return [];
  const data = body.command.data;
  if (!Array.isArray(data.groups)) return [];
  const windows = new Map<string, BackendUsageWindow>();
  for (const group of data.groups) {
    if (!object(group) || !Array.isArray(group.buckets)) continue;
    const name = scope_label_of_group(group.name);
    for (const bucket of group.buckets) {
      if (!object(bucket) || bucket.disabled === true) continue;
      const bucket_id = bucket.id;
      if (typeof bucket_id !== 'string' || !bucket_id || windows.has(bucket_id)) continue;
      const kind: BackendUsageWindowKind | null =
        bucket.window === '5h' ? 'five_hour' : bucket.window === 'weekly' ? 'seven_day' : null;
      const remaining = bucket.remaining_fraction;
      if (kind === null || typeof remaining !== 'number' || !Number.isFinite(remaining)) continue;
      // An untouched bucket answers the Unix epoch, which means it names no reset.
      const reset = parse_datetime(bucket.reset_time);
      windows.set(
        bucket_id,
        usage_window({
          id: bucket_id,
          kind,
          used_percent: (1 - Math.max(0, Math.min(1, remaining))) * 100,
          resets_at: reset !== null && reset.getTime() > 0 ? reset : null,
          model_scope: name ?? family_of_bucket(bucket_id),
        }),
      );
    }
  }
  return [...windows.values()];
}
