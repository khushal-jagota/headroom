import { afterEach, expect, test, vi } from 'vitest';
import { OWNER, CHIEF, PlannerError } from '../core/contracts.ts';
import { JsonNumber, stringify_numeric_json } from '../core/http/numeric_json.ts';
import { StoredTicketIntegrityError, type DecisionCommand } from './contracts.ts';
import * as data from './data/index.ts';
import { apply, lookup, parse_command } from './decision_operations.ts';
import { fixture, create, propose, read, dump, now, worker } from '../tests/ticket_fixture.ts';

afterEach(() => vi.restoreAllMocks());
const state = (db: ReturnType<typeof fixture>) =>
  (dump(db) as [string, unknown][]).filter(([table]) => table !== 'ticket_decision_operations');
function command(revision: number, decision: DecisionCommand['decision']): DecisionCommand {
  return {
    decision,
    field: 'what_changes',
    expected_proposal_revision: revision,
    ...(decision === 'approve'
      ? {
          next_holder: { kind: 'owner' as const, id: 'owner' },
          next_ceiling: 'implementation',
          edited_body: 'Accepted exact text',
        }
      : {}),
    message: '  Exact note.\n\nUnicode café 🧭\nFinal line.  ',
  };
}

// Existing transition coverage does not prove immutable receipt recovery after later work.
test.each(['approve', 'send_back'] as const)(
  '%s replay preserves its original outcome and feedback',
  (decision) => {
    const db = fixture(),
      original = propose(db, create(db), 'Original');
    if (decision === 'send_back')
      db.write((tx) =>
        tx.run("UPDATE tickets SET conversation_id='c_decision_replay' WHERE id=?", [original.id]),
      );
    const body = { ...command(original.pending_proposal_revision, decision) };
    const result = db.write((tx) => apply(tx, original.id, 'lost-response', body, OWNER, now));
    expect(result.receipt?.outcome).toBe('applied');
    const next = propose(db, read(db, original.id), 'A later proposal');
    const saved = state(db);
    expect(db.read((sql) => lookup(sql, original.id, 'lost-response', OWNER))).toEqual(result);
    const unavailable = vi.spyOn(data, 'load_ticket_for_write').mockImplementation(() => {
      throw new Error('workflow unavailable');
    });
    expect(db.write((tx) => apply(tx, original.id, 'lost-response', body, OWNER, now + 1))).toEqual(
      result,
    );
    unavailable.mockRestore();
    expect(state(db)).toEqual(saved);
    expect(
      db.read((sql) =>
        sql.all('SELECT * FROM ticket_decision_messages WHERE decision_id=?', ['lost-response']),
      ),
    ).toHaveLength(1);
    const refused = db.write((tx) => apply(tx, next.id, 'stale', body, OWNER, now + 2));
    expect(refused.receipt?.outcome).toBe('refused');
    propose(db, next, 'Another proposal');
    expect(db.write((tx) => apply(tx, next.id, 'stale', body, OWNER, now + 3))).toEqual(refused);
  },
);

// Receipt identity must not reveal another actor's saved decision or merge null with omission.
test('private receipt identity preserves submitted keys and global ownership', () => {
  const db = fixture(),
    original = propose(db, create(db), 'Original');
  const body = { ...command(original.pending_proposal_revision, 'send_back') };
  delete body.message;
  db.write((tx) => apply(tx, original.id, 'private', body, OWNER, now));
  expect(db.read((sql) => lookup(sql, original.id, 'private', CHIEF))).toEqual({
    receipt_contract: 1,
    receipt: null,
  });
  expect(() =>
    db.read((sql) => lookup(sql, original.id, 'private', worker(original.id))),
  ).toThrowError(expect.objectContaining({ code: 'agent_forbidden' }));
  expect(() => db.write((tx) => apply(tx, original.id, 'private', body, CHIEF, now))).toThrowError(
    expect.objectContaining({ code: 'agent_forbidden', detail: {} }),
  );
  expect(() =>
    db.write((tx) => apply(tx, original.id, 'private', { ...body, message: null }, OWNER, now)),
  ).toThrow('different content');
  const other = propose(db, create(db), 'Other');
  expect(() => db.write((tx) => apply(tx, other.id, 'private', body, OWNER, now))).toThrow(
    'another Ticket',
  );
});

// A receipt failure must roll back canonical writes; a retained refusal needs a savepoint.
test.each(['receipt', 'business', 'integrity', 'operational'] as const)(
  '%s failure retains only admitted durable effects',
  (failure) => {
    const db = fixture(),
      original = propose(db, create(db), 'Original');
    const body = { ...command(original.pending_proposal_revision, 'approve') },
      before = state(db);
    if (failure === 'receipt')
      db.write((tx) =>
        tx.run(
          "CREATE TRIGGER refuse_receipt BEFORE INSERT ON ticket_decision_operations BEGIN SELECT RAISE(ABORT, 'receipt disk failure'); END",
        ),
      );
    else {
      const real = data.accept_proposal;
      vi.spyOn(data, 'accept_proposal').mockImplementation((...args) => {
        real(...args);
        throw failure === 'integrity'
          ? new StoredTicketIntegrityError(original.id, 'test fault')
          : new PlannerError(
              failure === 'business' ? 'validation' : 'gateway_offline',
              'post-write refusal',
            );
      });
    }
    const run = () => db.write((tx) => apply(tx, original.id, 'atomic', body, OWNER, now));
    if (failure === 'business') expect(run().receipt?.outcome).toBe('refused');
    else expect(run).toThrow();
    expect(state(db)).toEqual(before);
    expect(db.read((sql) => sql.all('SELECT * FROM ticket_decision_operations'))).toHaveLength(
      failure === 'business' ? 1 : 0,
    );
    if (failure === 'receipt') db.write((tx) => tx.run('DROP TRIGGER refuse_receipt'));
    vi.restoreAllMocks();
    expect(run().receipt?.outcome).toBe(failure === 'business' ? 'refused' : 'applied');
  },
);

// Exact JSON numeric kinds and codepoint bounds precede every write and retry.
test('command admission preserves integer digits, holder kinds, and Unicode bounds', () => {
  const base = command(1, 'approve');
  for (const expected_proposal_revision of [true, null, '1', 0, new JsonNumber('1.0')])
    expect(() => parse_command({ ...base, expected_proposal_revision })).toThrow(
      'positive integer',
    );
  expect(() => parse_command({ ...base, next_holder: { kind: 'goal', id: 'g' } })).toThrow(
    'owner, chief, or manager',
  );
  expect(() => parse_command({ ...base, edited_body: '🧭'.repeat(10001) })).toThrow('10000');
  expect(parse_command({ ...base, edited_body: '🧭'.repeat(10000) }).edited_body).toHaveLength(
    20000,
  );
  const revision = 900719925474099312345n;
  const db = fixture(),
    original = propose(db, create(db), 'Original');
  const result = db.write((tx) =>
    apply(
      tx,
      original.id,
      'large',
      { ...base, expected_proposal_revision: new JsonNumber(String(revision)) },
      OWNER,
      now,
    ),
  );
  expect(result.receipt?.outcome).toBe('refused');
  expect(stringify_numeric_json(result)).toContain('"expected_proposal_revision":' + revision);
  expect(db.read((sql) => lookup(sql, original.id, 'large', OWNER))).toEqual(result);
});
