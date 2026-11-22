/** Hosted Python/VPS peer. One real Mac invocation, with scripted provider bindings. */
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { MacService } from '../host/service.ts';
import { production_bindings } from '../host/entry.ts';
import type { MacHostBindings } from '../host/contracts.ts';
import { create_mac_adapter_host, load_executor_drivers } from '../adapters.ts';
import { MacCatalog } from '../catalog/index.ts';
import { ExecutorPaths } from '../configuration.ts';
import { row_envelope } from '../storage/envelopes.ts';
import { as_object } from '../../protocol.ts';
import type { BackendEventSink, BackendChild } from '../../../conversation/backends/contracts.ts';
import {
  BackendPromptAccepted,
  BackendSteerAccepted,
  BackendPermissionAsk,
  BackendUserInputRequest,
  ToolCallStarted,
  ToolCallFinished,
  ToolCallProgress,
  AgentMessageDelta,
  AgentMessageCompleted,
  ModelThinkingHappened,
  VendorSessionCursorRebound,
  PermissionAskRaised,
  UserInputRequested,
  TurnEnded,
} from '../../../conversation/backends/contracts.ts';
import {
  PermissionAskOption,
  UserInputQuestion,
  UserInputOption,
} from '../../../conversation/events.ts';
import type { TurnToken } from '../../../conversation/backends/contracts.ts';
import type { ResolvedConversationStart } from '../../../conversation/contracts.ts';
import { WorkerSupervisor } from '../../../worker_supervision/service.ts';
import { RECORD_ROOT_ENV } from '../../../worker_supervision/config.ts';
import { worker_ticket_id } from '../../../worker_supervision/logic/worker_launch.ts';
import { child_ticket_id } from '../runner/logic/child_owner.ts';
import type { AcceptanceReceipt, QueuedEnvelope } from '../../wire.generated.ts';
import { text_message_content } from '../../../conversation/message_content.ts';
import { create_claude_backend_child_factory } from '../../../drivers/claude/runtime/adapter.ts';
import { create_claude_cleanup_fixture } from './claude_cleanup.ts';
import { runtime_archive } from '../updates/testing/runtime_archive.ts';
import { LINUX_EXECUTOR } from '../../../environments/release/contracts.ts';
import { LINUX_EXECUTOR_ENTRYPOINT } from '../../../environments/linux_executor/configuration.ts';

// Stdout belongs to the fixture protocol. Keep host diagnostics in the existing log.
console.debug = console.error;

const input = JSON.parse(await readFile(process.argv[2], 'utf8'));
// These values come from the parent's bound fixture, before MacService can open a root or HTTP session.
const origin = new URL(input.origin);
assert.equal(origin.protocol, 'https:');
assert.equal(origin.hostname, '127.0.0.1');
assert.equal(origin.origin, input.fixture_origin);
assert.equal(Number(origin.port), input.allocated_port);
assert.ok(input.allocated_port > 1023 && input.allocated_port !== 8767);
assert.equal(origin.pathname, '/');
assert.equal(origin.username + origin.password + origin.search + origin.hash, '');

const options = () => ({ deadline: performance.now() / 1000 + 20 });
const configuration = {
  paths: new ExecutorPaths(input.root),
  revision: input.revision,
  executor_id: 'test-executor',
  panels_server_url: origin.origin,
  runtime: input.runtime ?? { child: true, ready_path: null, handoff_path: null },
  http: { tls: { ca: await readFile(input.ca_path) } },
};
const drivers = await load_executor_drivers();
const concrete = production_bindings(configuration, input.environment, drivers);
const claude_cleanup = input.claude_cleanup ? create_claude_cleanup_fixture() : null;
let expected_input: { id: string; base64: string } | null = null;
let early_event = input.early_event === true;
let catalog_blocked = false,
  catalog_model = input.catalog_model ?? (claude_cleanup ? 'claude-old' : 'scripted'),
  catalog_complete = input.catalog_complete ?? true;
let catalog_models: string[] | undefined = claude_cleanup
  ? ['claude-old', 'claude-new']
  : undefined;
let held_steer: Promise<void> | undefined, release_steer: (() => void) | undefined;
let final_event_on_stop = false;
const catalog_observed = { calls: 0, cancelled: 0 };
const calls = {
  starts: 0,
  prompts: 0,
  stops: 0,
  stops_by_conversation: {} as Record<string, number>,
  cancels: 0,
  steers: 0,
  starts_details: [] as unknown[],
  deliveries: [] as unknown[],
  answers: [] as unknown[],
  user_answers: [] as unknown[],
};
let active_sink: BackendEventSink | undefined, turn: TurnToken | undefined;
let retired_sink: BackendEventSink | undefined;
const bindings: MacHostBindings = {
  ...concrete,
  adapters(owners) {
    const host = create_mac_adapter_host({
      ...owners,
      drivers,
      environment: input.environment,
      panels_server_url: origin.origin,
      executable_path: () => null,
    });
    const record_scripted_worker = async (resolved: ResolvedConversationStart) => {
      assert.ok(resolved.role_materials);
      const environment = Object.fromEntries(
        resolved.role_materials.identity_environment_variables,
      );
      environment.PLAN_CONVERSATION_ID = resolved.conversation_id;
      assert.ok(environment[RECORD_ROOT_ENV]);
      // These fixture natives never spawn a process. Preserve that exact evidence
      // through the invocation's real owner before the scripted start can succeed.
      const receipt = await WorkerSupervisor.create(environment, resolved.workspace_folder, {
        runtime: owners.worker_supervision,
        conversation_id: resolved.conversation_id,
        worker_ticket_id: worker_ticket_id(resolved),
      });
      assert.ok(receipt);
      await receipt.cancel_unspawned();
    };
    return {
      ...host,
      factory: (resolved) => {
        if (claude_cleanup && resolved.backend_key === 'claude') {
          const claude = host.drivers.find((driver) => driver.instance_id === 'claude')!;
          const launch = {
            claude_executable: claude.host.executable,
            environment_overrides: [['PLAN_SERVER_URL', origin.origin]] as [string, string][],
          };
          const create = create_claude_backend_child_factory(launch, {
            ...owners,
            client_factory: claude_cleanup.client_factory,
          });
          return (values) => {
            const child = create(values),
              start = child.start.bind(child);
            child.start = async (resolved, start_options) => {
              await record_scripted_worker(resolved);
              await start(resolved, start_options);
            };
            return child;
          };
        }
        return (values) => {
          const owned_sink = values.event_sink;
          retired_sink = active_sink;
          active_sink = values.event_sink;
          let stopped = false;
          const unsupported = async (): Promise<never> => {
            throw new Error('Unscripted provider operation');
          };
          return {
            stop_outside_lock: false,
            withdraw_delivery: unsupported,
            detach: unsupported,
            async start(resolved, start_options) {
              await record_scripted_worker(resolved);
              calls.starts++;
              calls.starts_details.push({
                conversation_id: resolved.conversation_id,
                model: resolved.model,
                reasoning_effort: resolved.reasoning_effort,
                ...start_options,
              });
            },
            async write_prompt(current, content, prompt_options) {
              if (expected_input)
                assert.equal(
                  Buffer.from(
                    await values.message_files.read(current.conversation_id, expected_input.id),
                  ).toString('base64'),
                  expected_input.base64,
                );
              calls.prompts++;
              calls.deliveries.push({
                turn: current,
                text: content.flatMap((piece) => ('text' in piece ? [piece.text] : [])).join('\n'),
                options: prompt_options,
              });
              turn = current;
              if (early_event)
                await owned_sink.report(
                  current,
                  new ToolCallStarted({
                    tool_call_id: `early-${calls.prompts}`,
                    title: 'Before prompt returns',
                    tool_kind: 'other',
                    detail: null,
                  }),
                );
              return new BackendPromptAccepted({ composed_content_delivered: true });
            },
            async steer() {
              calls.steers++;
              if (held_steer) await held_steer;
              if (stopped) throw new Error('Native stopped after the steer invocation.');
              return new BackendSteerAccepted({ composed_content_delivered: true });
            },
            async cancel_running_turn() {
              calls.cancels++;
            },
            async stop() {
              stopped = true;
              release_steer?.();
              if (final_event_on_stop)
                await owned_sink.report(
                  null,
                  new VendorSessionCursorRebound({ vendor_session_cursor: 'cleanup-cursor' }),
                );
              calls.stops++;
              calls.stops_by_conversation[resolved.conversation_id] =
                (calls.stops_by_conversation[resolved.conversation_id] ?? 0) + 1;
            },
            async answer_permission_ask(id, option) {
              calls.answers.push([id, option]);
              return 'applied';
            },
            async answer_user_input(id, answers) {
              calls.user_answers.push([id, answers]);
              return 'applied';
            },
          } as BackendChild;
        };
      },
    };
  },
  catalog(_host, _owners, on_failure) {
    return new MacCatalog({
      on_failure,
      backend_ids: drivers.map(({ driver_kind }) => driver_kind),
      async stop_sources() {},
      snapshots: {
        retained: () => null,
        async snapshot(backend_key, read_options) {
          catalog_observed.calls++;
          if (catalog_blocked) {
            const signal = read_options!.signal!;
            signal.throwIfAborted();
            await new Promise<never>((_resolve, reject) =>
              signal.addEventListener(
                'abort',
                () => {
                  catalog_observed.cancelled++;
                  reject(signal.reason);
                },
                { once: true },
              ),
            );
          }
          return {
            backend_key,
            installed: true,
            executable_path: '/scripted',
            version: '1.0.0',
            identity: null,
            available_models: (catalog_models ?? [catalog_model]).map((model_id) => ({
              model_id,
              display_name: null,
              detail: null,
              reasoning_effort_options: ['low', 'medium', 'high', 'xhigh', 'max'],
              selected_variant: null,
              version: [],
              resolved_model_id: null,
            })),
            reasoning_effort_options: ['low', 'medium', 'high', 'xhigh', 'max'],
            default_model_id: catalog_model,
            default_reasoning_effort: 'medium',
            update_advisory: null,
            diagnoses: [],
            models_complete: catalog_complete,
          };
        },
      },
    });
  },
};
const service = await MacService.open(configuration, bindings, options());
const published: QueuedEnvelope[] = [],
  receipts: AcceptanceReceipt[] = [];
const append = service.outbox.append.bind(service.outbox);
service.outbox.append = (...args: Parameters<typeof append>) => {
  const row = append(...args);
  published.push(row_envelope(service.outbox.sender_id, row));
  return row;
};
const confirmed = service.gateway_outbox.notify_confirmed.bind(service.gateway_outbox);
service.gateway_outbox.notify_confirmed = (receipt) => {
  receipts.push(receipt);
  confirmed(receipt);
};
const serialise = (value: unknown) =>
  JSON.stringify(value, (_key, item) => (typeof item === 'bigint' ? Number(item) : item));
const write = (value: unknown) => process.stdout.write(serialise(value) + '\n');
async function state() {
  const unreported = await service.journal.unreported(1);
  let waiting = false;
  try {
    for await (const result of unreported) {
      waiting = true;
      await result.result.close();
    }
  } finally {
    await unreported.close();
  }
  return {
    identity: service.identity,
    leased: service.presence.leased(),
    admission_stopped: service.runner.admission_stopped,
    planned_update_replay: service.runner.planned_update_replay,
    claude: claude_cleanup?.state(),
    catalog: catalog_observed,
    minted: published.length,
    receipts: receipts.map((receipt) => as_object(receipt, 'AcceptanceReceipt')),
    calls,
    turn,
    children: service.runner.native_children.retained_children().map((child) => ({
      reference: child.reference,
      capability: child.capability,
      ticket_id: child_ticket_id(child.owner),
    })),
    pending: service.outbox
      .pending()
      .map((row) => as_object(row_envelope(service.outbox.sender_id, row), 'QueuedEnvelope')),
    unreported: waiting,
  };
}
async function emit(
  kind: string,
  id?: string,
  values: { text?: string; detail?: string; turn_offset?: number; retired?: boolean } = {},
) {
  const sink = values.retired ? retired_sink : active_sink;
  const current = turn;
  assert.ok(sink && current);
  const token = { ...current, turn_number: current.turn_number + (values.turn_offset ?? 0) };
  if (kind === 'delta')
    return sink.report(token, new AgentMessageDelta({ text_delta: values.text ?? '' }));
  if (kind === 'thinking') return sink.report(token, new ModelThinkingHappened());
  if (kind === 'progress')
    return sink.report(
      token,
      new ToolCallProgress({ tool_call_id: id ?? 'tool', detail: values.detail ?? '' }),
    );
  if (kind === 'completed')
    return sink.report(
      token,
      new AgentMessageCompleted({ content: text_message_content(values.text ?? 'Full reply') }),
    );
  if (kind === 'tool_finished')
    return sink.report(
      token,
      new ToolCallFinished({
        tool_call_id: id ?? 'tool',
        tool_call_status: 'completed',
        detail: values.detail ?? null,
      }),
    );
  if (kind === 'end') return sink.report(token, new TurnEnded({ ending: 'completed' }));
  if (kind === 'tool')
    return sink.report(
      token,
      new ToolCallStarted({
        tool_call_id: id ?? 'idle',
        title: 'While idle',
        tool_kind: 'other',
        detail: null,
      }),
    );
  if (kind === 'ask')
    return sink.report(
      token,
      new PermissionAskRaised({
        ask: new BackendPermissionAsk({
          ask_id: 'ask_permission',
          title: 'Run the scripted action?',
          detail: null,
          options: [
            new PermissionAskOption({
              option_id: 'allow',
              label: 'Allow',
              option_kind: 'allow_once',
            }),
            new PermissionAskOption({
              option_id: 'deny',
              label: 'Deny',
              option_kind: 'reject_once',
            }),
          ],
        }),
      }),
    );
  assert.equal(kind, 'input');
  return sink.report(
    token,
    new UserInputRequested({
      request: new BackendUserInputRequest({
        request_id: id ?? 'request_input',
        questions: [
          new UserInputQuestion({
            question_id: 'q_one',
            header: 'First',
            question: 'Choose one',
            options: [
              new UserInputOption({ label: 'Yes', description: 'Proceed' }),
              new UserInputOption({ label: 'No', description: 'Decline' }),
            ],
            multi_select: false,
            allow_other: false,
          }),
          new UserInputQuestion({
            question_id: 'q_two',
            header: 'Colors',
            question: 'Choose colors',
            options: [
              new UserInputOption({ label: 'Red', description: 'Warm' }),
              new UserInputOption({ label: 'Blue', description: 'Cool' }),
            ],
            multi_select: true,
            allow_other: false,
          }),
        ],
      }),
    }),
  );
}
async function publish_edit(recap: string, submission_id: string) {
  const child = service.runner.native_children.retained_children()[0]!;
  return service.gateway_outbox.add(
    {
      operation: 'ticket.edit',
      arguments: { ticket_id: child_ticket_id(child.owner), body: { recap } },
      child: child.reference,
      turn: turn ?? null,
    },
    submission_id,
    child_ticket_id(child.owner),
  );
}
async function publish_refusals() {
  const child = service.runner.native_children.retained_children()[0]!;
  const malformed = row_envelope(
    service.outbox.sender_id,
    service.outbox.append(
      {
        conversation_id: child.reference.conversation_id,
        child_id: child.reference.child_id,
        kind: 'child_stopped',
      },
      Buffer.from(
        JSON.stringify({
          payload: { child: child.reference, disposition: 'unsupported' },
          files: [],
        }),
      ),
      () => {},
    ),
  );
  const unassigned = await service.gateway_outbox.add(
    {
      operation: 'ticket.update',
      arguments: {},
      child: { ...child.reference, child_id: 'unassigned' },
      turn: null,
    },
    randomUUID(),
    child_ticket_id(child.owner),
  );
  const assigned = await service.gateway_outbox.add(
    { operation: 'ticket.unsupported', arguments: {}, child: child.reference, turn: null },
    randomUUID(),
    child_ticket_id(child.owner),
  );
  await emit('ask');
  const following = published.at(-1)!;
  assert.equal(following.kind, 'backend_event');
  return {
    published: [malformed, unassigned, assigned].map((envelope) =>
      as_object(envelope, 'QueuedEnvelope'),
    ),
    following: as_object(following, 'QueuedEnvelope'),
  };
}
const lines = createInterface({ input: process.stdin });
try {
  await service.start();
  write({ ready: true });
  for await (const line of lines) {
    const command = JSON.parse(line);
    try {
      if (command.operation === 'stop') {
        await service.stop(options());
        await service.settled();
        write({ id: command.id, result: { stopped: true, calls } });
        break;
      }
      if (command.operation === 'runtime_archive') {
        const entrypoint = `await import(${JSON.stringify(import.meta.url)});\n`;
        const archive = runtime_archive(command.revision, false, LINUX_EXECUTOR, entrypoint);
        const path = join(input.root, 'fixture-native-runtime.tar.gz');
        await writeFile(path, archive.bytes);
        const runtime = await service.update_files.extract_runtime(
          path,
          join(input.root, 'fixture-native-releases'),
          archive.sha,
          archive.bytes.length,
          command.revision,
          LINUX_EXECUTOR,
        );
        const revision = await service.update_files.read_runtime_revision(
          runtime,
          command.revision,
          LINUX_EXECUTOR,
        );
        write({
          id: command.id,
          result: {
            base64: archive.bytes.toString('base64'),
            sha256: archive.sha,
            byte_count: archive.bytes.length,
            revision,
            entrypoint: join(runtime, LINUX_EXECUTOR_ENTRYPOINT),
          },
        });
        continue;
      }
      if (command.operation === 'admit_cached_runtime') {
        const runtime = join(
          input.root,
          'updates',
          'releases',
          command.revision + '-' + command.sha256,
        );
        const revision = await service.update_files.read_runtime_revision(
          runtime,
          command.revision,
          LINUX_EXECUTOR,
        );
        write({ id: command.id, result: { revision, runtime } });
        continue;
      }
      if (command.operation === 'publish') {
        assert.equal(command.publication, 'ticket.edit');
        write({
          id: command.id,
          result: as_object(
            await publish_edit(command.recap, command.submission_id),
            'QueuedEnvelope',
          ),
        });
        continue;
      }
      if (command.operation === 'publish_refusal_sequence') {
        write({ id: command.id, result: await publish_refusals() });
        continue;
      }
      if (command.operation === 'claude_finish_turn') claude_cleanup!.finish_turn();
      else if (command.operation === 'claude_hold_cleanup') claude_cleanup!.hold_cleanup();
      else if (command.operation === 'claude_release_cleanup') claude_cleanup!.release_cleanup();
      else if (command.operation === 'emit') await emit(command.kind, command.native_id, command);
      else if (command.operation === 'configure') {
        if ('expected_input' in command) expected_input = command.expected_input;
        if ('early_event' in command) early_event = command.early_event;
        if ('catalog_blocked' in command) catalog_blocked = command.catalog_blocked;
        if ('catalog_model' in command) catalog_model = command.catalog_model;
        if ('catalog_models' in command) catalog_models = command.catalog_models;
        if ('catalog_complete' in command) catalog_complete = command.catalog_complete;
        if ('final_event_on_stop' in command) final_event_on_stop = command.final_event_on_stop;
        if ('hold_steer' in command) {
          if (command.hold_steer)
            held_steer = new Promise((resolve) => {
              release_steer = resolve;
            });
          else {
            release_steer?.();
            held_steer = undefined;
          }
        }
      } else assert.equal(command.operation, 'state');
      write({ id: command.id, result: await state() });
    } catch (error) {
      write({ id: command.id, error: error instanceof Error ? error.stack : String(error) });
    }
  }
} finally {
  lines.close();
  try {
    await service.stop(options());
  } finally {
    await service.settled();
  }
}
