/** Ports the two named Python readiness tests using the existing SQLite fixture. */
import { expect, test, vi, afterEach } from 'vitest';
import { fixture, create, read, edit, propose, now, advance } from './ticket_fixture.ts';
import * as executor_clock from '../executor/clock.ts';
import { PROTOCOL_VERSION, SPOOL_FORMAT_VERSION } from '../execution_configuration.ts';
import { MAC_AWAY_AFTER_SECONDS } from '../executor/configuration.ts';
import { worker_step_blocker, is_ready_for_worker_step } from '../runtime/worker_step_readiness.ts';
afterEach(() => vi.restoreAllMocks());
function ready(db: ReturnType<typeof fixture>, id: string): boolean {
  const ticket = read(db, id);
  return db.read((sql) =>
    is_ready_for_worker_step(sql, ticket, { worker_type_definition: ticket.workflow }),
  );
}
test('user_owned_ticket_is_ready_once_per_stage_entry', () => {
  const db = fixture();
  const ticket = create(db, { worker_type: 'new_worker' });
  expect(ready(db, ticket.id)).toBe(true);
  db.write((tx) =>
    tx.run('INSERT INTO ticket_paired_stage_openers(ticket_id,stage,opened_at) VALUES (?,?,?)', [
      ticket.id,
      ticket.stage,
      now,
    ]),
  );
  expect(ready(db, ticket.id)).toBe(false);
  const moved = advance(db, ticket, 'needs_stages');
  expect(
    db.read((sql) =>
      sql.get('SELECT 1 FROM ticket_paired_stage_openers WHERE ticket_id=?', [ticket.id]),
    ),
  ).toBeUndefined();
  expect(ready(db, moved.id)).toBe(true);
});
test.each([
  ['coding', 'needs_what_changes'],
  ['new_worker', 'needs_what_good_looks_like_at_each_stage'],
  ['new_worker', 'needs_stages'],
])('a_ticket_at_its_ceiling_is_still_started_to_propose: %s %s', (worker_type, ceiling) => {
  const db = fixture();
  let ticket = edit(db, create(db, { worker_type }), { ceiling });
  if (worker_type === 'new_worker') {
    ticket = propose(db, ticket, 'purpose_and_boundaries');
    expect(ticket.pending_proposal).toBeNull();
    expect(ticket.stage).toBe('needs_stages');
  }
  expect(ready(db, ticket.id)).toBe(true);
});

// Gap: the shared HTTP host cannot mint Mac presence or fieldless nonterminal stages.
// Read the real rule with a synthetic presence record and a fixed wall clock.
test('every readiness refusal preserves its first-refusal order, including Mac wall-time expiry', () => {
  const db = fixture();
  const ticket = create(db);
  const blocker = (value: typeof ticket, presence: unknown = undefined) =>
    db.read((sql) =>
      worker_step_blocker(
        {
          all: (query, args) => sql.all(query, args),
          get: <Row>(
            query: string,
            args?: import('../core/database/contracts.ts').SqlParameters,
          ): Row | undefined =>
            query.includes('FROM mac_executor_presence')
              ? (presence as Row | undefined)
              : sql.get<Row>(query, args),
        },
        value,
        { worker_type_definition: value.workflow },
      ),
    );
  expect(blocker({ ...ticket, is_backlogged: true, locale: 'macos' })).toBe(
    'the Ticket is backlogged',
  );
  expect(blocker({ ...ticket, locale: 'macos' })).toBe(
    'Mac Tickets cannot start: no compatible Mac executor has leased yet',
  );
  const wall = 1800000000000;
  vi.spyOn(executor_clock, 'now_ms').mockReturnValue(wall);
  const presence = {
    executor_incarnation: 'synthetic',
    identity_json: JSON.stringify({
      protocol_version: PROTOCOL_VERSION,
      spool_format_version: SPOOL_FORMAT_VERSION,
      application_revision: 'synthetic',
      executor_incarnation: 'synthetic',
      connection_generation: 0,
    }),
    last_contact_at_ms: wall,
    last_lease_at_ms: wall,
    continuous_since_ms: wall,
    going_to_sleep_at_ms: null,
    refusal_json: null,
  };
  expect(blocker({ ...ticket, locale: 'macos' }, presence)).toBeNull();
  expect(
    blocker(
      { ...ticket, locale: 'macos' },
      { ...presence, last_lease_at_ms: wall - MAC_AWAY_AFTER_SECONDS * 1000 },
    ),
  ).toBe('Waiting for the Mac');
  expect(blocker({ ...ticket, locale: 'macos' }, { ...presence, going_to_sleep_at_ms: wall })).toBe(
    'Waiting for the Mac',
  );
  expect(blocker({ ...ticket, stage: 'done' })).toBe('the Stage done is terminal');
  expect(blocker({ ...ticket, ticket_status: 'agent' })).toBe(
    'the Ticket is at agent, so no worker step is due',
  );
  const fieldless = {
    ...ticket,
    workflow: {
      ...ticket.workflow,
      stages: ticket.workflow.stages.map((stage) =>
        stage.id === ticket.stage ? { ...stage, gating_field: null } : stage,
      ),
    },
  };
  expect(blocker(fieldless)).toBe(`the Stage ${ticket.stage} has no field for a worker to fill`);
  db.write((tx) =>
    tx.run('INSERT INTO backend_model_enablement VALUES (?,?,0)', [
      ticket.employee_backend!,
      ticket.employee_launch_model!,
    ]),
  );
  expect(blocker(ticket)).toBe(`the model ${ticket.employee_launch_model} is disabled`);
});
