import type { ReadSql } from '../../core/database/contracts.ts';
import type { LifecycleSummary, CurrentAttentionKnowledge } from '../../../web/src/lib/navigation/contracts.ts';
import { RECAP_PREVIEW_CHARACTERS } from './derivation_configuration.ts';
import { PlannerError } from '../../core/contracts.ts';
export type Row = Record<string, any>;
export interface WorkflowStage { id: string; label: string; gating_field: string | null; is_terminal: boolean; ownership_mode: string | null }
export interface Workflow { label: string; stages: WorkflowStage[] }
export interface DerivationContext { workflows: Map<string, Workflow>; ticketOwners?: Map<string, Map<string, Row>> }
export const newDerivationContext = (): DerivationContext => ({ workflows: new Map() });
export function hasColumn(sql: ReadSql, table: string, column: string): boolean {
  return !!sql.get('SELECT 1 FROM pragma_table_info(?) WHERE name=?', [table, column]);
}
export function recapPreview(recap: string): string {
  // Python str.split includes ASCII separators and Unicode whitespace, including U+0085.
  const compact = recap.split(/[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/u).filter(Boolean).join(' ');
  const points = [...compact];
  return points.length <= RECAP_PREVIEW_CHARACTERS ? compact : points.slice(0, RECAP_PREVIEW_CHARACTERS - 1).join('').replace(/[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/u, '') + '…';
}
export function storedWorkflow(raw: string, context: DerivationContext): Workflow {
  const saved = context.workflows.get(raw); if (saved) return saved;
  const parsed = JSON.parse(raw) as Workflow;
  if (!parsed || typeof parsed.label !== 'string' || !Array.isArray(parsed.stages)) throw new Error('saved Ticket workflow is malformed');
  for (const stage of parsed.stages) {
    if (typeof stage.id !== 'string' || typeof stage.label !== 'string' || typeof stage.is_terminal !== 'boolean'
      || stage.gating_field !== null && typeof stage.gating_field !== 'string'
      || stage.ownership_mode !== null && !['worker', 'user', 'paired'].includes(stage.ownership_mode)) throw new Error('saved Ticket workflow stage is malformed');
  }
  context.workflows.set(raw, parsed); return parsed;
}
export function proposal(raw: string | null): Row | null {
  if (raw === null) return null;
  const value = JSON.parse(raw);
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).sort().join(',') !== 'body,created_at,field,proposed_by'
      || ['field', 'body', 'proposed_by'].some(key => typeof value[key] !== 'string') || !Number.isInteger(value.created_at)) throw new PlannerError('validation', 'corrupt ticket field state');
  return value;
}
const SAFE_REASONS = new Set([
  'PlannerError: unknown worker type (worker_type)', 'PlannerError: stage outside the linear order (stage)',
  "PlannerError: ceiling outside the type's range (ceiling)", 'PlannerError: ceiling holder does not exist (ceiling_holder)',
  'PlannerError: a Ticket cannot hold its own ceiling (ceiling_holder)', 'PlannerError: corrupt ticket field state (fields or proposal)',
  'PlannerError: unknown agent backend (employee_backend)', 'PlannerError: invalid stored Ticket structure',
  'JSONDecodeError: malformed stored JSON', 'KeyError: missing stored Ticket member',
  'TypeError: invalid stored Ticket member type', 'ValueError: invalid stored Ticket member value',
  'TicketIntegrityError: structural fault reason unavailable',
]);
export const safeReason = (reason: string): string => SAFE_REASONS.has(reason) ? reason : 'TicketIntegrityError: structural fault reason unavailable';
export function effectiveCompletion(goal: Row): boolean {
  if (goal.kind === 'quantitative' && goal.completion_rule !== null && goal.completion_rule !== undefined) return Boolean(goal.completion_date);
  if (!('primary_checklist_block_id' in goal) || goal.primary_checklist_block_id === null) return Boolean(goal.completed);
  const page = JSON.parse(goal.page);
  function * blocks(items: Row[]): Generator<Row> {
    for (const item of items) {
      yield item; yield * blocks(item.blocks ?? []);
      for (const view of item.views ?? []) yield * blocks(view.blocks);
    }
  }
  for (const block of blocks(page.blocks)) if (block.id === goal.primary_checklist_block_id && block.type === 'checklist') return !!block.items.length && block.items.every((item: Row) => item.state === 'done');
  return false;
}
export function lifecycleSummary(sql: ReadSql, id: string): LifecycleSummary {
  const latest = sql.get<{ identity: string; turn_number: number; status: string }>("SELECT identity,turn_number,status FROM conversation_lifecycle_facts INDEXED BY navigation_lifecycle_latest_turn WHERE conversation_id=? AND kind='turn' ORDER BY turn_number DESC,identity DESC LIMIT 1", [id]);
  const rows = sql.all<{ kind: string; status: string; count: number }>("SELECT kind,status,COUNT(*) AS count FROM conversation_lifecycle_facts INDEXED BY navigation_lifecycle_unresolved WHERE conversation_id=? AND ((kind='turn' AND status IN ('intent','running')) OR (kind='request' AND status IN ('pending','answering')) OR (kind='attempt' AND status IN ('intent','unknown'))) GROUP BY kind,status", [id]);
  const count = (kind: string, status: string) => rows.find(row => row.kind === kind && row.status === status)?.count ?? 0;
  return { latest_turn: latest ?? null, turn_status_counts: { intent: count('turn', 'intent'), running: count('turn', 'running') }, request_status_counts: { pending: count('request', 'pending'), answering: count('request', 'answering') }, attempt_status_counts: { intent: count('attempt', 'intent'), unknown: count('attempt', 'unknown') } };
}
export function currentAttention(sql: ReadSql, id: string): CurrentAttentionKnowledge {
  const row = sql.get<Row>('SELECT p.runtime_epoch,p.conversation_revision,p.attention,i.runtime_epoch AS installed_epoch,i.installation_id,i.scope FROM navigation_signal_publications p JOIN navigation_signal_installation i ON i.singleton=1 WHERE p.conversation_id=?', [id]);
  if (!row || row.attention === null || row.runtime_epoch !== row.installed_epoch || !row.runtime_epoch || !row.installation_id || !row.scope) throw new Error('Signal attention lacks current complete coverage');
  if (!/^(0|[1-9][0-9]*)$/.test(row.conversation_revision)) throw new Error('revision must be a canonical decimal string');
  const current = JSON.parse(row.attention);
  if (!current || typeof current !== 'object' || Array.isArray(current) || Object.keys(current).sort().join(',') !== 'awaiting_answer,last_turn_failed,signal'
    || !current.signal || Object.keys(current.signal).sort().join(',') !== 'message_for_owner,working'
    || [current.signal.working, current.signal.message_for_owner, current.awaiting_answer, current.last_turn_failed].some(value => typeof value !== 'boolean')) throw new Error('Signal attention requires exact complete facts');
  return { current, durable: { message_for_owner: current.signal.message_for_owner, last_turn_failed: current.last_turn_failed } };
}
