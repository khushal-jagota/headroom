import { expect, test, vi } from 'vitest';
import { OWNER } from '../core/contracts.ts';
import type { Clock } from '../core/clock.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import { update_chief_recap } from '../worker_settings/service.ts';
import { validate_recap } from '../tickets/logic/recap.ts';
import { update_manager } from '../managers/data.ts';
import { request_recap, RECAP_REQUEST_MESSAGE } from '../message_delivery/recap_request.ts';
import { send_message } from '../message_delivery/service.ts';
import { fixture, create, edit, propose, accept, read, manager, now } from './ticket_fixture.ts';

vi.mock('../message_delivery/service.ts', () => ({ send_message: vi.fn() }));
const clock: Clock = {
  nowUnix: () => now,
  now: () => new Date(now * 1000),
  isoformat: () => new Date(now * 1000).toISOString(),
};

// Existing Ticket writes do not distinguish recap age from record age, or two
// transitions in one second. Reuse the same database and canonical write doors.
test('recap age survives unrelated edits and same-second stage decisions until its next write', () => {
  const db = fixture();
  let ticket = create(db, { settle_kickoff: false });
  ticket = edit(db, ticket, { recap: 'Ready for review.' });
  ticket = edit(db, ticket, { title: 'Unrelated title' }, OWNER, now + 10);
  expect(ticket.recap_updated_at).toBe(now);
  ticket = accept(db, propose(db, ticket, 'Scope'), 'brief');
  expect([ticket.recap_updated_at, ticket.recap_stage_changed]).toEqual([now, true]);
  ticket = edit(db, ticket, { recap: 'Ready for review.' }, OWNER, now + 20);
  expect([ticket.recap_updated_at, ticket.recap_stage_changed]).toEqual([now + 20, false]);
  ticket = edit(db, ticket, { recap: '' });
  expect([ticket.recap_updated_at, ticket.recap_stage_changed]).toEqual([null, false]);
  const id = manager(db, 'Fresh manager');
  const update = (edits: Record<string, string>, time: number) =>
    db.write((tx) => update_manager(tx, id, { edits, clock: { ...clock, nowUnix: () => time } }));
  update({ recap: 'Manager scope' }, now);
  expect(update({ title: 'Changed title' }, now + 10).recap_updated_at).toBe(now);
  expect(update({ recap: '' }, now + 20).recap_updated_at).toBeNull();
  // Gap: transport tests cannot prove all three canonical writers refuse atomically.
  for (const recap of [
    '🧭'.repeat(81),
    'Review t_01234567',
    'Build abc1234',
    'Host 12345678-abcd-1234-abcd-123456789abc',
  ]) {
    expect(() => edit(db, ticket, { recap })).toThrow(
      expect.objectContaining({ code: 'validation' }),
    );
    expect(() => update({ recap }, now)).toThrow(expect.objectContaining({ code: 'validation' }));
    expect(() => update_chief_recap(db, { recap })).toThrow(
      expect.objectContaining({ code: 'validation' }),
    );
  }
  for (const recap of ['  ' + '🧭'.repeat(80) + '  ', 'defaced 12345678', 'x_t_01234567'])
    expect(() => validate_recap(recap)).not.toThrow();
  expect(read(db, ticket.id).recap).toBe('');
  expect(update({ title: 'Still empty' }, now).recap).toBe('');
  expect(update_chief_recap(db, { recap: '🧭'.repeat(80) }).recap).toBe('🧭'.repeat(80));
  expect(update_chief_recap(db, { recap: '' }).recap).toBe('');
});

// HTTP parity proves delivered prompt content. This direct seam also proves that
// non-HTTP callers cannot bypass admission, and refusal cannot clear the recap.
test('recap admission precedes clearing and sends the requester through the ordinary door', async () => {
  const db = fixture();
  const own = manager(db, 'Own scope'),
    foreign = manager(db, 'Foreign scope');
  const ticket = edit(db, create(db, { manager_id: own }), { recap: 'Keep until admitted' });
  const target = { kind: 'ticket' as const, id: ticket.id };
  const conversations = {} as ConversationSystem;
  await expect(
    request_recap(
      conversations,
      db,
      clock,
      { principal: { kind: 'manager', id: foreign }, conversationId: null },
      target,
    ),
  ).rejects.toMatchObject({ code: 'agent_forbidden' });
  expect(read(db, ticket.id).recap).toBe('Keep until admitted');
  expect(send_message).not.toHaveBeenCalled();
  vi.mocked(send_message).mockImplementationOnce(async () => {
    expect(read(db, ticket.id).recap).toBe('Keep until admitted');
    throw new Error('scripted refusal');
  });
  await expect(
    request_recap(
      conversations,
      db,
      clock,
      { principal: { kind: 'manager', id: own }, conversationId: null },
      target,
    ),
  ).rejects.toThrow('scripted refusal');
  expect(read(db, ticket.id).recap).toBe('Keep until admitted');
  expect(vi.mocked(send_message).mock.calls[0]?.slice(5)).toMatchObject([
    `${RECAP_REQUEST_MESSAGE}\nRequested by Manager Own scope.`,
    'steer',
    {
      system_message: true,
      sender_message_id: expect.stringMatching(/^recap_/),
      recording_receipt: { in_admission: expect.any(Function), admitted: expect.any(Function) },
    },
  ]);
});
