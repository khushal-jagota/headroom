// Risk: a Ticket whose Worker is setting up reads idle, so a supervisor restarts a healthy
// start (19 November). Gap: no test proves that setup opens, moves and closes on Home and on
// another machine, that every surface reads it, or that restart-worker refuses it.
// Reuses ticket_fixture, the bare-remote pattern from project_repository.test.ts and its
// standalone MacRuns start; no provider process starts.
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
import { text_message_content } from '../conversation/message_content.ts';
import {
  configure_conversation_start,
  send_to_ticket_conversation,
  start_ticket_conversation,
  worker_resolve,
} from '../runtime/conversation_start.ts';
import { createProject } from '../projects/data/index.ts';
import { git } from '../ticket_workspaces/filesystem/shared.ts';
import { PromptDeliveryStarted, type ConversationSystem } from '../conversation/contracts.ts';
import { OWNER, PlannerError } from '../core/contracts.ts';
import type { Database } from '../core/database/contracts.ts';
import { TestClock } from '../core/clock.ts';
import { ChangeSignal } from '../core/changes/index.ts';
import { VpsExecutorRegistry } from '../executor/registry.ts';
import { PROTOCOL_VERSION, SPOOL_FORMAT_VERSION } from '../execution_configuration.ts';
import { HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import { require_restartable } from '../tickets/worker_restart.ts';
import {
  close_home_worker_setups,
  WORKER_SETUP_RESTART_REFUSAL_SECONDS,
} from '../ticket_workspaces/worker_setup.ts';
import { add_work_attention } from '../work_attention.ts';
import { SignalPublicationCoordinator } from '../conversation/publication/coordinator.ts';
import { createSignalNavigationRuntime } from '../navigation/index.ts';
import { create, fixture, fixture_path, now } from './ticket_fixture.ts';

/** The Ticket fixture with the real producer and navigation commit steps, as in navigation_totals. */
function navigated_fixture(): Database {
  let producer: SignalPublicationCoordinator | undefined;
  let navigation: ReturnType<typeof createSignalNavigationRuntime> | undefined;
  const database = fixture({
    commitSteps: [
      (tx) => producer?.finalize_signal(tx),
      (tx) => navigation?.navigationCommitStep(tx),
    ],
  });
  database.write((tx) => {
    tx.run("INSERT INTO navigation_meta VALUES(1,'source',7,'view-epoch',0)");
    tx.run('INSERT INTO chief_page(singleton) VALUES(1)');
  });
  producer = new SignalPublicationCoordinator({ database, require_runtime_ownership: () => {} });
  producer.bind_runtime(() => null);
  producer.initialize();
  navigation = createSignalNavigationRuntime(producer);
  navigation.attach();
  return database;
}

const setup_row = (database: Database, ticket_id: string) =>
  database.read((sql) =>
    sql.get<Record<string, unknown>>('SELECT * FROM ticket_worker_setups WHERE ticket_id=?', [
      ticket_id,
    ]),
  ) ?? null;
const navigation_setup = (database: Database, ticket_id: string) =>
  database.read((sql) => {
    const row = sql.get<{ value: string }>(
      "SELECT value FROM navigation_records WHERE kind='ticket' AND id=?",
      [ticket_id],
    );
    return (JSON.parse(row!.value) as { worker_setup?: unknown }).worker_setup;
  });
async function attention(database: Database, ticket_id: string) {
  const rows: Record<string, unknown>[] = [{ id: ticket_id }];
  await add_work_attention(database, undefined, rows);
  return { agent_state: rows[0]!.agent_state, worker_setup: rows[0]!.worker_setup };
}
// Restart Worker reads the wall clock: one minute after the fixture's setups began.
afterEach(() => vi.restoreAllMocks());
const one_minute_into_setup = () => vi.spyOn(Date, 'now').mockReturnValue((now + 60) * 1000);
function restart_refusal(database: Database, ticket_id: string): string | null {
  try {
    database.read((sql) => require_restartable(sql, OWNER, ticket_id));
    return null;
  } catch (error) {
    if (!(error instanceof PlannerError)) throw error;
    return error.message;
  }
}
async function bare_remote(root: string): Promise<string> {
  const remote = join(root, 'origin.git'),
    source = join(root, 'source');
  mkdirSync(remote, { recursive: true });
  mkdirSync(source, { recursive: true });
  for (const [cwd, args] of [
    [remote, ['init', '--bare', '-b', 'main']],
    [source, ['init', '-b', 'main']],
    [
      source,
      [
        '-c',
        'user.email=p@example.test',
        '-c',
        'user.name=p',
        'commit',
        '--allow-empty',
        '-m',
        'init',
      ],
    ],
    [source, ['push', remote, 'main']],
  ] as const) {
    const result = await git(cwd, [...args]);
    expect(result.returncode, result.stderr).toBe(0);
  }
  return remote;
}

test('Home declares its setup, every surface reads it, restart refuses it, and every end closes it', async () => {
  const database = navigated_fixture();
  const db_path = fixture_path(database);
  const root = join(dirname(db_path), 'setup-proof');
  configure_conversation_start(database, { db_path, workspace_root: root });
  const remote = await bare_remote(root);
  const project = database.write((tx) =>
    createProject(
      tx,
      { name: 'Setup', summary: '', repository_url: remote, default_branch: 'main' },
      now,
    ),
  );
  const ticket = create(database, { project_id: project.id });
  one_minute_into_setup();
  let seen_while_starting: unknown = null;
  let seen_while_sending: {
    navigation: unknown;
    attention: unknown;
    refusal: string | null;
  } | null = null;
  const system = {
    async start_conversation() {
      // The workspace is ready and the Worker launch is next.
      seen_while_starting = setup_row(database, ticket.id);
    },
    async send() {
      // The first prompt is on its way: setup is still open, and every surface reads it.
      seen_while_sending = {
        navigation: navigation_setup(database, ticket.id),
        attention: await attention(database, ticket.id),
        refusal: restart_refusal(database, ticket.id),
      };
      return new PromptDeliveryStarted();
    },
    async kill() {},
  } as unknown as ConversationSystem;

  await send_to_ticket_conversation(system, database, ticket.id, text_message_content('opener'), {
    conversation_id: null,
    created_conversation_id: 'conv_home',
    sender_label: 'test',
    now,
  });
  expect(seen_while_starting).toMatchObject({
    conversation_id: 'conv_home',
    machine_id: HOME_MACHINE_ID,
    step: 'starting_worker',
    began_at: now,
  });
  const open = { step: 'starting_worker', began_at: now, machine_id: HOME_MACHINE_ID };
  expect(seen_while_sending).toEqual({
    navigation: open,
    attention: { agent_state: 'setting_up', worker_setup: open },
    refusal: 'Still setting up since 2026-07-04 12:00:00 UTC (starting Worker)',
  });
  // The first prompt landed, so the send path closed Home's setup.
  expect(setup_row(database, ticket.id)).toBeNull();
  expect(navigation_setup(database, ticket.id)).toBeNull();
  expect((await attention(database, ticket.id)).agent_state).toBe('idle');
  expect(restart_refusal(database, ticket.id) ?? '').not.toMatch(/Still setting up/);

  // A failed workspace preparation closes the setup with the failure.
  const broken = database.write((tx) =>
    createProject(
      tx,
      {
        name: 'Broken',
        summary: '',
        repository_url: join(root, 'missing.git'),
        default_branch: 'main',
      },
      now,
    ),
  );
  const failing = create(database, { project_id: broken.id });
  await expect(
    start_ticket_conversation(system, database, failing, worker_resolve(database, failing), {
      conversation_id: 'conv_failing',
      now,
    }),
  ).rejects.toThrow('Cannot prepare the Ticket workspace');
  expect(setup_row(database, failing.id)).toBeNull();

  // A server restart ends every Home setup, because it ran inside the old process.
  await start_ticket_conversation(system, database, ticket, worker_resolve(database, ticket), {
    conversation_id: 'conv_interrupted',
    now,
  });
  expect(setup_row(database, ticket.id)).not.toBeNull();
  database.write(close_home_worker_setups);
  expect(setup_row(database, ticket.id)).toBeNull();
});

test('Restart Worker refuses a setup only within its bound, so a stuck setup cannot block it', async () => {
  const database = navigated_fixture();
  const ticket = create(database);
  database.write((tx) =>
    tx.run("INSERT INTO ticket_worker_setups VALUES(?,'conv_stuck',?,'preparing_workspace',?)", [
      ticket.id,
      ORIGINAL_MAC_MACHINE_ID,
      now,
    ]),
  );
  vi.spyOn(Date, 'now').mockReturnValue((now + WORKER_SETUP_RESTART_REFUSAL_SECONDS - 1) * 1000);
  expect(restart_refusal(database, ticket.id)).toMatch(/^Still setting up since/);
  vi.spyOn(Date, 'now').mockReturnValue((now + WORKER_SETUP_RESTART_REFUSAL_SECONDS) * 1000);
  expect(restart_refusal(database, ticket.id) ?? '').not.toMatch(/Still setting up/);
});

test('Another machine declares its setup while its start is open, and every settlement closes it', async () => {
  const database = navigated_fixture();
  const db_path = fixture_path(database);
  configure_conversation_start(database, { db_path, workspace_root: dirname(db_path) });
  const registry = new VpsExecutorRegistry(
    database,
    new TestClock(new Date(now * 1000).toISOString()),
    {
      db_path,
      changes: new ChangeSignal(),
      media_signal: new AbortController().signal,
      original_enabled: true,
    },
  );
  database.read((sql) => registry.load(sql));
  const endpoint = registry.get(ORIGINAL_MAC_MACHINE_ID)!;
  const identity = {
    protocol_version: PROTOCOL_VERSION,
    application_revision: 'synthetic',
    spool_format_version: SPOOL_FORMAT_VERSION,
    executor_incarnation: '11111111-1111-4111-8111-111111111111',
    connection_generation: 1,
  };
  await endpoint.presence.accept_lease({
    identity,
    process_id: 123,
    mac_wall_clock_ms: Date.now(),
    queue_depth: 0,
    oldest_item_age_ms: null,
    waiting_conversations: [],
    waiting_conversations_truncated: false,
    working_turns: [],
    admission_stopped: false,
    cleanup_uncertainties: [],
    driver_kinds: ['codex'],
  });
  const ticket = create(database, { locale: 'macos', machine_id: ORIGINAL_MAC_MACHINE_ID });
  one_minute_into_setup();
  try {
    // One start per settlement: finished, withdrawn and expired each close the setup.
    for (const [index, settled] of (['finished', 'withdrawn', 'expired'] as const).entries()) {
      const conversation_id = `conv_remote_${index}`;
      const values = worker_resolve(database, ticket);
      const start = {
        conversation_id,
        backend_key: values.backend_key,
        model: values.model,
        reasoning_effort: values.reasoning_effort,
        role_materials: values.role_materials,
        workspace_folder: values.workspace_folder,
        workspace_path_policy: values.workspace_path_policy,
        access: values.access,
      };
      database.write((tx) => {
        tx.run(
          "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,workspace_path_policy,access,created_at) VALUES(?,'codex','model','/','executor_default','full',?)",
          [conversation_id, now],
        );
        tx.run('UPDATE tickets SET conversation_id=? WHERE id=?', [conversation_id, ticket.id]);
      });
      const child = endpoint.runs.create({
        resolved_start: start as never,
        event_sink: { async report() {} },
        message_files: null as never,
      });
      await child.start(start as never, { vendor_session_cursor: null });
      const reference = database.read((sql) => {
        const row = sql.get<{ command_json: string }>(
          "SELECT command_json FROM executor_commands WHERE conversation_id=? AND operation='start'",
          [conversation_id],
        )!;
        return (JSON.parse(row.command_json) as { child_command: { child: never } }).child_command
          .child;
      });

      await endpoint.report_worker_setup(identity, {
        child: reference,
        step: 'preparing_workspace',
      });
      expect(setup_row(database, ticket.id)).toMatchObject({
        conversation_id,
        machine_id: ORIGINAL_MAC_MACHINE_ID,
        step: 'preparing_workspace',
      });
      expect((await attention(database, ticket.id)).agent_state).toBe('setting_up');
      expect(restart_refusal(database, ticket.id)).toMatch(
        /^Still setting up since .* UTC \(preparing workspace\)$/,
      );
      await endpoint.report_worker_setup(identity, { child: reference, step: 'starting_worker' });
      expect(navigation_setup(database, ticket.id)).toMatchObject({ step: 'starting_worker' });

      database.write((tx) =>
        tx.run(
          "UPDATE executor_commands SET state=? WHERE conversation_id=? AND operation='start'",
          [settled, conversation_id],
        ),
      );
      expect(setup_row(database, ticket.id), settled).toBeNull();
      expect(navigation_setup(database, ticket.id)).toBeNull();
      // A report that arrives after the start settled declares nothing.
      await endpoint.report_worker_setup(identity, { child: reference, step: 'starting_worker' });
      expect(setup_row(database, ticket.id)).toBeNull();
    }
  } finally {
    await registry.stop({ deadline: performance.now() / 1000 + 2 });
  }
});
