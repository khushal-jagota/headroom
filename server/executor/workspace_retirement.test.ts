// Risk: machine code adopts a Ticket folder that the server registered before the switch, and
// retires it while an old local process of that Ticket can still write in it. Gap: no test
// covers the server's answer to machine code's retirement request. Reuses ticket_fixture and
// the registry and lease of worker_setup.test.ts; no provider process starts.
import { expect, test } from 'vitest';
import { TestClock } from '../core/clock.ts';
import { ChangeSignal } from '../core/changes/index.ts';
import { PROTOCOL_VERSION, SPOOL_FORMAT_VERSION } from '../execution_configuration.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import { read_workspace_states_for_tickets } from '../ticket_workspaces/data.ts';
import { create, fixture, fixture_path, now } from '../tests/ticket_fixture.ts';
import { VpsExecutorRegistry } from './registry.ts';

test('machine code retires an adopted workspace only after its old process record is retired', async () => {
  const database = fixture();
  const registry = new VpsExecutorRegistry(
    database,
    new TestClock(new Date(now * 1000).toISOString()),
    {
      db_path: fixture_path(database),
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
  try {
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
    const workspace = {
      machine_id: '22222222222242228222222222222222',
      workspace_id: '33333333333343338333333333333333',
    };
    database.write((tx) => {
      tx.run('UPDATE ticket_workspace_lifecycle SET ended_at=0 WHERE ticket_id=?', [ticket.id]);
      // The server ran this conversation before the switch, and machine code resumed it.
      tx.run(
        "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,created_at) VALUES('resumed','codex','/','full',0)",
      );
      tx.run(
        "INSERT INTO conversation_child_custody(conversation_id,generation,state) VALUES('resumed','supervised-before-switch','started')",
      );
      tx.run('INSERT INTO ticket_workspace_conversations VALUES(?,?,?,?)', [
        'resumed',
        ticket.id,
        workspace.machine_id,
        workspace.workspace_id,
      ]);
      tx.run(
        "INSERT INTO executor_child_assignments(executor_incarnation,child_id,conversation_id,principal_kind,principal_id,created_at_ms) VALUES(?,'child','resumed','ticket',?,0)",
        [identity.executor_incarnation, ticket.id],
      );
    });
    const state = database.read((sql) => read_workspace_states_for_tickets(sql, [ticket.id]))[0]!;
    expect(await endpoint.retire_workspace(identity, state, workspace)).toEqual({
      authorized: false,
    });
    database.write((tx) =>
      tx.run(
        "UPDATE conversation_child_custody SET state='retired' WHERE conversation_id='resumed'",
      ),
    );
    expect(await endpoint.retire_workspace(identity, state, workspace)).toEqual({
      authorized: true,
    });
  } finally {
    await registry.stop({ deadline: performance.now() / 1000 + 2 });
  }
});
