import { pageFacts } from '../../list_reads/logic/index.ts';
import {
  calendar_date,
  local_date,
  local_midnight,
  offset_date,
} from '../../measures/logic/dates.ts';
import { DEFAULT_DAYS, SORT_FIELDS, TIMEZONE } from '../configuration.ts';
import type {
  AttentionEvent,
  Comparison,
  Destination,
  Evidence,
  Figures,
  GateInterval,
  GroupBy,
  Metric,
  MetricName,
  Period,
  Scope,
  SummaryRequest,
  SummaryRow,
} from '../contracts.ts';
export const METRICS: MetricName[] = [
  'owner_messages',
  'owner_approvals',
  'approval_wait_seconds',
  'approval_wait_union_seconds',
  'active_ticket_count',
  'tickets_without_owner_involvement',
  'without_owner_involvement_share',
];
const text_order = (a: string, b: string): number => {
  const x = Array.from(a, (c) => c.codePointAt(0)!);
  const y = Array.from(b, (c) => c.codePointAt(0)!);
  for (let i = 0; i < Math.min(x.length, y.length); i++) if (x[i] !== y[i]) return x[i]! - y[i]!;
  return x.length - y.length;
};
export function periods(request: SummaryRequest, now: number): [Period, Period] {
  const today = local_date(now, TIMEZONE);
  if ((request.first_date === null) !== (request.last_date === null))
    throw new TypeError('first_date and last_date must be supplied together');
  const first = request.first_date ?? offset_date(today, -(DEFAULT_DAYS - 1));
  const last = request.last_date ?? today;
  if (first > last || last > today)
    throw new TypeError('dates must be ordered and must not extend beyond today');
  if (!(SORT_FIELDS as readonly string[]).includes(request.sort))
    throw new TypeError('unsupported attention sort');
  if (request.limit < 1 || request.offset < 0)
    throw new TypeError('limit must be positive and offset must be nonnegative');
  if (request.scope.kind === 'all' || request.scope.kind === 'chief') {
    if (request.scope.id !== null) throw new TypeError('all and chief scopes do not accept an id');
  } else if (!request.scope.id) throw new TypeError('this scope requires an id');
  try {
    if (
      !['project', 'manager', 'ticket', 'goal'].includes(request.group_by) ||
      !['all', 'project', 'manager', 'ticket', 'goal', 'chief'].includes(request.scope.kind)
    )
      throw new Error();
    const width = (calendar_date(last).getTime() - calendar_date(first).getTime()) / 86400000 + 1;
    const previous_last = offset_date(first, -1);
    const previous_first = offset_date(first, -width);
    const period = (a: string, b: string): Period => {
      const full_end = local_midnight(offset_date(b, 1), TIMEZONE);
      return {
        first_date: a,
        last_date: b,
        timezone: TIMEZONE,
        start_at: local_midnight(a, TIMEZONE),
        end_at: Math.min(full_end, now),
        partial: full_end > now,
      };
    };
    return [period(first, last), period(previous_first, previous_last)];
  } catch {
    throw new TypeError('invalid attention date range or grouping');
  }
}
export function matches(item: Destination, scope: Scope): boolean {
  if (scope.kind === 'all') return true;
  if (scope.kind === 'chief') return item.kind === 'chief';
  if (scope.kind === 'project') return item.project_id === scope.id;
  if (scope.kind === 'manager')
    return item.manager_id === scope.id || (item.kind === 'manager' && item.id === scope.id);
  return item.kind === scope.kind && item.id === scope.id;
}
function group(item: Destination, by: GroupBy): [string, string] {
  const relation = {
    project: item.project_id,
    manager: item.manager_id || (item.kind === 'manager' ? item.id : null),
    ticket: item.kind === 'ticket' ? item.id : null,
    goal: item.kind === 'goal' ? item.id : null,
  }[by];
  if (relation !== null) return [by, relation];
  if (by === 'ticket' && item.kind !== 'ticket') return [item.kind, item.id];
  return ['unattributed', 'unattributed'];
}
const key = (kind: string, id: string): string => JSON.stringify([kind, id]);
const group_key = (item: Destination, by: GroupBy): string => key(...group(item, by));
function metric(
  value: number | null,
  coverage_status: Metric['coverage_status'] = 'complete',
  ...limitations: string[]
): Metric {
  return { value, coverage_status, limitations };
}
function union(intervals: [number, number][]): number {
  let end: number | null = null;
  let result = 0;
  for (const [start, stop] of [...intervals].sort(([a, b], [c, d]) => a - c || b - d)) {
    result += Math.max(stop - (end === null ? start : Math.max(start, end)), 0);
    end = end === null ? stop : Math.max(end, stop);
  }
  return result;
}
function figures(
  events: AttentionEvent[],
  gates: GateInterval[],
  period: Period,
  evidence: Evidence,
): Figures {
  const start = period.start_at,
    end = period.end_at;
  const active = events.filter((event) => start <= event.occurred_at && event.occurred_at < end);
  const tickets = new Set(
    active.filter((e) => e.destination.kind === 'ticket').map((e) => e.destination.id),
  );
  const involved = new Set(
    active
      .filter(
        (e) =>
          e.destination.kind === 'ticket' && ['owner_message', 'owner_approval'].includes(e.kind),
      )
      .map((e) => e.destination.id),
  );
  const intervals: [number, number][] = [];
  for (const gate of gates) {
    const lo = Math.max(start, gate.opened_at ?? gate.observed_since);
    const hi = Math.min(end, gate.closed_at ?? end);
    if (lo < hi) {
      intervals.push([lo, hi]);
      tickets.add(gate.destination.id);
    }
  }
  const messages = active.filter((e) => e.kind === 'owner_message').length;
  const approvals = active.filter((e) => e.kind === 'owner_approval').length;
  const without = [...tickets].filter((id) => !involved.has(id)).length;
  const result: Figures = {
    owner_messages: metric(messages),
    owner_approvals: metric(approvals),
    approval_wait_seconds: metric(intervals.reduce((sum, [lo, hi]) => sum + hi - lo, 0)),
    approval_wait_union_seconds: metric(union(intervals)),
    active_ticket_count: metric(tickets.size),
    tickets_without_owner_involvement: metric(without),
    without_owner_involvement_share: metric(tickets.size ? without / tickets.size : null),
  };
  const message_unknown =
    evidence.message_history_started_at === null || start < evidence.message_history_started_at;
  const missing_senders = evidence.unattributed_message_times.some((at) => start <= at && at < end);
  const history_unknown = start < evidence.history_started_at;
  if (message_unknown || missing_senders)
    result.owner_messages = metric(
      messages || null,
      messages ? 'partial' : 'unknown',
      'Retained message history or authenticated sender metadata is incomplete for this period.',
    );
  if (history_unknown)
    for (const name of [
      'owner_approvals',
      'approval_wait_seconds',
      'approval_wait_union_seconds',
    ] as const)
      result[name] = metric(
        null,
        'unknown',
        'This period predates complete approval and gate history.',
      );
  if (message_unknown || missing_senders || history_unknown) {
    for (const name of [
      'tickets_without_owner_involvement',
      'without_owner_involvement_share',
    ] as const)
      result[name] = metric(
        null,
        'unknown',
        'Absence of owner involvement cannot be established from incomplete history.',
      );
    result.active_ticket_count.coverage_status = 'partial';
    result.active_ticket_count.limitations.push(
      'The activity denominator can omit unavailable history.',
    );
  }
  if (period.partial)
    for (const item of Object.values(result)) {
      if (item.coverage_status === 'complete') item.coverage_status = 'partial';
      item.limitations.push('The current local date is incomplete.');
    }
  return result;
}
function comparison(current: Figures, previous: Figures): Comparison {
  const difference = {} as Figures;
  for (const name of METRICS) {
    const left = current[name],
      right = previous[name];
    const status =
      left.coverage_status === 'complete' && right.coverage_status === 'complete'
        ? 'complete'
        : 'partial';
    const value = left.value === null || right.value === null ? null : left.value - right.value;
    difference[name] = metric(
      value,
      value === null ? 'unknown' : status,
      ...new Set([...left.limitations, ...right.limitations]),
    );
  }
  return { current, previous, difference };
}
export function build_summary(request: SummaryRequest, evidence: Evidence, now: number) {
  const [current_period, previous_period] = periods(request, now);
  const unique = new Map<string, AttentionEvent>();
  for (const event of [...evidence.events].sort(
    (a, b) => a.occurred_at - b.occurred_at || text_order(a.identity, b.identity),
  ))
    if (!unique.has(event.identity)) unique.set(event.identity, event);
  const events = [...unique.values()].filter((e) => matches(e.destination, request.scope));
  const gates = evidence.gates.filter((g) => matches(g.destination, request.scope));
  const totals = comparison(
    figures(events, gates, current_period, evidence),
    figures(events, gates, previous_period, evidence),
  );
  const groups = new Map<string, Destination>();
  const within = (e: AttentionEvent): boolean =>
    previous_period.start_at <= e.occurred_at && e.occurred_at < current_period.end_at;
  const overlaps = (g: GateInterval): boolean =>
    (g.closed_at ?? now) > previous_period.start_at &&
    (g.opened_at ?? g.observed_since) < current_period.end_at;
  for (const event of events.filter(within)) {
    const id = group_key(event.destination, request.group_by);
    if (!groups.has(id)) groups.set(id, event.destination);
  }
  for (const gate of gates.filter(overlaps)) {
    const id = group_key(gate.destination, request.group_by);
    if (!groups.has(id)) groups.set(id, gate.destination);
  }
  const labels = new Map(evidence.destinations.map((item) => [key(item.kind, item.id), item]));
  const rows: SummaryRow[] = [];
  for (const [id, source] of groups) {
    const [kind, identifier] = JSON.parse(id) as [string, string];
    const label = labels.get(id);
    const direct = kind === source.kind && identifier === source.id;
    const title =
      kind === 'unattributed'
        ? 'Unattributed'
        : (label?.title ??
          (direct ? source.title : `${kind[0]!.toUpperCase()}${kind.slice(1)} ${identifier}`));
    const url =
      kind === 'unattributed' ? null : label !== undefined ? label.url : direct ? source.url : null;
    const group_events = events.filter((e) => group_key(e.destination, request.group_by) === id);
    const group_gates = gates.filter((g) => group_key(g.destination, request.group_by) === id);
    const destinations = [
      ...group_events.filter(within).map((e) => e.destination),
      ...group_gates.filter(overlaps).map((g) => g.destination),
    ];
    const numbers = comparison(
      figures(group_events, group_gates, current_period, evidence),
      figures(group_events, group_gates, previous_period, evidence),
    );
    const relation = (name: 'project_id' | 'manager_id' | 'goal_id'): string | null => {
      const values = new Set(destinations.map((item) => item[name]));
      return values.size === 1 ? [...values][0]! : null;
    };
    const share = (name: 'owner_messages' | 'owner_approvals'): number | null => {
      const numerator = numbers.current[name].value,
        denominator = totals.current[name].value;
      return numerator !== null && denominator ? numerator / denominator : null;
    };
    rows.push({
      kind,
      id: identifier,
      title,
      url,
      attribution_basis: [...new Set(destinations.map((d) => d.attribution_basis))].sort(
        text_order,
      ),
      ...numbers,
      message_share: share('owner_messages'),
      approval_share: share('owner_approvals'),
      project_id: relation('project_id'),
      manager_id: relation('manager_id'),
      goal_id: relation('goal_id'),
    });
  }
  rows.sort(
    (a, b) =>
      -(a.current[request.sort as MetricName].value || 0) +
        (b.current[request.sort as MetricName].value || 0) ||
      text_order(a.kind, b.kind) ||
      text_order(a.id, b.id),
  );
  const selected = rows.slice(request.offset, request.offset + request.limit);
  const limitations = [...evidence.limitations];
  if (request.group_by === 'goal' || request.scope.kind === 'goal')
    limitations.push(
      'Goal figures cover direct goal conversations only. Ticket-to-goal membership is unavailable.',
    );
  return {
    period: current_period,
    previous_period,
    scope: request.scope,
    group_by: request.group_by,
    totals,
    rows: selected,
    page: pageFacts(rows.length, selected.length, { limit: request.limit, offset: request.offset }),
    snapshot_at: now,
    reliable_history_start: {
      messages: evidence.message_history_started_at,
      approvals_and_gates: evidence.history_started_at,
    },
    limitations,
  };
}
