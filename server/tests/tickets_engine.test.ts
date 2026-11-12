// Ports the 12 existing Python rule tests. Real SQLite through the shared write door
// protects stage/claim ordering and atomic row updates that pure decision tests omit.
import { expect, test, vi, afterEach } from 'vitest';
import { OWNER } from '../core/contracts.ts';
import { admitCommand } from '../core/authority/intake.ts';
import * as data from '../tickets/data/index.ts';
import * as blocks from '../core/ticket_blocks.ts';
import {
  fixture,
  create,
  edit,
  propose,
  accept,
  advance,
  read,
  row,
  claim,
  now,
  worker,
} from './ticket_fixture.ts';
afterEach(() => vi.restoreAllMocks());
test('accept_kickoff_field_advances_to_what_changes_and_leaves_title_independent', () => {
  const db = fixture();
  let t = create(db, { title: 'Proposed title', brief: 'proposed note', settle_kickoff: false });
  t = edit(db, t, { title: 'Approved title' });
  expect(t.stage).toBe('needs_brief');
  expect(t.pending_proposal).not.toBeNull();
  t = accept(db, t, 'brief', 'none', 'approved note');
  expect(t).toMatchObject({
    stage: 'needs_what_changes',
    ticket_status: 'empty',
    title: 'Approved title',
    pending_proposal: null,
    field_values: { brief: 'approved note' },
  });
});
test('a02_gating_chain_one_state_per_accept', () => {
  const db = fixture();
  let t = edit(db, create(db), { ceiling: 'needs_consequences' });
  t = propose(db, t, 'changes body');
  expect(t.stage).toBe('needs_implementation');
  expect(t.field_values.what_changes).toBe('changes body');
  expect(t.pending_proposal).toBeNull();
  t = propose(db, t, 'implementation body');
  expect(t.stage).toBe('needs_consequences');
  expect(t.field_values.implementation).toBe('implementation body');
  expect(t.pending_proposal).toBeNull();
});
test('a03_ceiling_auto_accept_until_cap_then_pending', () => {
  const db = fixture();
  let t = edit(db, create(db), { ceiling: 'needs_implementation' });
  t = propose(db, t, 'changes body');
  expect(t.stage).toBe('needs_implementation');
  t = propose(db, t, 'implementation body');
  expect(t.stage).toBe('needs_implementation');
  expect(t.field_values.implementation).toBeUndefined();
  expect(t.pending_proposal).toMatchObject({ body: 'implementation body', proposed_by: 'worker' });
  expect(t.ticket_status).toBe('awaiting_approval');
});
test('a06_edit_accept_stores_edited_text', () => {
  const db = fixture();
  let t = propose(db, create(db), 'draft body');
  t = accept(db, t, 'what_changes', 'none', 'edited body exactly');
  expect(t.field_values.what_changes).toBe('edited body exactly');
  expect(t.pending_proposal).toBeNull();
  expect(t.stage).toBe('needs_implementation');
  expect(t.ceiling).toBe('needs_implementation');
});
test('a07_closeout_routing', () => {
  const db = fixture();
  let t1 = edit(db, create(db), { ceiling: 'needs_consequences' });
  for (const body of ['a', 'i']) t1 = propose(db, t1, body);
  expect(t1.stage).toBe('needs_consequences');
  t1 = propose(db, t1, 'c');
  expect(t1.stage).toBe('needs_consequences');
  expect(t1.pending_proposal).not.toBeNull();
  expect(t1.field_values.consequences).toBeUndefined();
  t1 = accept(db, t1, 'consequences');
  expect(t1.stage).toBe('done');
  expect(t1.field_values.consequences).toBe('c');
  expect(t1.ceiling).toBe('done');
  let t2 = edit(db, create(db), { ceiling: 'done' });
  for (const body of ['a', 'i', 'c']) t2 = propose(db, t2, body);
  expect(t2.stage).toBe('done');
  expect(t2.field_values).toMatchObject({
    what_changes: 'a',
    implementation: 'i',
    consequences: 'c',
  });
  let t3 = edit(db, create(db), { ceiling: 'needs_implementation' });
  t3 = propose(db, t3, 'a');
  expect(t3.stage).toBe('needs_implementation');
  t3 = propose(db, t3, 'i');
  expect(t3.stage).toBe('needs_implementation');
  expect(t3.pending_proposal).not.toBeNull();
  t3 = accept(db, t3, 'implementation', 'done');
  expect(t3.stage).toBe('needs_consequences');
});
test('a36_onward_scope', () => {
  const db = fixture();
  let t = propose(db, create(db), 'body');
  expect(t.pending_proposal).not.toBeNull();
  const before = row(db, t.id);
  for (const [ceiling, code] of [
    [null, 'scope_missing'],
    ['needs_what_changes', 'scope_invalid'],
    ['needs_nothing', 'scope_invalid'],
  ] as const) {
    expect(() => accept(db, t, 'what_changes', ceiling)).toThrow(expect.objectContaining({ code }));
    expect(row(db, t.id)).toEqual(before);
  }
  expect(read(db, t.id)).toMatchObject({
    stage: 'needs_what_changes',
    ceiling: 'needs_what_changes',
    pending_proposal: { body: 'body' },
  });
  expect(read(db, t.id).field_values.what_changes).toBeUndefined();
  expect(() =>
    db.read((sql) =>
      admitCommand(
        sql,
        worker(t.id),
        'POST',
        '/api/tickets/{ticket_id}/accept/{field}',
        { ticket_id: t.id, field: 'what_changes' },
        {},
      ),
    ),
  ).toThrow(expect.objectContaining({ code: 'agent_forbidden' }));
  expect(row(db, t.id)).toEqual(before);
  t = accept(db, t, 'what_changes');
  expect(t.stage).toBe('needs_implementation');
  expect(t.ceiling).toBe('needs_implementation');
  t = propose(db, t, 'x');
  expect(t.stage).toBe('needs_implementation');
  expect(t.ticket_status).toBe('awaiting_approval');
  let t2 = propose(db, create(db), 'body');
  t2 = accept(db, t2, 'what_changes');
  t2 = propose(db, t2, 'draft');
  expect(t2.pending_proposal).not.toBeNull();
  expect(t2.stage).toBe('needs_implementation');
  let t3 = propose(db, create(db), 'body');
  t3 = accept(db, t3, 'what_changes', 'needs_consequences');
  expect(t3.ceiling).toBe('needs_consequences');
  t3 = propose(db, t3, 'a');
  expect(t3).toMatchObject({
    stage: 'needs_consequences',
    ceiling: 'needs_consequences',
    field_values: { implementation: 'a' },
    pending_proposal: null,
  });
  let t4 = edit(db, create(db), { ceiling: 'done' });
  for (const body of ['a', 'i', 'c']) t4 = propose(db, t4, body);
  expect(t4.stage).toBe('done');
  expect(t4.ceiling).toBe('done');
});
test('a_ticket_created_with_a_live_blocker_never_starts_until_it_clears', () => {
  const db = fixture(),
    blocker = create(db, { title: 'Blocker' }),
    t = create(db, {
      title: 'Blocked at birth',
      settle_kickoff: false,
      stated_ceiling: 'needs_what_changes',
      blocked_by_ticket_ids: [blocker.id],
    });
  expect(t.stage).toBe('needs_what_changes');
  expect(t.ticket_status).toBe('blocked');
  expect(claim(db, t.id)).toBeNull();
  advance(db, blocker);
  expect(read(db, t.id).ticket_status).toBe('empty');
  expect(claim(db, t.id, now + 1)?.ticket_status).toBe('agent');
});
test('every_creation_blocker_holds_until_the_last_one_is_done', () => {
  const db = fixture(),
    blockers = Array.from({ length: 4 }, (_, i) => create(db, { title: `Blocker ${i}` })),
    t = create(db, {
      title: 'Blocked four times',
      settle_kickoff: false,
      stated_ceiling: 'needs_what_changes',
      blocked_by_ticket_ids: blockers.map((b) => b.id),
    });
  expect(t.ticket_status).toBe('blocked');
  expect(
    blockers.map((b) =>
      db.read((sql) =>
        sql
          .all<{ blocked_ticket_id: string }>(
            'SELECT blocked_ticket_id FROM ticket_blocks WHERE blocking_ticket_id=? ORDER BY blocked_ticket_id',
            [b.id],
          )
          .map((r) => r.blocked_ticket_id),
      ),
    ),
  ).toEqual(blockers.map(() => [t.id]));
  expect(claim(db, t.id)).toBeNull();
  for (const index of [2, 0, 3]) {
    advance(db, blockers[index]!);
    expect(read(db, t.id).ticket_status).toBe('blocked');
    expect(claim(db, t.id)).toBeNull();
  }
  advance(db, blockers[1]!);
  expect(claim(db, t.id, now + 1)?.ticket_status).toBe('agent');
});
test('a_parked_brief_outranks_a_creation_blocker_until_it_is_settled', () => {
  const db = fixture(),
    blocker = create(db),
    t = create(db, {
      title: 'Parked and blocked',
      settle_kickoff: false,
      blocked_by_ticket_ids: [blocker.id],
    });
  expect(t.stage).toBe('needs_brief');
  expect(t.ticket_status).toBe('awaiting_approval');
  expect(accept(db, t, 'brief', 'needs_what_changes').ticket_status).toBe('blocked');
  expect(claim(db, t.id)).toBeNull();
});
test('a_failed_creation_block_rolls_the_whole_create_back', () => {
  const db = fixture(),
    first = create(db),
    second = create(db),
    count = db.read((sql) => sql.get<{ n: number }>('SELECT COUNT(*) n FROM tickets')!.n),
    write = blocks.add_ticket_block,
    written: string[] = [];
  vi.spyOn(blocks, 'add_ticket_block').mockImplementation((tx, blocking, blocked, time) => {
    written.push(blocking);
    if (written.length === 2) throw new Error('block write failed mid-create');
    write(tx, blocking, blocked, time);
  });
  expect(() =>
    create(db, {
      title: 'Never existed',
      settle_kickoff: false,
      stated_ceiling: 'needs_what_changes',
      blocked_by_ticket_ids: [first.id, second.id],
    }),
  ).toThrow('mid-create');
  expect(written).toEqual([first.id, second.id]);
  expect(db.read((sql) => sql.get<{ n: number }>('SELECT COUNT(*) n FROM tickets')!.n)).toBe(count);
  expect(
    db.read((sql) =>
      sql.all('SELECT blocked_ticket_id FROM ticket_blocks WHERE blocking_ticket_id=?', [first.id]),
    ),
  ).toEqual([]);
});
test('a_claim_release_does_not_fire_once_the_ticket_has_moved_on', () => {
  const db = fixture(),
    t = create(db),
    claimed = claim(db, t.id)!;
  expect(claimed).not.toBeNull();
  expect(
    db.write((tx) =>
      data.release_worker_step_claim(tx, t.id, {
        expected_claim: claimed.worker_step_claim,
        expected_claim_revision: claimed.worker_step_claim_revision,
        now,
      }),
    ),
  ).toBe(true);
  const reclaimed = claim(db, t.id, now + 5)!;
  expect(reclaimed.ticket_status).toBe(claimed.ticket_status);
  expect(reclaimed.worker_step_claim_revision).not.toBe(claimed.worker_step_claim_revision);
  expect(
    db.write((tx) =>
      data.release_worker_step_claim(tx, t.id, {
        expected_claim: claimed.worker_step_claim,
        expected_claim_revision: claimed.worker_step_claim_revision,
        now: now + 6,
      }),
    ),
  ).toBe(false);
  expect(read(db, t.id).ticket_status).toBe('agent');
});
test('auto_approval_old_data_and_multi_field_edit', () => {
  const db = fixture();
  let t = create(db, { stated_ceiling: 'needs_implementation', settle_kickoff: false });
  expect(t.field_approvals.brief!.method).toBe('auto_accept');
  t = propose(db, t, 'Changes');
  expect(t.field_approvals.what_changes).toMatchObject({
    principal: null,
    display_label: 'Auto-accepted',
  });
  const before = row(db, t.id);
  expect(() =>
    edit(db, t, { field_values: { brief: 'Changed', implementation: 'Not settled' } }),
  ).toThrow();
  expect(row(db, t.id)).toEqual(before);
  t = edit(db, t, { field_values: { brief: 'Changed', what_changes: 'Changes' } });
  expect(t.field_approvals.brief!.method).toBe('unknown');
  expect(t.field_approvals.what_changes!.method).toBe('auto_accept');
  t = edit(db, t, { field_values: { what_changes: 'Changed', brief: 'Changed' } });
  expect(Object.values(t.field_approvals).every((a) => a.method === 'unknown')).toBe(true);
  db.write((tx) => tx.run("UPDATE tickets SET field_approvals='{}' WHERE id=?", [t.id]));
  const old = read(db, t.id);
  expect(Object.keys(old.field_approvals).sort()).toEqual(Object.keys(old.field_values).sort());
  expect(
    Object.values(old.field_approvals).every(
      (a) => a.display_label === 'Approval unknown' && a.principal === null,
    ),
  ).toBe(true);
});
