/** Ports the two named Python readiness tests using the existing SQLite fixture. */
import { expect, test } from 'vitest';
import { fixture, create, read, edit, propose, now, advance } from './ticket_fixture.ts';
import { worker_step_blocker, is_ready_for_worker_step } from '../runtime/worker_step_readiness.ts';
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
