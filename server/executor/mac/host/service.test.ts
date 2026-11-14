/** Distinct host boundary: catalog cancellation precedes actual shutdown and root release. */
import { test, expect, vi } from 'vitest';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { MacService } from './service.ts';
import { production_bindings } from './entry.ts';
import type { MacHostBindings, MacHostContext, MacInvocation } from './contracts.ts';
import { MacHttpClient } from '../transport/http.ts';
import { create_mac_catalog } from '../catalog/production.ts';
import { ExecutorPaths } from '../configuration.ts';
import { create_mac_adapter_host } from '../adapters.ts';
import { request } from '../ipc_client.ts';
import { decode_json, decode_value, encode_bounded, parse_identity } from '../../protocol.ts';
import { Deferred } from '../../../conversation/runtime/state.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import type { CanonicalReader } from '../storage/journal_contracts.ts';
import { read_canonical_body } from '../mailbox/poll.ts';

test('quiesce cancels a catalog probe before native dispatch and joins HTTP before root reuse', async () => {
  const root = await mkdtemp(join(tmpdir(), 'mac-host-'));
  const environment = {
    ...process.env,
    HOME: join(root, 'home'),
    CODEX_HOME: join(root, 'codex'),
    CLAUDE_CONFIG_DIR: join(root, 'claude'),
    HERMES_HOME: join(root, 'hermes'),
    PLAN_HERMES_HOME: join(root, 'hermes'),
  };
  for (const home of [
    environment.HOME,
    environment.CODEX_HOME,
    environment.CLAUDE_CONFIG_DIR,
    environment.HERMES_HOME,
  ])
    await mkdir(join(home, 'skills'), { recursive: true });
  const configuration = {
    paths: new ExecutorPaths(join(root, 'state')),
    revision: 'synthetic',
    executor_id: 'synthetic',
    panels_server_url: 'https://127.0.0.1:9',
    runtime: { child: true, ready_path: null, handoff_path: null },
  };
  const production = production_bindings(configuration, environment);
  const poll_reply = decode_value(
    {
      commands: [
        {
          command_sequence: 1,
          command_id: 'catalog',
          created_at_vps_ms: 1700000000000,
          child_command: null,
          model_list_request: { backend_key: 'codex', refresh: true },
        },
      ],
    },
    'MailboxPollReply',
  );
  const reported = new Deferred<void>(),
    catalog_entered = new Deferred<void>();
  let catalog_outcome: unknown;
  let http_closed = false,
    catalog_cancelled = false,
    probes = 0;
  let invocation!: MacInvocation, context!: MacHostContext;
  class ScriptedClient extends MacHttpClient {
    private polled = false;
    async lease() {
      assert.equal(http_closed, false);
      return decode_value(
        {
          accepted_at_vps_ms: 1700000000000,
          away_after_seconds: 25,
          mailbox_high_sequence: 1,
          update: { schema_version: 1, target: null, prefetch: null },
        },
        'LeaseReply',
      );
    }
    async poll_commands(_after: number, _hold: number, signal: AbortSignal) {
      assert.equal(http_closed, false);
      signal.throwIfAborted();
      if (!this.polled) {
        this.polled = true;
        return encode_bounded(poll_reply, 'MailboxPollReply');
      }
      return await new Promise<Uint8Array>((_resolve, reject) =>
        signal.addEventListener('abort', () => reject(signal.reason), { once: true }),
      );
    }
    async report_outcomes(body: CanonicalReader) {
      assert.equal(http_closed, false);
      const report = decode_json(await read_canonical_body(body), 'CommandOutcomeReport');
      assert.equal(report.outcomes.length, 1);
      assert.equal(report.outcomes[0]!.command_id, 'catalog');
      catalog_outcome = report.outcomes[0]!.model_list;
      reported.resolve();
      return { recorded: ['catalog'], rejected: [] };
    }
    async stop(options: { deadline: number }) {
      await super.stop(options);
      await super.settled();
      http_closed = true;
    }
  }
  const bindings: MacHostBindings = {
    client: (url, executor, who, options) =>
      new ScriptedClient(url, executor, parse_identity(who), options),
    processes: () => null,
    adapters(owners) {
      invocation = owners;
      return create_mac_adapter_host({
        ...owners,
        panels_server_url: configuration.panels_server_url,
        environment,
        executable_path: () => null,
      });
    },
    catalog(adapters, owners, on_failure) {
      assert.equal(owners.child_processes, invocation.child_processes);
      assert.equal(owners.one_shot_children, invocation.one_shot_children);
      return create_mac_catalog({
        host: adapters,
        ...owners,
        on_failure,
        environment: {
          executable_path: () => '/synthetic/codex',
          configured_executable_path: () => null,
          real_path: (path) => path,
          user_local_npm_prefix: () => join(root, 'npm'),
          prefix_is_owned_and_writable: () => false,
          latest_released_version: async () => null,
          async run(argv, options) {
            if (argv[1] === '--version')
              return { exit_code: 0, standard_output: 'codex 1.0.0', standard_error: '' };
            assert.deepEqual(argv, ['/synthetic/codex', 'login', 'status']);
            probes++;
            const signal = options!.signal!;
            signal.throwIfAborted();
            catalog_entered.resolve();
            return new Promise((resolve) =>
              signal.addEventListener(
                'abort',
                () => {
                  catalog_cancelled = true;
                  resolve({ exit_code: 0, standard_output: '', standard_error: '' });
                },
                { once: true },
              ),
            );
          },
        },
      });
    },
    updates(value) {
      context = value;
      return production.updates(value);
    },
  };
  let service: MacService | undefined;
  const options = () => ({ deadline: performance.now() / 1000 + 10 });
  try {
    service = await MacService.open(configuration, bindings, options());
    expect(invocation.child_processes).toBe(service.child_processes);
    expect(invocation.one_shot_children).toBe(service.one_shot_children);
    expect(context.outbox).toBe(service.outbox);
    expect(context.update_files).not.toBe(service.files);
    const native_probe = vi.spyOn(service.child_processes, 'spawn');
    await service.start();
    await observe_shutdown(
      catalog_entered.promise,
      options(),
      'Catalog login probe did not start.',
    );
    const quiescence = await request(
      'quiesce',
      { exit_when_ready: false },
      { ...environment, PLAN_EXECUTOR_ROOT: service.configuration.paths.root },
    );
    expect(quiescence.error).toBeNull();
    await observe_shutdown(
      reported.promise,
      options(),
      'Cancelled catalog result was not reported.',
    );
    expect(catalog_outcome).toBeNull();
    expect(catalog_cancelled).toBe(true);
    expect(probes).toBe(1);
    await service.stop(options());
    await service.settled();
    expect(native_probe).not.toHaveBeenCalled();
    expect(http_closed).toBe(true);
    const replacement = await MacService.open(service.configuration, bindings, options());
    await replacement.stop(options());
    await replacement.settled();
  } finally {
    if (service) {
      await service.stop(options()).catch(() => {});
      await service.settled().catch(() => {});
    }
    await rm(root, { recursive: true, force: true });
  }
}, 20000);
