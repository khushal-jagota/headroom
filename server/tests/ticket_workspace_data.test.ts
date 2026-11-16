/** Gap: old scratch tests prove Git safety, not durable end clocks or exact grants.
 * Reuse the populated Ticket database fixture. No provider, browser, or new host.
 */
import { expect, test } from 'vitest';
import { fixture, create, now } from './ticket_fixture.ts';
import {
  authorize_retirement,
  ensure_workspace_activity,
  has_committed_retirement,
  read_workspace_states,
} from '../ticket_workspaces/data.ts';
import { RETENTION_SECONDS } from '../ticket_workspaces/contracts.ts';

test('retirement grants bind the observed end clock and physical workspace, and active claims fence them', () => {
  const db = fixture();
  const ticket = create(db);
  const identity = { machine_id: 'test-machine', workspace_id: 'original' };
  db.write((sql) =>
    sql.run('UPDATE ticket_workspace_lifecycle SET ended_at=? WHERE ticket_id=?', [now, ticket.id]),
  );
  const expected = db.read(read_workspace_states).get(ticket.id)!;
  expect(db.write((sql) => authorize_retirement(sql, expected, now, identity))).toBe(true);
  db.write((sql) => ensure_workspace_activity(sql, ticket.id, now + 10));
  expect(
    db.write((sql) => authorize_retirement(sql, expected, now + RETENTION_SECONDS + 20, identity)),
  ).toBe(false);
  const fresh = db.read(read_workspace_states).get(ticket.id)!;
  db.write((sql) => sql.run("UPDATE tickets SET worker_step_claim='out' WHERE id=?", [ticket.id]));
  expect(
    db.write((sql) => authorize_retirement(sql, fresh, now + RETENTION_SECONDS + 20, identity)),
  ).toBe(false);
  db.write((sql) => sql.run("UPDATE tickets SET worker_step_claim='none' WHERE id=?", [ticket.id]));
  expect(
    db.write((sql) => authorize_retirement(sql, fresh, now + RETENTION_SECONDS + 20, identity)),
  ).toBe(true);
  expect(db.read((sql) => has_committed_retirement(sql, fresh, identity))).toBe(true);
  expect(
    db.read((sql) =>
      has_committed_retirement(sql, fresh, { ...identity, workspace_id: 'replacement' }),
    ),
  ).toBe(false);
  db.write((sql) =>
    sql.run('UPDATE ticket_workspace_lifecycle SET deleted=1 WHERE ticket_id=?', [ticket.id]),
  );
  db.write((sql) => ensure_workspace_activity(sql, ticket.id, now + 30));
  expect(db.read(read_workspace_states).get(ticket.id)?.ended_at).toBe(now + 10);
});
