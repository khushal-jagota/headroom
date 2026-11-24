// Distinct risk: the need word is the one answer every reader prints. A wrong precedence makes a
// done or backlogged Ticket read as waiting on the owner. The rule is pure, so a table proves it.
import { expect, test } from 'vitest';
import { ticket_need } from './logic.ts';

const owner = { kind: 'owner', id: 'owner' };
const chief = { kind: 'chief', id: 'chief' };
const quiet = { signal: { working: false, message_for_owner: false }, awaiting_answer: false };
const message = { ...quiet, signal: { working: false, message_for_owner: true } };
const asking = { ...quiet, awaiting_answer: true };
const working = { ...quiet, signal: { working: true, message_for_owner: false } };
const live = { stage: 'needs_brief', terminal: false, is_backlogged: false, ceiling_holder: owner };

test.each([
  [
    'integrity error wins over backlog',
    { integrity_error: {}, is_backlogged: true },
    null,
    'errored',
  ],
  [
    'backlog wins over an owner proposal and a message',
    { ...live, is_backlogged: true, ticket_status: 'awaiting_approval' },
    message,
    'backlog',
  ],
  ['backlog wins over done', { ...live, is_backlogged: true, terminal: true }, null, 'backlog'],
  [
    'done wins over an owner proposal and a message',
    { ...live, terminal: true, ticket_status: 'awaiting_approval' },
    message,
    'done',
  ],
  [
    'stage done is terminal without the flag',
    { ...live, stage: 'done', terminal: undefined },
    asking,
    'done',
  ],
  ['awaiting_approval', { ...live, ticket_status: 'awaiting_approval' }, quiet, 'owner_approval'],
  ['awaiting_answer', { ...live, ticket_status: 'agent' }, asking, 'owner_answer'],
  ['assigned', { ...live, ticket_status: 'empty', stage_ownership: 'user' }, quiet, 'owner_stage'],
  ['awaiting_reply', { ...live, ticket_status: 'empty' }, message, 'owner_reply'],
  [
    'status_awaiting_approval',
    { ...live, ticket_status: 'awaiting_approval', ceiling_holder: chief },
    quiet,
    'agent_approval',
  ],
  ['agent', { ...live, ticket_status: 'empty' }, working, 'working'],
  ['empty', { ...live, ticket_status: 'empty' }, null, 'ready'],
  ['blocked', { ...live, ticket_status: 'empty', blocked: true }, null, 'blocked'],
  ['errored', { ...live, ticket_status: 'errored' }, null, 'errored'],
])('%s', (_name, value, attention, need) => {
  expect(ticket_need(value, attention)).toBe(need);
});
