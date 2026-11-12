/** Model choices come from the selected CLI's startup handshake, without a prompt. */
import type { OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import { OneShotChildren, stop_one_shot_child } from '../../lifecycle/one_shot_children.ts';
import type { ChildProcess } from 'node:child_process';
import { load_claude_sdk, CLAUDE_AGENT_SDK_VERSION } from './claude_sdk.ts';
import type { Query, SDKUserMessage } from '@anthropic-ai/claude-agent-sdk';
import { ensure_cli_compatible } from './cli_compatibility.ts';

export const MODEL_CATALOG_TIMEOUT_SECONDS = 30;
export class ClaudeModelCatalogUnavailable extends Error {}
export interface ClaudeModel {
  readonly model_id: string;
  readonly display_name: string;
  readonly resolved_model_id: string;
  readonly reasoning_effort_options: readonly string[];
}
export interface ClaudeModelCatalog {
  readonly models: readonly ClaudeModel[];
  readonly reasoning_effort_options: readonly string[];
  readonly default_model_id: string | null;
}

export function versioned_display_name(resolved_model_id: string): string {
  const large_context = resolved_model_id.endsWith('[1m]');
  const identifier = large_context ? resolved_model_id.slice(0, -4) : resolved_model_id;
  const parts = identifier.split('-');
  if (parts[0] !== 'claude' || parts.length < 2) return resolved_model_id;
  const raw_family = parts[1]!;
  const family = raw_family.charAt(0).toUpperCase() + raw_family.slice(1).toLowerCase();
  const version_parts = parts.slice(2).filter((part) => !/^\d{8}$/.test(part));
  if (!version_parts.every((part) => /^\d+$/.test(part))) return resolved_model_id;
  const shown = version_parts.length === 0 ? family : `${family} ${version_parts.join('.')}`;
  return large_context ? `${shown} (1M)` : shown;
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function truthy(value: unknown): boolean {
  if (Array.isArray(value)) return value.length !== 0;
  if (object(value)) return Object.keys(value).length !== 0;
  return Boolean(value);
}

function listed_efforts(value: unknown): string[] {
  if (!truthy(value)) return [];
  if (typeof value === 'string') return [...value];
  if (Array.isArray(value))
    return value.filter((effort): effort is string => typeof effort === 'string');
  if (object(value)) return Object.keys(value);
  const kind =
    typeof value === 'boolean'
      ? 'bool'
      : typeof value === 'number' && Number.isInteger(value)
        ? 'int'
        : 'float';
  throw new TypeError(`'${kind}' object is not iterable`);
}

export function claude_model_catalog_from_init(info: unknown): ClaudeModelCatalog {
  if (!object(info) || !Array.isArray(info.models)) {
    throw new ClaudeModelCatalogUnavailable(
      "this claude's startup handshake does not report models",
    );
  }
  const models: ClaudeModel[] = [];
  const efforts_in_listed_order: string[] = [];
  let default_resolves_to: string | null = null;
  for (const entry of info.models) {
    if (!object(entry)) continue;
    const value = entry.value;
    const resolved = entry.resolvedModel;
    if (typeof value !== 'string' || typeof resolved !== 'string') continue;
    if (value === 'default') {
      default_resolves_to = resolved;
      continue;
    }
    const raw_efforts = entry.supportedEffortLevels;
    // Python iterates both lists and strings here. Preserve its consumed values.
    const efforts = listed_efforts(raw_efforts);
    for (const effort of efforts) {
      if (!efforts_in_listed_order.includes(effort)) efforts_in_listed_order.push(effort);
    }
    if (models.some((model) => model.model_id === resolved)) continue;
    models.push({
      model_id: resolved,
      // Python preserves a truthy displayName unchanged, even when malformed.
      display_name: (truthy(entry.displayName)
        ? entry.displayName
        : versioned_display_name(resolved)) as string,
      resolved_model_id: resolved,
      reasoning_effort_options: efforts,
    });
  }
  if (models.length === 0)
    throw new ClaudeModelCatalogUnavailable("claude's startup handshake named no models");
  return {
    models,
    reasoning_effort_options: efforts_in_listed_order,
    default_model_id:
      models.find((model) => model.resolved_model_id === default_resolves_to)?.model_id ?? null,
  };
}

export async function probe_claude_model_catalog(
  claude_executable: string,
  children: OwnedOneShotChildren = new OneShotChildren(),
): Promise<ClaudeModelCatalog> {
  const state: {
    child: ChildProcess | null;
    child_exit: Promise<void> | null;
    cli: Query | null;
    expired: boolean;
    child_error: Error | null;
  } = { child: null, child_exit: null, cli: null, expired: false, child_error: null };
  let release_input: () => void = () => {};
  const input_closed = new Promise<void>((resolve) => {
    release_input = resolve;
  });
  const prompt = async function* (): AsyncGenerator<SDKUserMessage> {
    await input_closed;
  };
  let timer: ReturnType<typeof setTimeout> | undefined;
  let info: unknown;
  let failure: unknown;
  let failed = false;
  let actual: Promise<unknown> | undefined;
  const close_query = () => {
    if (state.cli === null) return;
    state.cli.close();
    state.cli = null;
  };
  try {
    const operation = async () => {
      await ensure_cli_compatible('claude', claude_executable, { children });
      children.signal.throwIfAborted();
      if (state.expired)
        throw new ClaudeModelCatalogUnavailable(
          'claude did not finish its startup handshake in time',
        );
      const child_environment: NodeJS.ProcessEnv = {
        ...process.env,
        CLAUDE_CODE_ENTRYPOINT: 'sdk-ts',
        CLAUDE_AGENT_SDK_VERSION,
      };
      delete child_environment.CLAUDECODE;
      const { query } = await load_claude_sdk();
      children.signal.throwIfAborted();
      if (state.expired)
        throw new ClaudeModelCatalogUnavailable(
          'claude did not finish its startup handshake in time',
        );
      state.cli = query({
        prompt: prompt(),
        options: {
          pathToClaudeCodeExecutable: claude_executable,
          maxTurns: 1,
          env: child_environment,
          spawnClaudeCodeProcess(options) {
            const started = children.spawn(options.command, options.args, {
              cwd: options.cwd,
              // The SDK adds and removes launch variables. Python's environment remains canonical.
              env: child_environment,
              stdio: ['pipe', 'pipe', 'pipe'],
            });
            state.child = started;
            state.child_exit = new Promise<void>((resolve) => {
              started.once('close', () => resolve());
              started.once('error', (error) => {
                state.child_error = error;
              });
            });
            return started;
          },
        },
      });
      const info: unknown = await state.cli.initializationResult();
      close_query();
      release_input();
      if (state.child_exit !== null) await state.child_exit;
      if (state.child_error !== null) throw state.child_error;
      return info;
    };
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        state.expired = true;
        reject(
          new ClaudeModelCatalogUnavailable('claude did not finish its startup handshake in time'),
        );
      }, MODEL_CATALOG_TIMEOUT_SECONDS * 1000);
    });
    actual = operation();
    info = await Promise.race([actual, timeout]);
  } catch (cause) {
    failed = true;
    failure =
      cause instanceof ClaudeModelCatalogUnavailable
        ? cause
        : new ClaudeModelCatalogUnavailable(
            cause instanceof Error ? cause.message : String(cause),
            { cause },
          );
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    try {
      close_query();
    } catch (cause) {
      if (!failed) {
        failed = true;
        failure = new ClaudeModelCatalogUnavailable(
          cause instanceof Error ? cause.message : String(cause),
          { cause },
        );
      }
    }
    release_input();
    if (state.child !== null && state.child.exitCode === null && state.child.signalCode === null) {
      try {
        stop_one_shot_child(state.child);
      } catch (cause) {
        if (!failed) {
          failed = true;
          failure = new ClaudeModelCatalogUnavailable(
            cause instanceof Error ? cause.message : String(cause),
            { cause },
          );
        }
      }
    }
    if (state.child_exit !== null) await state.child_exit;
    // The timeout/abort observer cannot discard a still-running SDK operation.
    await actual?.catch(() => {});
  }
  if (failed) throw failure;
  // Python wraps startup failures, but transformation failures remain their own type.
  return claude_model_catalog_from_init(info);
}
