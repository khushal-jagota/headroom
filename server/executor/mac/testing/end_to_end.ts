/** Hosted Python/VPS peer. One real Mac invocation, with scripted provider bindings. */
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { createInterface } from 'node:readline';
import { MacService } from '../host/service.ts';
import { production_bindings } from '../host/entry.ts';
import type { MacHostBindings } from '../host/contracts.ts';
import { create_mac_adapter_host } from '../adapters.ts';
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
import type { AcceptanceReceipt, QueuedEnvelope } from '../../wire.generated.ts';
import { text_message_content } from '../../../conversation/message_content.ts';

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
const concrete = production_bindings(configuration, input.environment);
let expected_input: { id: string; base64: string } | null = null;
let early_event = input.early_event === true;
let catalog_blocked = false,
  catalog_model = input.catalog_model ?? 'scripted',
  catalog_complete = input.catalog_complete ?? true;
const catalog_observed = { calls: 0, cancelled: 0 };
const calls = {
  starts: 0,
  prompts: 0,
  stops: 0,
  cancels: 0,
  answers: [] as unknown[],
  user_answers: [] as unknown[],
};
let active_sink: BackendEventSink | undefined, turn: TurnToken | undefined;
let retired_sink: BackendEventSink | undefined;
const bindings: MacHostBindings = {
  ...concrete,
  processes: () => null,
  adapters(owners) {
    const host = create_mac_adapter_host({
      ...owners,
      environment: input.environment,
      panels_server_url: origin.origin,
      executable_path: () => null,
    });
    return {
      ...host,
      factory: () => (values) => {
        const owned_sink = values.event_sink;
        retired_sink = active_sink;
        active_sink = values.event_sink;
        const unsupported = async (): Promise<never> => {
          throw new Error('Unscripted provider operation');
        };
        return {
          stop_outside_lock: false,
          withdraw_delivery: unsupported,
          detach: unsupported,
          async start() {
            calls.starts++;
          },
          async write_prompt(current) {
            if (expected_input)
              assert.equal(
                Buffer.from(
                  await values.message_files.read(current.conversation_id, expected_input.id),
                ).toString('base64'),
                expected_input.base64,
              );
            calls.prompts++;
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
            return new BackendSteerAccepted({ composed_content_delivered: true });
          },
          async cancel_running_turn() {
            calls.cancels++;
          },
          async stop() {
            calls.stops++;
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
      },
    };
  },
  catalog(_host, _owners, on_failure) {
    return new MacCatalog({
      on_failure,
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
            available_models: [
              {
                model_id: catalog_model,
                display_name: null,
                detail: null,
                reasoning_effort_options: ['low', 'medium', 'high', 'xhigh', 'max'],
                selected_variant: null,
                version: [],
                resolved_model_id: null,
              },
            ],
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
    catalog: catalog_observed,
    minted: published.length,
    receipts: receipts.map((receipt) => as_object(receipt, 'AcceptanceReceipt')),
    calls,
    turn,
    children: service.runner.native_children.retained_children().map((child) => ({
      reference: child.reference,
      capability: child.capability,
      ticket_id: child.ticket_id,
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
      arguments: { ticket_id: child.ticket_id, body: { recap } },
      child: child.reference,
      turn: turn ?? null,
    },
    submission_id,
    child.ticket_id,
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
      [],
      [],
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
    child.ticket_id,
  );
  const assigned = await service.gateway_outbox.add(
    { operation: 'ticket.unsupported', arguments: {}, child: child.reference, turn: null },
    randomUUID(),
    child.ticket_id,
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
      if (command.operation === 'prepare_update') {
        const prepared = await service.runner.prepare_update(
          'now',
          randomUUID(),
          new AbortController().signal,
        );
        write({ id: command.id, result: { prepared, ...(await state()) } });
        continue;
      }
      if (command.operation === 'emit') await emit(command.kind, command.native_id, command);
      else if (command.operation === 'configure') {
        if ('expected_input' in command) expected_input = command.expected_input;
        if ('early_event' in command) early_event = command.early_event;
        if ('catalog_blocked' in command) catalog_blocked = command.catalog_blocked;
        if ('catalog_model' in command) catalog_model = command.catalog_model;
        if ('catalog_complete' in command) catalog_complete = command.catalog_complete;
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
