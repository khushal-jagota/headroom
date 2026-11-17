/** Private real TypeScript home for the hosted Darwin package rehearsal. */
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
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
import { create_production_conversation_core } from '../../../lifecycle/production.ts';
import {
  create_backend_runtime,
  unavailable_backend_providers,
} from '../../../conversation/snapshot/composition.ts';
import type { SqliteProcessConversationSystem } from '../../../conversation/system.ts';
import {
  ConversationRoleMaterials,
  ConversationStartRequest,
  WorkspacePathPolicy,
} from '../../../conversation/contracts.ts';
import {
  MessageFile,
  MessageText,
  text_message_content,
} from '../../../conversation/message_content.ts';
import type { MessageContent } from '../../../conversation/message_content.ts';
import { ConversationMessageFiles } from '../../../conversation/record/message_files.ts';
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
      bound = compose_production_backends(current, {
        claude: () => local,
        codex: () => local,
        hermes: () => local,
        http: () =>
          create_backend_runtime({
            database: current.database,
            config: current.config,
            now: () => current.clock.now(),
            providers: unavailable_backend_providers(),
            one_shot_children: current.one_shot_children,
          }),
        stall_sources: () => [],
      });
      core = create_production_conversation_core(current, bound);
      return core;
    },
    createBackendServices: () => bound.http,
    createEnvironments: create_test_environment_runtime,
  });
  const endpoint = registry.get(ORIGINAL_MAC_MACHINE_ID)!;
  assert.ok(endpoint);
  const credential = issue_original_credential(context.database, randomUUID()).credential;
  const files = new ConversationMessageFiles(path);
  return {
    address: server.address,
    context,
    core,
    registry,
    endpoint,
    credential,
    message_files: files,
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
      attachment?: Uint8Array,
    ) {
      const conversation_id = 'native_' + backend + '_' + randomUUID().replaceAll('-', '');
      const model = backend === 'claude' ? 'claude-sonnet-4-5' : 'gpt-6.1-sol';
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
          backend_key: backend,
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
      if (attachment) {
        const kept = await files.keep(conversation_id, attachment, { media_type: 'text/plain' });
        content = [
          ...content,
          new MessageFile({
            stored_file_id: kept.stored_file_id,
            media_type: kept.media_type,
            byte_count: kept.byte_count,
            file_name: 'native-input.txt',
          }),
        ];
      }
      const fate = await core.send(conversation_id, content, { sender_label: 'owner' });
      return { conversation_id, ticket_id: ticket.id, fate };
    },
    async turn_result(conversation_id: string) {
      const events = await wait_until(
        () => context.record.store.read_events_after(conversation_id, 0),
        (values) => values.some((event) => event.payload.kind === 'turn_ended'),
      );
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
    async wait_for_turn(conversation_id: string) {
      return this.turn_result(conversation_id);
    },
    async active_stop(conversation_id: string, claude_trace: string) {
      await core.send(conversation_id, text_message_content('[CASE:interrupt-base]'), {
        sender_label: 'owner',
      });
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
    async files(options: {
      root: string;
      runtime: string;
      environment: NodeJS.ProcessEnv;
      claude_trace: string;
      command: (args: string[]) => Promise<unknown>;
    }) {
      const expected = Buffer.from('native transfer: \u03bb and \u96ea\n');
      const turn = await this.create_turn(
        'claude',
        text_message_content('native file transfer'),
        expected,
      );
      await this.turn_result(turn.conversation_id);
      const events = context.record.store.read_events_after(turn.conversation_id, 0);
      const prompt = events.find((event) => event.payload.kind === 'prompt');
      assert.ok(prompt && prompt.payload.kind === 'prompt');
      const piece = prompt.payload.content.find((item) => item instanceof MessageFile);
      assert.ok(piece instanceof MessageFile);
      assert.deepEqual(
        await readFile(
          join(
            options.root,
            'artifacts/files/conversations',
            turn.conversation_id,
            piece.stored_file_id,
          ),
        ),
        expected,
      );
      const launch = await wait_until(
        async () =>
          (await readFile(options.claude_trace, 'utf8'))
            .split('\n')
            .filter(Boolean)
            .map((line) => JSON.parse(line)),
        (rows) =>
          rows.some(
            (row) =>
              row.kind === 'launch' &&
              row.environment.PLAN_CONVERSATION_ID === turn.conversation_id,
          ),
      );
      const identity = launch.find(
        (row) =>
          row.kind === 'launch' && row.environment.PLAN_CONVERSATION_ID === turn.conversation_id,
      ).environment;
      const relative_path = 'artifacts/native-upload.txt';
      const artifact = join(options.root, 'artifacts/tickets', turn.ticket_id, relative_path);
      await mkdir(dirname(artifact), { recursive: true });
      await writeFile(artifact, expected);
      const ipc = await import(
        pathToFileURL(join(options.runtime, 'server/executor/mac/ipc_client.ts')).href
      );
      const response = await ipc.request(
        'write',
        {
          operation: 'ticket.propose',
          arguments: {
            ticket_id: turn.ticket_id,
            body: { body: `[Native upload](/files/tickets/${turn.ticket_id}/${relative_path})` },
          },
        },
        { ...options.environment, ...identity },
      );
      assert.equal(response.error, null);
      assert.equal(response.unconfirmed, undefined);
      await this.verify_upload(turn.ticket_id, relative_path, expected);
      const saved = context.database.read((sql) =>
        sql.get<{ pending_proposal: string }>('SELECT pending_proposal FROM tickets WHERE id=?', [
          turn.ticket_id,
        ]),
      );
      assert.ok(saved?.pending_proposal?.includes('Native upload'));
      await this.stop_turn(turn.conversation_id);
    },
    async stop_turn(conversation_id: string) {
      await core.interrupt(conversation_id);
      await wait_until(
        () => core.is_running(conversation_id),
        (running) => !running,
      );
      await core.kill(conversation_id);
    },
    async verify_upload(ticket_id: string, relative_path: string, expected: Uint8Array) {
      const target = join(dirname(path), 'files', 'tickets', ticket_id, relative_path);
      const bytes = await wait_until(
        async () => {
          try {
            return await readFile(target);
          } catch (error) {
            if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
            throw error;
          }
        },
        (value) => value !== null,
      );
      assert.deepEqual(bytes, Buffer.from(expected));
      return target;
    },
    async stop() {
      await server.close({ deadline: performance.now() / 1000 + 30 });
      await server.shutdown_settled;
    },
  };
}
