/** Gap: persisted Mac presence cannot authorize a VPS-local adapter for a retained
 * conversation whose workspace policy is already home_relative. No HTTP/provider runs.
 */
import { dirname, join } from 'node:path';
import { expect, test, vi } from 'vitest';
import { setup } from './conversation_core_fixture.ts';
import { create } from './ticket_fixture.ts';
import { write_ticket_conversation_start } from '../tickets/data/index.ts';
import { can_execute_locale } from '../tickets/locale_configuration.ts';
import { PROTOCOL_VERSION, SPOOL_FORMAT_VERSION } from '../execution_configuration.ts';
import { PlannerError } from '../core/contracts.ts';
import { loadConfig } from '../core/config.ts';
import { TestClock } from '../core/clock.ts';
import { ChangeSignal } from '../core/changes/index.ts';
import { ConversationLiveTail } from '../conversation/live_tail.ts';
import { SignalPublicationCoordinator } from '../conversation/publication/coordinator.ts';
import { ResolvedConversationStart } from '../conversation/contracts.ts';
import type { BackendChildFactory } from '../conversation/backends/contracts.ts';
import {
  create_backend_runtime,
  unavailable_backend_providers,
} from '../conversation/snapshot/composition.ts';
import { compose_production_backends } from '../lifecycle/production_bindings.ts';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { OneShotChildren } from '../lifecycle/one_shot_children.ts';
import type { ConversationCompositionContext } from '../core/start.ts';

test('local composition refuses a retained Mac Ticket before constructing its selected child', async () => {
  const began = performance.now();
  let bound!: ReturnType<typeof compose_production_backends>;
  const delegate: BackendChildFactory = (values) =>
    bound.factories[values.resolved_start.backend_key](values);
  const f = setup({
    core: {
      backend_child_factories: {
        claude: delegate,
        codex: delegate,
        hermes: delegate,
      },
    },
  });
  const root = dirname(f.database_path);
  const config = loadConfig(join(root, 'absent-mac-guard.yaml'), {
    PLAN_DB_PATH: f.database_path,
    PLAN_TEST_MODE: '1',
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_BACKUP_DIR: join(root, 'backups'),
  });
  const clock = new TestClock('2026-11-12T12:00:00+00:00');
  const child_processes = new ChildProcesses();
  const children = new OneShotChildren(child_processes);
  const live_tail = new ConversationLiveTail({
    view_output_directory: join(root, 'mac-guard-output'),
  });
  const http = create_backend_runtime({
    database: f.database,
    config,
    now: () => clock.now(),
    providers: unavailable_backend_providers(),
  });
  const local_constructor = vi.fn<BackendChildFactory>(() => {
    throw new Error('Synthetic local constructor boundary');
  });
  try {
    await f.start();
    const ticket = create(f.database, { locale: 'macos' });
    f.database.write((sql) => {
      sql.run(`CREATE TABLE IF NOT EXISTS mac_executor_presence (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), executor_incarnation TEXT,
        identity_json TEXT, last_contact_at_ms INTEGER, last_lease_at_ms INTEGER,
        continuous_since_ms INTEGER, going_to_sleep_at_ms INTEGER, lease_json TEXT, refusal_json TEXT)`);
      sql.run(
        `INSERT INTO mac_executor_presence
        (singleton,executor_incarnation,identity_json,last_lease_at_ms) VALUES(1,?,?,0)`,
        [
          'retained-mac',
          JSON.stringify({
            protocol_version: PROTOCOL_VERSION,
            spool_format_version: SPOOL_FORMAT_VERSION,
            application_revision: 'synthetic-mac',
            executor_incarnation: 'retained-mac',
            connection_generation: 1,
          }),
        ],
      );
      write_ticket_conversation_start(sql, ticket.id, {
        conversation_id: 'core',
        backend: 'codex',
        model: 'model',
        reasoning_effort: null,
        now: 100,
      });
    });
    expect(f.database.read((sql) => can_execute_locale(sql, 'macos'))).toBe(true);
    const retained = f.store.read_conversation('core')!.resolved_start();
    expect(retained.workspace_path_policy).toBe('home_relative');
    // There is deliberately no PLAN_TICKET_ID hint. The canonical link owns locality.
    expect(retained.role_materials!.identity_environment_variables).not.toContainEqual([
      'PLAN_TICKET_ID',
      ticket.id,
    ]);
    const context: ConversationCompositionContext = {
      database: f.database,
      config,
      clock,
      changes: new ChangeSignal(),
      record: {
        database: f.database,
        store: f.store,
        live_tail,
        sse_heartbeat_ms: 15_000,
        runtime_facts: async () => ({ running: false, permissions: [], inputs: [] }),
      },
      publication: new SignalPublicationCoordinator({
        database: f.database,
        require_runtime_ownership() {},
      }),
      require_runtime_ownership() {},
      revoke_signal() {},
      one_shot_children: children,
      child_processes,
    };
    bound = compose_production_backends(context, {
      claude: () => local_constructor,
      codex: () => local_constructor,
      hermes: () => local_constructor,
      http: () => http,
      stall_sources: () => [],
    });
    let refusal: unknown;
    try {
      await f.send();
    } catch (error) {
      refusal = error;
    }
    expect(refusal).toBeInstanceOf(PlannerError);
    if (!(refusal instanceof PlannerError)) throw refusal;
    expect(refusal.statusCode).toBe(400);
    expect(refusal.toPayload()).toEqual({
      error: {
        code: 'validation',
        message: 'Waiting for the Mac',
        detail: { ticket_id: ticket.id, locale: 'macos', machine_locale: 'vps' },
      },
    });
    expect(local_constructor).not.toHaveBeenCalled();
    expect(f.children).toHaveLength(0);
    const values = {
      resolved_start: retained,
      event_sink: f.core.make_sink(f.core.conversation_state('core')!),
      message_files: f.core.options.message_files,
    };
    const standalone = f.store
      .create_conversation(
        new ResolvedConversationStart({ ...retained, conversation_id: 'non-ticket' }),
      )
      .resolved_start();
    expect(() => bound.factories.codex({ ...values, resolved_start: standalone })).toThrow(
      'Synthetic local constructor boundary',
    );
    expect(local_constructor).toHaveBeenCalledTimes(1);
  } finally {
    await http.shutdown?.();
    await http.settled();
    children.seal();
    await children.settled();
    await child_processes.settled();
    await f.core.shutdown();
    live_tail.close();
  }
  console.info(`Temporary Mac guard component proof: ${(performance.now() - began).toFixed(3)} ms`);
});
