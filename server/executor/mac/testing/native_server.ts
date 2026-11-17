/** Private real TypeScript home for the hosted Darwin package rehearsal. */
import { parse_backend_instance_id } from '../../../drivers/core/identity.ts';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as pause } from 'node:timers/promises';
import { createDatabaseFromSeed } from '../../../core/database/index.ts';
import type { ApplicationSchemaSeed } from '../../../core/database/contracts.ts';
import { startServer } from '../../../core/start.ts';
import type { ConversationCompositionContext } from '../../../core/start.ts';
import { OWNER } from '../../../core/contracts.ts';
import { bootstrap_application } from '../../../environments/bootstrap.ts';
import { create_test_environment_runtime } from '../../../environments/testing/composition.ts';
import { VpsExecutorRegistry } from '../../registry.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../../../execution_identity.ts';
import { PROTOCOL_VERSION } from '../../../execution_configuration.ts';
import { issue_original_credential } from '../../../machines/executor_credentials.ts';
import { compose_production_backends } from '../../../lifecycle/production_bindings.ts';
import { registry_of_factories, unread_driver_host } from '../../../drivers/testing/registry.ts';
import { create_production_conversation_core } from '../../../lifecycle/production.ts';
import { create_backend_runtime } from '../../../conversation/snapshot/composition.ts';
import type { SqliteProcessConversationSystem } from '../../../conversation/system.ts';
import {
  ConversationRoleMaterials,
  ConversationStartRequest,
  WorkspacePathPolicy,
  PromptDeliveryStarted,
} from '../../../conversation/contracts.ts';
import { MessageText, text_message_content } from '../../../conversation/message_content.ts';
import type { MessageContent } from '../../../conversation/message_content.ts';
import { create_ticket, write_ticket_conversation_start } from '../../../tickets/data/index.ts';

/** One deadline bounds a fixture observation. Production owns every operation and process. */
async function wait_until<T>(
  read: () => T | Promise<T>,
  accepted: (value: T) => boolean,
): Promise<T> {
  const signal = AbortSignal.timeout(90_000);
  for (;;) {
    signal.throwIfAborted();
    const value = await read();
    if (accepted(value)) return value;
    await pause(50, undefined, { signal });
  }
}

export async function start_native_server() {
  const path = process.env.PLAN_DB_PATH;
  assert.ok(path, 'A private home database path is required');
  assert.equal(process.env.PLAN_TEST_MODE, '1');
  assert.equal(process.env.PLAN_EXECUTOR_ID, 'test-executor');
  const seed = JSON.parse(
    readFileSync(
      new URL('../../../core/database/application_schema.json', import.meta.url),
      'utf8',
    ),
  ) as ApplicationSchemaSeed;
  const initial = createDatabaseFromSeed(path, seed, (tx) =>
    bootstrap_application(tx, Math.floor(Date.now() / 1000)),
  );
  initial.close();
  let context!: ConversationCompositionContext;
  let core!: SqliteProcessConversationSystem;
  let registry!: VpsExecutorRegistry;
  let bound!: ReturnType<typeof compose_production_backends>;
  const server = await startServer({
    createExecutor(current) {
      registry = new VpsExecutorRegistry(current.database, current.clock, {
        db_path: path,
        changes: current.changes,
        media_signal: current.media_signal,
        original_enabled: true,
        application_revision: current.config.app_sha ?? 'unknown',
      });
      return registry;
    },
    readOpenRuns: (current) => current.executor!.open_runs(),
    createConversationCore(current) {
      context = current;
      const local = () => {
        throw new Error('Native Mac rehearsal selected a local provider');
      };
      bound = compose_production_backends(
        current,
        {
          registry: registry_of_factories({ claude: local, codex: local, hermes: local }),
          host: unread_driver_host,
        },
        {
          drivers: [],
          driver_host: () => unread_driver_host,
          http: () =>
            create_backend_runtime({
              database: current.database,
              config: current.config,
              now: () => current.clock.now(),
              one_shot_children: current.one_shot_children,
            }),
          stall_sources: () => [],
        },
      );
      core = create_production_conversation_core(current, bound);
      return core;
    },
    createBackendServices: () => bound.http,
    createEnvironments: create_test_environment_runtime,
  });
  const endpoint = registry.get(ORIGINAL_MAC_MACHINE_ID)!;
  assert.ok(endpoint);
  const credential = issue_original_credential(context.database, randomUUID()).credential;
  return {
    address: server.address,
    context,
    core,
    registry,
    endpoint,
    credential,
    diagnostics() {
      return {
        health: endpoint.health_payload(),
        conversations: [...core.conversations].map(([id, state]) => ({
          id,
          phase: state.phase,
          running_turn: state.running_turn?.token ?? null,
          child_present: state.child !== null,
          retained_child: state.retained_child_reference,
          events: context.record.store.read_events_after(id, 0),
        })),
        durable: context.database.read((sql) =>
          Object.fromEntries(
            [
              'executor_commands',
              'executor_child_assignments',
              'mac_inbox_positions',
              'conversation_lifecycle_facts',
            ].map((table) => [table, sql.all(`SELECT * FROM ${table} LIMIT 100`)]),
          ),
        ),
      };
    },
    async wait_for_presence() {
      await wait_until(() => endpoint.presence.is_present(), Boolean);
      const identity = context.database.read((sql) =>
        sql.get<{ identity_json: string }>(
          'SELECT identity_json FROM mac_executor_presence WHERE machine_id=?',
          [ORIGINAL_MAC_MACHINE_ID],
        ),
      );
      assert.ok(identity);
      assert.equal(JSON.parse(identity.identity_json).protocol_version, 26);
      assert.equal(PROTOCOL_VERSION, 26);
      return endpoint.health_payload();
    },
    async create_turn(
      backend: 'claude' | 'codex',
      content: MessageContent = text_message_content('[CASE:plain]'),
    ) {
      const conversation_id = 'native_' + backend + '_' + randomUUID().replaceAll('-', '');
      const model = backend === 'claude' ? 'haiku' : 'gpt-6.1-sol';
      const ticket = context.database.write((tx) =>
        create_ticket(tx, {
          title: 'Native ' + backend + ' rehearsal',
          principal: OWNER,
          now: context.clock.nowUnix(),
          title_max_chars: 200,
          worker_type: 'coding',
          machine_id: ORIGINAL_MAC_MACHINE_ID,
          employee_backend: backend,
          employee_launch_model: model,
          employee_launch_reasoning_effort: null,
          reasoning_effort_is_set: true,
          model_source_is_historical: true,
        }),
      );
      await core.start_conversation(
        new ConversationStartRequest({
          conversation_id,
          backend_key: parse_backend_instance_id(backend),
          model,
          reasoning_effort: null,
          role_materials: new ConversationRoleMaterials({
            role_text: 'Run only the scripted native rehearsal.',
            identity_environment_variables: [
              ['PLAN_ACTOR', 'worker'],
              ['PLAN_TICKET_ID', ticket.id],
            ],
          }),
          workspace_folder: '/native-rehearsal/' + ticket.id,
          workspace_path_policy: WorkspacePathPolicy.executor_default,
          access: 'full',
        }),
      );
      context.database.write((tx) =>
        write_ticket_conversation_start(tx, ticket.id, {
          conversation_id,
          backend,
          model,
          reasoning_effort: null,
          now: context.clock.nowUnix(),
        }),
      );
      const fate = await core.send(conversation_id, content, { sender_label: 'owner' });
      assert.ok(
        fate instanceof PromptDeliveryStarted,
        `Native ${backend} prompt did not start: ${JSON.stringify(fate)}`,
      );
      return { conversation_id, ticket_id: ticket.id, fate };
    },
    async turn_result(conversation_id: string) {
      let events;
      try {
        events = await wait_until(
          () => context.record.store.read_events_after(conversation_id, 0),
          (values) => values.some((event) => event.payload.kind === 'turn_ended'),
        );
      } catch (error) {
        const evidence = process.env.MAC_REHEARSAL_EVIDENCE;
        if (evidence)
          await writeFile(
            join(evidence, 'server-turn-timeout.json'),
            JSON.stringify(this.diagnostics(), null, 2),
          );
        throw error;
      }
      const answers = events.filter((event) => event.payload.kind === 'agent_message');
      assert.ok(answers.length > 0);
      assert.ok(
        answers.some(
          (event) =>
            event.payload.kind === 'agent_message' &&
            event.payload.content.some(
              (piece) =>
                piece instanceof MessageText &&
                ['plain live text', 'plain ☃ output', 'plain answer'].includes(piece.text),
            ),
        ),
      );
      assert.ok(
        events.some(
          (event) => event.payload.kind === 'turn_ended' && event.payload.ending === 'completed',
        ),
      );
      return events;
    },
    async active_stop(conversation_id: string, claude_trace: string) {
      const fate = await core.send(conversation_id, text_message_content('[CASE:interrupt-base]'), {
        sender_label: 'owner',
      });
      assert.ok(
        fate instanceof PromptDeliveryStarted,
        `Active Stop prompt did not start: ${JSON.stringify(fate)}`,
      );
      await wait_until(
        async () =>
          (await readFile(claude_trace, 'utf8'))
            .split('\n')
            .filter(Boolean)
            .map((line) => JSON.parse(line)),
        (rows) => rows.some((row) => row.kind === 'prompt' && row.scenario === 'interrupt-base'),
      );
      assert.equal(await core.is_running(conversation_id), true);
      await this.stop_turn(conversation_id);
      assert.ok(
        ![...endpoint.runs.children.values()].some(
          (child) => child.reference.conversation_id === conversation_id,
        ),
      );
    },
    async stop_turn(conversation_id: string) {
      await core.interrupt(conversation_id);
      await wait_until(
        () => core.is_running(conversation_id),
        (running) => !running,
      );
      await core.kill(conversation_id);
    },
    async stop() {
      await server.close({ deadline: performance.now() / 1000 + 30 });
      await server.shutdown_settled;
    },
  };
}
