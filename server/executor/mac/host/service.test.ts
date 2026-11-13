/** Assembly risk: independently correct owners receive different stores/scopes or close out of order. */
import { test, expect } from 'vitest';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import type { IncomingMessage } from 'node:http';
import { createHash } from 'node:crypto';
import { MacService } from './service.ts';
import { production_bindings } from './entry.ts';
import type { MacHostBindings, MacHostContext, MacInvocation } from './contracts.ts';
import { MacHttpClient } from '../transport/http.ts';
import { create_mac_catalog } from '../catalog/production.ts';
import { ExecutorPaths } from '../configuration.ts';
import { create_mac_adapter_host } from '../adapters.ts';
import { request } from '../ipc_client.ts';
import { decode_json, decode_value, encode_bounded } from '../../protocol.ts';
import { Deferred } from '../../../conversation/runtime/state.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { BackendPromptAccepted, TurnEnded } from '../../../conversation/backends/contracts.ts';
import type { BackendChild, BackendEventSink } from '../../../conversation/backends/contracts.ts';
import type { CanonicalReader } from '../storage/journal_contracts.ts';
import type { MacHttpOptions } from '../transport/contracts.ts';
import type { ExecutorIdentity } from '../../../execution_identity.ts';
import { parse_identity } from '../../protocol.ts';
import type { ItemBatch, CommandOutcome } from '../../wire.generated.ts';
import { read_canonical_body } from '../mailbox/poll.ts';

test('composes the Mac invocation and joins HTTP, providers and workers before the spool closes', async () => {
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
  await mkdir(join(root, 'workspace'));
  const identity = parse_identity({
    protocol_version: 22,
    spool_format_version: 5,
    application_revision: 'synthetic',
    executor_incarnation: '00000000-0000-0000-0000-000000000001',
    connection_generation: 0,
  });
  const adopted_identity = parse_identity({
    ...identity,
    executor_incarnation: '00000000-0000-0000-0000-000000000002',
    connection_generation: 4,
  });
  const handoff_path = join(root, 'handoff.json');
  const configuration = {
    paths: new ExecutorPaths(join(root, 'state')),
    revision: 'synthetic',
    executor_id: 'synthetic',
    panels_server_url: 'https://127.0.0.1:9',
    identity,
    runtime: { child: true, ready_path: null, handoff_path },
  };
  const production = production_bindings(configuration, environment);
  await writeFile(
    handoff_path,
    JSON.stringify({
      executor_incarnation: adopted_identity.executor_incarnation,
      connection_generation: 4,
      replay_pending: true,
    }),
  );
  const child = {
    executor_incarnation: adopted_identity.executor_incarnation,
    child_id: 'child',
    conversation_id: 'conversation',
  };
  const start = {
    command_id: 'start',
    child,
    operation: 'start',
    turn: null,
    arguments: {
      kind: 'start',
      resolved_start: {
        conversation_id: 'conversation',
        backend_key: 'codex',
        model: 'scripted',
        reasoning_effort: null,
        access: 'full',
        workspace_path_policy: 'absolute',
        workspace_folder: join(root, 'workspace'),
        role_materials: {
          role_text: 'scripted worker',
          identity_environment_variables: [
            ['PLAN_ACTOR', 'worker'],
            ['PLAN_TICKET_ID', 'ticket'],
          ],
        },
      },
      canonical_skill_snapshot: {
        sources: ['panels', 'panels-worker'].map((skill_name) => ({
          skill_name,
          source_text: `---\nname: ${skill_name}\ndescription: Synthetic host skill\n---\nHost fixture.\n`,
        })),
        required_skill_names: ['panels', 'panels-worker'],
      },
      vendor_session_cursor: null,
      workspace_home_relative: null,
    },
  };
  const prompt = {
    command_id: 'prompt',
    child,
    operation: 'write_prompt',
    turn: { conversation_id: 'conversation', turn_number: 1 },
    arguments: {
      kind: 'write_prompt',
      content: [
        {
          stored_file_id: 'f_input',
          file_name: 'input.txt',
          media_type: 'text/plain',
          byte_count: 11,
        },
      ],
      sender_content: [
        {
          stored_file_id: 'f_input',
          file_name: 'input.txt',
          media_type: 'text/plain',
          byte_count: 11,
        },
      ],
      sender_label: 'Test',
      mode: 'send_now',
      sender_message_count: 1,
      model_change: null,
      reasoning_effort_change: null,
      reasoning_effort_is_set: false,
      automatic_compaction: true,
    },
  };
  const commands: unknown[] = [start, prompt].map((command, index) => ({
    command_sequence: index + 1,
    command_id: command.command_id,
    created_at_vps_ms: 1700000000000,
    child_command: command,
    model_list_request: null,
  }));
  commands.push({
    command_sequence: 3,
    command_id: 'catalog',
    created_at_vps_ms: 1700000000000,
    child_command: null,
    model_list_request: { backend_key: 'codex', refresh: true },
  });
  const poll_reply = decode_value({ commands }, 'MailboxPollReply');
  const reported = new Deferred<void>();
  const native_reported = new Deferred<void>();
  const catalog_entered = new Deferred<void>();
  let catalog_outcome: unknown;
  const seen = new Set<string>();
  const outcomes: CommandOutcome[] = [];
  const calls: string[] = [];
  let native_stopped = false,
    http_closed = false,
    probes = 0;
  let invocation!: MacInvocation, context!: MacHostContext;
  class ScriptedClient extends MacHttpClient {
    private polled = false;
    constructor(url: string, executor: string, who: ExecutorIdentity, options: MacHttpOptions) {
      super(url, executor, who, options);
      assert.deepEqual(who, adopted_identity);
      this.sessions.request = async (input, _headers, read) => {
        assert.equal(http_closed, false);
        assert.equal(input.path, '/api/executor/commands/prompt/files/f_input');
        const bytes = Buffer.from('input bytes');
        const response = Object.assign(Readable.from([bytes]), {
          statusCode: 200,
          headers: {
            'x-panels-file-sha256': createHash('sha256').update(bytes).digest('hex'),
            'content-length': String(bytes.length),
          },
        });
        return read(response as unknown as IncomingMessage);
      };
    }
    async lease() {
      assert.equal(http_closed, false);
      calls.push('lease');
      return decode_value(
        {
          accepted_at_vps_ms: 1700000000000,
          away_after_seconds: 25,
          mailbox_high_sequence: 3,
          update: { schema_version: 1, target: null, prefetch: null },
        },
        'LeaseReply',
      );
    }
    async poll_commands(_after: bigint, _hold: number, signal: AbortSignal) {
      assert.equal(http_closed, false);
      signal.throwIfAborted();
      if (!this.polled) {
        this.polled = true;
        return encode_bounded(poll_reply, 'MailboxPollReply', {
          limit: 4 * 1024 * 1024,
        });
      }
      return await new Promise<Uint8Array>((_resolve, reject) =>
        signal.addEventListener('abort', () => reject(signal.reason), { once: true }),
      );
    }
    async report_outcomes(body: CanonicalReader) {
      assert.equal(http_closed, false);
      const report = decode_json(await read_canonical_body(body), 'CommandOutcomeReport');
      for (const item of report.outcomes) {
        if (item.child_outcome) outcomes.push(item.child_outcome);
        if (item.command_id === 'catalog') catalog_outcome = item.model_list;
        seen.add(item.command_id);
      }
      if (seen.has('start') && seen.has('prompt')) native_reported.resolve();
      if (seen.size === 3) reported.resolve();
      return { recorded: report.outcomes.map((item) => item.command_id), rejected: [] };
    }
    async post_items(_conversation: string, batch: ItemBatch) {
      assert.equal(http_closed, false);
      return decode_value(
        {
          receipts: batch.items.map(({ envelope }) => ({
            sender_id: batch.sender_id,
            submission_id: envelope.submission_id,
            disposition: 'accepted',
            result: {},
            accepted_at_vps_ms: 1700000000000,
          })),
          stop: null,
        },
        'ItemBatchReply',
      );
    }
    stop(options: { deadline: number }) {
      http_closed = true;
      calls.push('http-stop');
      return super.stop(options);
    }
  }
  const bindings: MacHostBindings = {
    client: (url, executor, who, options) =>
      new ScriptedClient(url, executor, parse_identity(who), options),
    processes: () => ({
      async stop() {},
      async settled() {},
      async end_orphans() {
        calls.push('orphans');
      },
      async record_children() {
        calls.push('record');
      },
    }),
    adapters(owners) {
      invocation = owners;
      const adapters = create_mac_adapter_host({
        ...owners,
        panels_server_url: 'https://127.0.0.1:9',
        environment,
        executable_path: () => null,
      });
      return {
        ...adapters,
        factory: () => (values) => {
          const sink = values.event_sink as BackendEventSink;
          const unsupported = async (): Promise<never> => {
            throw new Error('Unexpected scripted provider call');
          };
          return {
            stop_outside_lock: false,
            withdraw_delivery: unsupported,
            detach: unsupported,
            steer: unsupported,
            cancel_running_turn: unsupported,
            answer_permission_ask: unsupported,
            answer_user_input: unsupported,
            async start(resolved) {
              assert.equal(
                new Map(resolved.role_materials!.identity_environment_variables).get(
                  'PLAN_EXECUTOR_ROOT',
                ),
                join(root, 'state'),
              );
              calls.push('native-start');
            },
            async write_prompt(turn) {
              assert.equal(
                Buffer.from(await values.message_files.read('conversation', 'f_input')).toString(),
                'input bytes',
              );
              await sink.report(
                turn,
                new TurnEnded({
                  ending: 'completed',
                  error_summary: null,
                  standard_error_tail: null,
                }),
              );
              return new BackendPromptAccepted({ composed_content_delivered: true });
            },
            async stop() {
              native_stopped = true;
              calls.push('native-stop');
            },
          } as BackendChild;
        },
      };
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
            // The subprocess finishes at cancellation. No native catalog can start behind it.
            return new Promise((resolve) =>
              signal.addEventListener(
                'abort',
                () =>
                  resolve({
                    exit_code: 0,
                    standard_output: '',
                    standard_error: '',
                  }),
                { once: true },
              ),
            );
          },
        },
      });
    },
    updates(value) {
      if (!context) assert.equal(value.runner.planned_update_replay, true);
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
    await service.start();
    await observe_shutdown(
      Promise.all([native_reported.promise, catalog_entered.promise]),
      options(),
      'Scripted host did not report its commands.',
    );
    expect(probes).toBe(1);
    expect(outcomes).toMatchObject([
      { command_id: 'start', disposition: 'succeeded' },
      { command_id: 'prompt', disposition: 'succeeded' },
    ]);
    const status = await request(
      'health',
      {},
      { ...environment, PLAN_EXECUTOR_ROOT: service.configuration.paths.root },
    );
    expect(status.result!.identity).toEqual(adopted_identity);
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
    expect(calls[0]).toBe('orphans');
    await service.stop(options());
    await service.settled();
    expect(native_stopped).toBe(true);
    expect(http_closed).toBe(true);
    await expect(service.start()).rejects.toThrow('shutting down');
    // The root lock is last: a replacement opens only after the host's actual join.
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
