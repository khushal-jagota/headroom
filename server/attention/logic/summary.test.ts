import { expect, test } from 'vitest';
import { destination, summary_request } from '../contracts.ts';
import type { Destination, Evidence } from '../contracts.ts';
import { build_summary, periods } from './summary.ts';
import { local_midnight } from '../../measures/logic/dates.ts';
const at = (day: string, hour = 0): number => local_midnight(day, 'Europe/Berlin') + hour * 3600;
const request = (values: Parameters<typeof summary_request>[0] = {}) =>
  summary_request({
    first_date: '2026-11-02',
    last_date: '2026-11-02',
    ...values,
  });
const ticket = (id: string, project = 'p', manager: string | null = null): Destination =>
  destination('ticket', id, id, `/tickets/${id}`, { project_id: project, manager_id: manager });
const evidence = (values: Partial<Evidence> = {}): Evidence => ({
  events: [],
  gates: [],
  history_started_at: at('2026-10-01'),
  limitations: [],
  destinations: [],
  message_history_started_at: null,
  unattributed_message_times: [],
  ...values,
});
test('grouped figures retain unique events union wait and full-page denominators', () => {
  const first = ticket('first', 'p', 'm'),
    second = ticket('second', 'q'),
    start = at('2026-11-02');
  const result = build_summary(
    request({ limit: 1 }),
    evidence({
      events: [
        { identity: 'one', occurred_at: start + 100, kind: 'owner_message', destination: first },
        { identity: 'two', occurred_at: start + 200, kind: 'owner_message', destination: first },
        {
          identity: 'approve',
          occurred_at: start + 300,
          kind: 'owner_approval',
          destination: second,
        },
        {
          identity: 'agent',
          occurred_at: start + 400,
          kind: 'activity',
          destination: ticket('agent', 'q'),
        },
      ],
      gates: [
        {
          identity: 'g1',
          destination: first,
          opened_at: start - 50,
          observed_since: start - 50,
          closed_at: start + 600,
        },
        {
          identity: 'g2',
          destination: second,
          opened_at: start + 300,
          observed_since: start + 300,
          closed_at: start + 900,
        },
      ],
      history_started_at: start - 864000,
      message_history_started_at: start - 864000,
      destinations: [destination('project', 'p', 'Project P', '/projects/p', { project_id: 'p' })],
    }),
    at('2026-11-04'),
  );
  const current = result.totals.current;
  expect(current.owner_messages.value).toBe(2);
  expect(current.owner_approvals.value).toBe(1);
  expect(current.approval_wait_seconds.value).toBe(1200);
  expect(current.approval_wait_union_seconds.value).toBe(900);
  expect(current.active_ticket_count.value).toBe(3);
  expect(current.without_owner_involvement_share.value).toBeCloseTo(1 / 3);
  expect(result.page.match_count).toBe(2);
  expect(result.page.omitted_after).toBe(1);
  expect(result.rows[0]!.title).toBe('Project P');
  expect(result.rows[0]!.message_share).toBe(1);
  expect(result.rows[0]!.approval_share).toBe(0);
});
test('global message identity precedes date scope and group selection', () => {
  const data = evidence({
    events: [
      {
        identity: 'owner:replayed',
        occurred_at: at('2026-11-02', 10),
        kind: 'owner_message',
        destination: ticket('copy', 'q'),
      },
      {
        identity: 'owner:replayed',
        occurred_at: at('2026-11-01', 10),
        kind: 'owner_message',
        destination: ticket('original', 'p'),
      },
    ],
    message_history_started_at: at('2026-10-01'),
  });
  const scoped = build_summary(
    request({ scope: { kind: 'project', id: 'q' } }),
    data,
    at('2026-11-04'),
  );
  expect(scoped.totals.current.owner_messages.value).toBe(0);
  expect(scoped.rows).toEqual([]);
  const all = build_summary(request(), data, at('2026-11-04'));
  expect(all.totals.current.owner_messages.value).toBe(0);
  expect(all.totals.previous.owner_messages.value).toBe(1);
});
test('unknown approvals and unauthenticated messages cannot prove absence', () => {
  const start = at('2026-11-02');
  const result = build_summary(
    request(),
    evidence({
      events: [
        {
          identity: 'agent',
          occurred_at: start + 10,
          kind: 'activity',
          destination: ticket('work'),
        },
      ],
      history_started_at: start + 100,
      message_history_started_at: start - 86400,
      unattributed_message_times: [start + 20],
    }),
    at('2026-11-04'),
  );
  for (const metric of [
    'owner_messages',
    'owner_approvals',
    'tickets_without_owner_involvement',
    'without_owner_involvement_share',
  ] as const)
    expect(result.totals.current[metric].value).toBeNull();
  expect(result.totals.current.active_ticket_count.coverage_status).toBe('partial');
  expect(result.totals.difference.owner_approvals.value).toBeNull();
});
test('open unknown-start gates and direct Goal attribution stay explicit', () => {
  const start = at('2026-11-02');
  const result = build_summary(
    request({ group_by: 'goal' }),
    evidence({
      events: [
        {
          identity: 'direct',
          occurred_at: start + 10,
          kind: 'owner_message',
          destination: destination('goal', 'g', 'Goal', '/goals/g', {
            project_id: 'p',
            goal_id: 'g',
          }),
        },
        {
          identity: 'ticket',
          occurred_at: start + 20,
          kind: 'owner_message',
          destination: ticket('t'),
        },
      ],
      gates: [
        {
          identity: 'open',
          destination: ticket('t'),
          opened_at: null,
          observed_since: start,
          closed_at: null,
        },
      ],
      history_started_at: start,
      message_history_started_at: start - 86400,
    }),
    start + 3600,
  );
  expect(result.totals.current.approval_wait_seconds.value).toBe(3600);
  expect(result.totals.current.approval_wait_seconds.coverage_status).toBe('partial');
  expect(new Set(result.rows.map((row) => row.kind))).toEqual(new Set(['goal', 'unattributed']));
  expect(result.limitations.some((note) => note.includes('direct goal'))).toBe(true);
});
test('calendar comparisons use DST boundaries and validate dates', () => {
  const [current, previous] = periods(
    summary_request({ first_date: '2026-10-25', last_date: '2026-10-25' }),
    at('2026-11-04'),
  );
  expect(current.end_at - current.start_at).toBe(25 * 3600);
  expect(previous.end_at - previous.start_at).toBe(24 * 3600);
  expect(previous.last_date).toBe('2026-10-24');
  expect(() => periods(summary_request({ first_date: '2026-11-01' }), at('2026-11-04'))).toThrow(
    /together/,
  );
  expect(() =>
    periods(
      summary_request({ first_date: '2026-11-05', last_date: '2026-11-05' }),
      at('2026-11-04'),
    ),
  ).toThrow(/beyond today/);
});
