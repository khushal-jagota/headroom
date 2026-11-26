/** One short-lived Claude SDK session for a read that sends no prompt: owned, bounded, reaped. */
import type { OwnedOneShotChildren, StopOptions } from '../../../lifecycle/contracts.ts';
import { copy_environment } from '../../../core/environment.ts';
import { stop_one_shot_child } from '../../../lifecycle/one_shot_children.ts';
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import { load_claude_sdk, CLAUDE_AGENT_SDK_VERSION, PANELS_CLAUDE_CODE_ENTRYPOINT } from './sdk.ts';
import type { Query, SDKUserMessage } from '@anthropic-ai/claude-agent-sdk';
import { ensure_cli_compatible } from '../../../conversation/backends/cli_compatibility.ts';
import { ModelCatalogCleanupFailed } from '../../../conversation/backends/catalog_cleanup.ts';
import { OwnedProcess } from '../../../lifecycle/owned_processes.ts';
import { OWNED_PROCESS_KILL_WAIT_SECONDS } from '../../../lifecycle/configuration.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';

export interface ClaudeProbeSessionOptions {
  readonly signal?: AbortSignal;
  readonly stop_options?: () => StopOptions | undefined;
  readonly environment?: Readonly<NodeJS.ProcessEnv>;
}

/**
 * Starts the selected claude with an input stream that never yields, so no model turn runs,
 * hands the open query to `read`, then closes the query and reaps the child. Every failure,
 * the deadline and cancellation reject as `unavailable`; a failed native close rejects as
 * `ModelCatalogCleanupFailed`, which keeps the cleanup for a later retry.
 */
export async function run_claude_probe_session<T>(
  claude_executable: string,
  children: OwnedOneShotChildren,
  options: ClaudeProbeSessionOptions,
  session: {
    readonly timeout_seconds: number;
    readonly timed_out_message: string;
    readonly unavailable: new (message: string, options?: ErrorOptions) => Error;
    readonly read: (cli: Query) => Promise<T>;
  },
): Promise<T> {
  const signal = options.signal
    ? AbortSignal.any([children.signal, options.signal])
    : children.signal;
  signal.throwIfAborted();
  const environment = copy_environment(options.environment ?? process.env);
  const state: {
    owned: OwnedProcess | null;
    cli: Query | null;
    expired: boolean;
    child_error: Error | null;
    child_exited: boolean;
  } = { owned: null, cli: null, expired: false, child_error: null, child_exited: false };
  let release_input: () => void = () => {};
  const input_closed = new Promise<void>((resolve) => {
    release_input = resolve;
  });
  const prompt = async function* (): AsyncGenerator<SDKUserMessage> {
    await input_closed;
  };
  let timer: ReturnType<typeof setTimeout> | undefined;
  let value: T | undefined;
  let failure: unknown;
  let failed = false;
  let actual: Promise<T> | undefined;
  let primary: unknown;
  let cleanup_deadline: number | undefined;
  let reject_cancelled!: (error: unknown) => void;
  const cancelled = new Promise<never>((_, reject) => {
    reject_cancelled = reject;
  });
  const cancellation_observed = cancelled.catch(() => {});
  const abort = () => reject_cancelled(signal.reason);
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  const close_query = () => {
    if (state.cli === null) return;
    state.cli.close();
    state.cli = null;
  };
  const cleanup = async (): Promise<void> => {
    const errors: unknown[] = [];
    try {
      close_query();
    } catch (error) {
      errors.push(error);
    }
    release_input();
    try {
      if (state.owned) {
        stop_one_shot_child(state.owned.child);
        cleanup_deadline ??= performance.now() / 1000 + OWNED_PROCESS_KILL_WAIT_SECONDS;
        if (!state.child_exited) {
          await observe_shutdown(
            Promise.race([state.owned.exited, cancellation_observed]),
            {
              deadline: Math.min(cleanup_deadline, options.stop_options?.()?.deadline ?? Infinity),
            },
            'the Claude probe process did not exit after SIGKILL',
          );
        }
        if (!state.child_exited)
          await observe_shutdown(
            state.owned.exited,
            {
              deadline: Math.min(cleanup_deadline, options.stop_options?.()?.deadline ?? Infinity),
            },
            'the Claude probe process did not exit after SIGKILL',
          );
        // The leader is reaped. The shared owner now closes pipes without another signal.
        await state.owned.stop();
      }
    } catch (error) {
      errors.push(error);
    }
    // A rejected probe retains this closure, its native owner, and the SDK operation.
    if (errors.length) throw new ModelCatalogCleanupFailed(primary, errors, cleanup);
    await actual?.catch(() => {});
    signal.removeEventListener('abort', abort);
  };
  try {
    const operation = async (): Promise<T> => {
      await ensure_cli_compatible('claude', claude_executable, {
        children,
        ...(options.signal ? { signal } : {}),
        ...(options.environment ? { environment } : {}),
      });
      signal.throwIfAborted();
      if (state.expired) throw new session.unavailable(session.timed_out_message);
      const child_environment: NodeJS.ProcessEnv = {
        ...environment,
        CLAUDE_CODE_ENTRYPOINT: PANELS_CLAUDE_CODE_ENTRYPOINT,
        CLAUDE_AGENT_SDK_VERSION,
      };
      delete child_environment.CLAUDECODE;
      const { query } = await load_claude_sdk();
      signal.throwIfAborted();
      if (state.expired) throw new session.unavailable(session.timed_out_message);
      state.cli = query({
        prompt: prompt(),
        options: {
          pathToClaudeCodeExecutable: claude_executable,
          maxTurns: 1,
          env: child_environment,
          spawnClaudeCodeProcess(options) {
            const started = children.spawn(options.command, options.args, {
              cwd: options.cwd,
              // The SDK adds and removes launch variables.
              // The configured environment stays canonical.
              env: child_environment,
              stdio: ['pipe', 'pipe', 'pipe'],
            });
            state.owned = new OwnedProcess(started as ChildProcessWithoutNullStreams);
            void state.owned.exited.then(() => {
              state.child_exited = true;
            });
            started.once('error', (error) => {
              state.child_error = error;
            });
            return started;
          },
        },
      });
      const value = await session.read(state.cli);
      if (state.child_error !== null) throw state.child_error;
      return value;
    };
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        state.expired = true;
        reject(new session.unavailable(session.timed_out_message));
      }, session.timeout_seconds * 1000);
    });
    actual = operation();
    value = await Promise.race([actual, timeout, cancelled]);
  } catch (cause) {
    primary = cause;
    failed = true;
    failure =
      cause instanceof session.unavailable
        ? cause
        : new session.unavailable(cause instanceof Error ? cause.message : String(cause), {
            cause,
          });
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    await cleanup();
  }
  if (failed) throw failure;
  return value as T;
}
