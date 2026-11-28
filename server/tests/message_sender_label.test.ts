/** Risk: an agent message reaches another agent with a label that does not say who wrote or where
 * to reply. The authority test proves admission only, and the one label test covers Unidentified.
 * Reuse the Ticket fixture's real schema. No server, listener, or backend process.
 */
import { expect, test } from 'vitest';
import { create, fixture } from './ticket_fixture.ts';
import { create_manager } from '../managers/data.ts';
import { sender_label } from '../message_delivery/service.ts';
import { admitMessage } from '../core/authority/intake.ts';
import type { Principal } from '../core/contracts.ts';

test('a Worker and a Manager label themselves by title and Manager, and may message each other', () => {
  const db = fixture();
  db.write((tx) => {
    tx.run("INSERT INTO projects(id,name,created_at,updated_at) VALUES('project','Project',0,0)");
    tx.run("INSERT OR REPLACE INTO manager_launch_defaults VALUES(1,'codex','gpt-6.1-sol',NULL)");
  });
  const manager = db.write((tx) =>
    create_manager(tx, {
      title: 'Growth',
      project_id: 'project',
      clock: { nowUnix: () => 0, now: () => new Date(0) } as never,
    }),
  );
  const under_manager = create(db, { title: 'Fix login', manager_id: manager.id });
  const under_chief = create(db, { title: 'Plan roadmap', manager_kind: 'chief' });
  const label = (principal: Principal) =>
    db.read((sql) => sender_label({ principal, conversationId: null }, sql));
  expect(label({ kind: 'ticket', id: under_manager.id })).toBe(
    `Worker on "Fix login" (ticket ${under_manager.id}), under Manager "Growth" (manager ${manager.id})`,
  );
  expect(label({ kind: 'ticket', id: under_chief.id })).toBe(
    `Worker on "Plan roadmap" (ticket ${under_chief.id}), under Chief`,
  );
  expect(label({ kind: 'manager', id: manager.id })).toBe(
    `Manager "Growth" (manager ${manager.id})`,
  );
  // Ticket to Ticket and Manager to a Ticket outside its chain were refused before.
  db.read((sql) => {
    admitMessage(
      sql,
      { kind: 'ticket', id: under_manager.id },
      { kind: 'ticket', id: under_chief.id },
    );
    admitMessage(sql, { kind: 'manager', id: manager.id }, { kind: 'ticket', id: under_chief.id });
    admitMessage(sql, { kind: 'ticket', id: under_chief.id }, { kind: 'manager', id: manager.id });
  });
});
