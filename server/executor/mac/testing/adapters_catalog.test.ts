/** Cleanup faults must not become optional model-list unavailability. */
import { afterEach, expect, it, vi } from 'vitest';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { load_claude_sdk } from '../../../conversation/backends/claude_sdk.ts';
import { ensure_cli_compatible } from '../../../conversation/backends/cli_compatibility.ts';
import {
  ClaudeModelCatalogUnavailable,
  probe_claude_model_catalog,
} from '../../../conversation/backends/claude_model_catalog.ts';
import { ModelCatalogCleanupFailed } from '../../../conversation/backends/catalog_cleanup.ts';
import { probe_codex_model_catalog } from '../../../conversation/backends/codex_app_server/model_catalog.ts';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../../../lifecycle/contracts.ts';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { RecordingBackendProbeEnvironment } from '../../../conversation/snapshot/recording_environment.ts';
import { create_mac_catalog } from '../catalog/production.ts';
import type { MacAdapterHost } from '../adapters.ts';
import { MacCommandQueues } from '../mailbox/queues.ts';
import { MacCatalog } from '../catalog/index.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { QueueDependencies } from '../mailbox/contracts.ts';

const { query } = vi.hoisted(() => ({
  query: vi.fn<(typeof import('@anthropic-ai/claude-agent-sdk'))['query']>(),
}));
vi.mock('@anthropic-ai/claude-agent-sdk', () => ({ query }));
vi.mock('../../../conversation/backends/cli_compatibility.ts', () => ({
  ensure_cli_compatible: vi.fn(),
}));
await load_claude_sdk();
afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});
function invocation_children(): OwnedChildProcesses {
  return {
    seal: vi.fn(),
    spawn: vi.fn(() => {
      throw new Error('No provider spawn in this proof.');
    }),
    retain_worker_unit: vi.fn(() => {
      throw new Error('No provider unit in this proof.');
    }),
    settled: async () => {},
    force_kill: vi.fn(),
  } as OwnedChildProcesses;
}

it('distinguishes an unavailable handshake from failed query cleanup and retains both errors', async () => {
  vi.mocked(ensure_cli_compatible).mockResolvedValue(undefined);
  const primary = new Error('scripted handshake refusal'),
    cleanup = new Error('scripted query close failure');
  query.mockReturnValueOnce({
    initializationResult: async () => {
      throw primary;
    },
    close: () => {},
  } as never);
  const unavailable = await probe_claude_model_catalog('/synthetic/claude').then(
    () => null,
    (error: unknown) => error,
  );
  expect(unavailable).toBeInstanceOf(ClaudeModelCatalogUnavailable);
  expect(unavailable).not.toBeInstanceOf(ModelCatalogCleanupFailed);
  const close = vi.fn().mockImplementationOnce(() => {
    throw cleanup;
  });
  query.mockReturnValueOnce({
    initializationResult: async () => {
      throw primary;
    },
    close,
  } as never);
  const child_processes = invocation_children(),
    one_shot_children = new OneShotChildren(child_processes);
  const environment = new RecordingBackendProbeEnvironment();
  environment.executables.set('claude', '/synthetic/claude');
  environment.outcomes.set(JSON.stringify(['/synthetic/claude', '--version']), {
    exit_code: 0,
    standard_output: '2.1.219',
    standard_error: '',
  });
  const notify = vi.fn();
  const catalog = create_mac_catalog({
    child_processes,
    one_shot_children,
    environment,
    on_failure: notify,
    host: {
      hermes_paths: {
        python: '/synthetic/python',
        home: '/synthetic/hermes',
        source_root: '/synthetic/source',
      },
    } as MacAdapterHost,
  });
  const failed = await catalog
    .read_result('cleanup', { backend_key: 'claude', refresh: true })
    .then(
      () => null,
      (error: unknown) => error,
    );
  expect(failed).toBeInstanceOf(ServerLifecycleError);
  const retained = (failed as Error).cause;
  expect(retained).toBeInstanceOf(ModelCatalogCleanupFailed);
  expect((retained as ModelCatalogCleanupFailed).primary).toBe(primary);
  expect((retained as ModelCatalogCleanupFailed).cleanup_errors).toContain(cleanup);
  await expect(catalog.stop({ deadline: performance.now() / 1000 + 1 })).rejects.toBeInstanceOf(
    AggregateError,
  );
  await expect(catalog.settled()).rejects.toBeInstanceOf(AggregateError);
  expect(close).toHaveBeenCalledTimes(2);
  expect(notify).toHaveBeenCalledExactlyOnceWith(failed);
  expect(child_processes.spawn).not.toHaveBeenCalled();
  one_shot_children.seal();
  await one_shot_children.settled();
});

it('retains the Codex probe failure when its exact client cleanup also fails', async () => {
  const primary = new Error('scripted Codex startup failure'),
    cleanup = new Error('scripted Codex stop failure');
  const invocation = invocation_children();
  const stop = vi.fn().mockRejectedValueOnce(cleanup).mockResolvedValue(undefined);
  const failed = await probe_codex_model_catalog('/synthetic/codex', {
    child_processes: invocation,
    client_factory: (options) => {
      expect(options.child_processes).toBe(invocation);
      return {
        start: async () => {
          throw primary;
        },
        stop,
        request: vi.fn(),
        notify: vi.fn(),
      } as never;
    },
  }).then(
    () => null,
    (error: unknown) => error,
  );
  expect(failed).toBeInstanceOf(ModelCatalogCleanupFailed);
  expect((failed as ModelCatalogCleanupFailed).primary).toBe(primary);
  expect((failed as ModelCatalogCleanupFailed).cleanup_errors).toContain(cleanup);
  expect(stop).toHaveBeenCalledOnce();
  await (failed as ModelCatalogCleanupFailed).retry_cleanup();
  expect(stop).toHaveBeenCalledTimes(2);
  expect(invocation.spawn).not.toHaveBeenCalled();
});

it('joins Claude direct exit without waiting for a child close event or signaling its reaped group', async () => {
  vi.mocked(ensure_cli_compatible).mockResolvedValue(undefined);
  const signal = vi.spyOn(process, 'kill').mockReturnValue(true);
  const child = Object.assign(new EventEmitter(), {
    pid: 424242,
    exitCode: null as number | null,
    signalCode: null,
    stdin: new PassThrough(),
    stdout: new PassThrough(),
    stderr: new PassThrough(),
  });
  const close_event = vi.fn();
  child.once('close', close_event);
  const children = {
    signal: new AbortController().signal,
    spawn: vi.fn(() => child),
    seal() {},
    settled: async () => {},
  } as unknown as OwnedOneShotChildren;
  query.mockImplementationOnce((options) => {
    options.options!.spawnClaudeCodeProcess!({
      command: '/synthetic/claude',
      args: [],
      cwd: '/synthetic',
      env: {},
    } as never);
    return {
      initializationResult: async () => {
        child.exitCode = 0;
        child.emit('exit', 0, null);
        return {
          models: [
            { value: 'haiku', resolvedModel: 'claude-haiku-4-5', supportedEffortLevels: ['low'] },
          ],
        };
      },
      close: () => {},
    } as never;
  });
  const result = await probe_claude_model_catalog('/synthetic/claude', children);
  expect(result.models[0]?.model_id).toBe('claude-haiku-4-5');
  expect(close_event).not.toHaveBeenCalled();
  expect(signal).not.toHaveBeenCalled();
  expect(child.stdout.destroyed && child.stderr.destroyed && child.stdin.destroyed).toBe(true);
});

it('fails queue ownership without a null result when the catalog reports a lifecycle fault', async () => {
  const finish = vi.fn(),
    notify = vi.fn(),
    healthy = vi.fn();
  const catalog_notify = vi.fn(),
    snapshot = vi.fn();
  const catalog = new MacCatalog({
    snapshots: { snapshot, retained: () => null },
    stop_sources: async () => {},
    on_failure: catalog_notify,
  });
  const primary = new Error('handshake failure'),
    cleanup = new Error('query close failure');
  const failure = catalog.resource_failure(
    new ModelCatalogCleanupFailed(primary, [cleanup], async () => {}),
  );
  expect(failure).toBeInstanceOf(ServerLifecycleError);
  const queues = new MacCommandQueues({
    runner: { admission_stopped: false },
    catalog,
    journal: { finish, finish_stream: finish, assert_healthy: healthy },
    result_finished: vi.fn(),
    on_failure: notify,
  } as unknown as QueueDependencies);
  queues.submit({
    command_sequence: 1,
    command_id: 'catalog',
    created_at_vps_ms: 1,
    child_command: null,
    model_list_request: { backend_key: 'claude', refresh: true },
  });
  await expect(queues.settled()).rejects.toBeInstanceOf(AggregateError);
  expect(notify).toHaveBeenCalledExactlyOnceWith(failure);
  expect(finish).not.toHaveBeenCalled();
  expect(healthy).not.toHaveBeenCalled();
  expect(snapshot).not.toHaveBeenCalled();
  expect(catalog_notify).toHaveBeenCalledExactlyOnceWith(failure);
  await expect(queues.stop({ deadline: performance.now() / 1000 + 1 })).rejects.toBeInstanceOf(
    AggregateError,
  );
  await expect(catalog.stop({ deadline: performance.now() / 1000 + 1 })).rejects.toBeInstanceOf(
    AggregateError,
  );
  await expect(catalog.settled()).rejects.toBeInstanceOf(AggregateError);
});
