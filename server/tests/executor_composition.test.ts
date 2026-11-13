/** A real startup proves ordering that a pure retained-map test cannot: the executor
 * must supply open turns before record reconciliation. Reuse the Ticket database
 * fixture and actual server lifecycle, with no peer or provider launch.
 */
import { dirname, join } from 'node:path';
import { readFileSync } from 'node:fs';
import { expect, test, vi } from 'vitest';
import { fixture, fixture_path, create } from './ticket_fixture.ts';
import { startServer } from '../core/start.ts';
import type { ConversationCompositionContext, StartedServer } from '../core/start.ts';
import { ConversationStore } from '../conversation/record/data/store.ts';
import { ConversationRoleMaterials, ResolvedConversationStart } from '../conversation/contracts.ts';
import { SqliteProcessConversationSystem } from '../conversation/system.ts';
import type { BackendChildFactory } from '../conversation/backends/contracts.ts';
import { VpsExecutor } from '../executor/vps_transport.ts';
import { MacRemoteChild } from '../executor/vps_remote_child.ts';
import { ExecutorStore } from '../executor/data/runs.ts';
import { compose_production_backends } from '../lifecycle/production_bindings.ts';
import {
  create_backend_runtime,
  unavailable_backend_providers,
} from '../conversation/snapshot/composition.ts';
import { write_ticket_conversation_start } from '../tickets/data/index.ts';
import { PlannerError } from '../core/contracts.ts';
import { create_production_conversation_core } from '../lifecycle/production.ts';

const incarnation = '11111111-1111-4111-8111-111111111111';
test('configured restart adopts before reconciliation and Mac locality never selects a local child', async () => {
  const began = performance.now();
  const database = fixture(),
    path = fixture_path(database),
    root = dirname(path);
  const schema = JSON.parse(
    readFileSync(new URL('../core/database/application_schema.json', import.meta.url), 'utf8'),
  ) as { statements: string[] };
  database.write((tx) => {
    // Reuse the staged lifecycle host's prepared navigation frontier.
    tx.run("INSERT INTO navigation_meta VALUES(1,'source',7,'old-view',0)");
    tx.run('INSERT INTO chief_page(singleton) VALUES(1)');
    tx.run("INSERT INTO conversation_runtime_epoch VALUES(1,'old-runtime')");
    for (const name of [
      'mac_executor_presence',
      'mac_runs',
      'mac_inbox_positions',
      'executor_commands',
      'executor_incarnations',
      'executor_child_assignments',
    ]) {
      if (tx.get('SELECT 1 FROM sqlite_master WHERE name=?', [name])) continue;
      const statement = schema.statements.find((sql) => sql.startsWith(`CREATE TABLE ${name} (`));
      if (statement === undefined) throw new Error(`Missing schema fixture table ${name}`);
      tx.run(statement);
    }
  });
  const ticket = create(database, { locale: 'macos' });
  const initial = new ConversationStore(database, { integer_now: () => 100 });
  const materials = (id: string) =>
    new ConversationRoleMaterials({
      role_text: 'Synthetic Worker',
      identity_environment_variables: [
        ['PLAN_ACTOR', 'worker'],
        ['PLAN_TICKET_ID', id],
      ],
    });
  const start = new ResolvedConversationStart({
    conversation_id: 'kept',
    backend_key: 'codex',
    model: 'model',
    reasoning_effort: null,
    role_materials: materials(ticket.id),
    workspace_folder: '/tmp',
    access: 'full',
    workspace_path_policy: 'home_relative',
  });
  initial.create_conversation(start);
  initial.transition_lifecycle('kept', [
    {
      kind: 'turn',
      identity: '1',
      turn_number: 1,
      operation: 'turn',
      status: 'running',
      provider_identity: null,
    },
  ]);
  database.write((tx) => {
    write_ticket_conversation_start(tx, ticket.id, {
      conversation_id: 'kept',
      backend: 'codex',
      model: 'model',
      reasoning_effort: null,
      now: 100,
    });
    // A compatible-away Mac retains its run. Age does not authorize local fallback.
    tx.run(
      'INSERT INTO mac_executor_presence(singleton,executor_incarnation,identity_json,last_lease_at_ms) VALUES(1,?,?,?)',
      [
        incarnation,
        JSON.stringify({
          protocol_version: 22,
          application_revision: 'synthetic',
          spool_format_version: 5,
          executor_incarnation: incarnation,
          connection_generation: 0,
        }),
        Date.now() - 120000,
      ],
    );
  });
  new ExecutorStore(database).register_child(
    { executor_incarnation: incarnation, child_id: 'retained', conversation_id: 'kept' },
    'ticket',
    ticket.id,
    Date.now(),
  );
  database.close();
  for (const [key, value] of Object.entries({
    PLAN_DB_PATH: path,
    PLAN_PORT: '0',
    PLAN_TEST_MODE: '1',
    PLAN_EXECUTOR_ID: 'synthetic-executor',
    PLAN_DISPATCH_ENABLED: 'false',
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_BACKUP_DIR: join(root, 'backups'),
    PLAN_DISPATCHER_LOCK_PATH: join(root, 'dispatcher.lock'),
  }))
    vi.stubEnv(key, value);
  const local = vi.fn<BackendChildFactory>(() => {
    throw new Error('local constructor selected');
  });
  let core!: SqliteProcessConversationSystem, endpoint!: VpsExecutor;
  let context!: ConversationCompositionContext,
    bound!: ReturnType<typeof compose_production_backends>;
  let server: StartedServer | undefined;
  try {
    server = await startServer({
      createExecutor(current) {
        endpoint = new VpsExecutor(current.database, current.clock, {
          db_path: path,
          changes: current.changes,
          media_signal: current.media_signal,
        });
        return endpoint;
      },
      createConversationCore(current) {
        context = current;
        const http = create_backend_runtime({
          database: current.database,
          config: current.config,
          now: () => current.clock.now(),
          providers: unavailable_backend_providers(),
        });
        bound = compose_production_backends(current, {
          claude: () => local,
          codex: () => local,
          hermes: () => local,
          http: () => http,
          stall_sources: () => [],
        });
        core = create_production_conversation_core(current, bound);
        return core;
      },
      createBackendServices: () => bound.http,
    });
    expect(core.runtime_turn('kept')?.token.turn_number).toBe(1);
    expect(
      context.record.store.lifecycle_facts('kept').find((fact) => fact.kind === 'turn')?.status,
    ).toBe('running');
    expect(core.conversation_state('kept')?.child).toBeInstanceOf(MacRemoteChild);
    expect(endpoint.presence.is_present()).toBe(false);
    expect(local).not.toHaveBeenCalled();
    expect(bound.http.machine_catalog()).toBe(endpoint.catalog);
    expect(bound.stall_sources()).toHaveLength(3);
    const away_ticket = create(context.database, { locale: 'macos' });
    const away = new ResolvedConversationStart({
      ...start,
      conversation_id: 'away',
      role_materials: materials(away_ticket.id),
    });
    context.record.store.create_conversation(away);
    context.database.write((tx) =>
      write_ticket_conversation_start(tx, away_ticket.id, {
        conversation_id: 'away',
        backend: 'codex',
        model: 'model',
        reasoning_effort: null,
        now: 100,
      }),
    );
    expect(
      bound.factories.codex({
        resolved_start: away,
        event_sink: { async report() {} },
        message_files: endpoint.message_files,
      }),
    ).toBeInstanceOf(MacRemoteChild);
    expect(local).not.toHaveBeenCalled();
    const unconfigured = compose_production_backends(
      { ...context, config: { ...context.config, executor_id: null }, executor: null },
      {
        claude: () => local,
        codex: () => local,
        hermes: () => local,
        http: () => bound.http,
        stall_sources: () => [],
      },
    );
    expect(unconfigured.execution_refusal('kept')).toBeInstanceOf(PlannerError);
    expect(() =>
      unconfigured.factories.codex({
        resolved_start: start,
        event_sink: { async report() {} },
        message_files: endpoint.message_files,
      }),
    ).toThrow('Execution admission invariant violated');
    expect(local).not.toHaveBeenCalled();
  } finally {
    if (server) {
      await server.close({ deadline: performance.now() / 1000 + 5 });
      await server.shutdown_settled;
    }
    vi.unstubAllEnvs();
  }
  console.info(`Executor startup/locality component: ${(performance.now() - began).toFixed(3)} ms`);
});
