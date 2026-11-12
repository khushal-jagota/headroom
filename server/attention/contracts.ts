export type GroupBy = 'project' | 'manager' | 'ticket' | 'goal';
export type ScopeKind = 'all' | GroupBy | 'chief';
export interface Scope {
  kind: ScopeKind;
  id: string | null;
}
export interface SummaryRequest {
  first_date: string | null;
  last_date: string | null;
  group_by: GroupBy;
  scope: Scope;
  sort: string;
  limit: number;
  offset: number;
}
export interface Destination {
  kind: string;
  id: string;
  title: string;
  url: string | null;
  project_id: string | null;
  manager_id: string | null;
  goal_id: string | null;
  attribution_basis: string;
}
export interface AttentionEvent {
  identity: string;
  occurred_at: number;
  kind: string;
  destination: Destination;
}
export interface GateInterval {
  identity: string;
  destination: Destination;
  opened_at: number | null;
  observed_since: number;
  closed_at: number | null;
}
export interface Evidence {
  events: AttentionEvent[];
  gates: GateInterval[];
  history_started_at: number;
  limitations: string[];
  destinations: Destination[];
  message_history_started_at: number | null;
  unattributed_message_times: number[];
}
export interface Period {
  first_date: string;
  last_date: string;
  timezone: string;
  start_at: number;
  end_at: number;
  partial: boolean;
}
export interface Metric {
  value: number | null;
  coverage_status: 'complete' | 'partial' | 'unknown';
  limitations: string[];
}
export type MetricName =
  | 'owner_messages'
  | 'owner_approvals'
  | 'approval_wait_seconds'
  | 'approval_wait_union_seconds'
  | 'active_ticket_count'
  | 'tickets_without_owner_involvement'
  | 'without_owner_involvement_share';
export type Figures = Record<MetricName, Metric>;
export interface Comparison {
  current: Figures;
  previous: Figures;
  difference: Figures;
}
export interface SummaryRow extends Comparison {
  kind: string;
  id: string;
  title: string;
  url: string | null;
  attribution_basis: string[];
  message_share: number | null;
  approval_share: number | null;
  project_id: string | null;
  manager_id: string | null;
  goal_id: string | null;
}
export function destination(
  kind: string,
  id: string,
  title: string,
  url: string | null,
  values: Partial<Destination> = {},
): Destination {
  return {
    kind,
    id,
    title,
    url,
    project_id: null,
    manager_id: null,
    goal_id: null,
    attribution_basis: 'recorded',
    ...values,
  };
}
export function summary_request(values: Partial<SummaryRequest> = {}): SummaryRequest {
  return {
    first_date: null,
    last_date: null,
    group_by: 'project',
    scope: { kind: 'all', id: null },
    sort: 'owner_messages',
    limit: 50,
    offset: 0,
    ...values,
  };
}
