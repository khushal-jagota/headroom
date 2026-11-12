/** Pure conversion of consumed Codex items into Panels values. */
import { JsonNumber, numeric_kind, numeric_value } from '../../../core/http/numeric_json.ts';
import { PlanEntry, PlanEntryStatus, ToolCallStatus } from '../../events.ts';
import type * as P from './protocol.ts';
import { TOOL_CALL_DETAIL_MAXIMUM_CHARACTERS, TOOL_CALL_TITLE_MAXIMUM_CHARACTERS } from './adapter_configuration.ts';

export function shortened(text: string, limit: number): string {
  const collapsed = text.trim();
  const chars = Array.from(collapsed);
  return chars.length <= limit ? collapsed : chars.slice(0, limit).join('') + '…';
}
export function plan_entries(plan: readonly P.TurnPlanStep[]): readonly PlanEntry[] {
  const statuses = { pending: PlanEntryStatus.pending, inProgress: PlanEntryStatus.in_progress, completed: PlanEntryStatus.completed };
  return plan.map((step) => new PlanEntry({ text: step.step, status: statuses[step.status] }));
}
function file_detail(item: P.FileChangeThreadItem): string {
  return shortened(item.changes.map((change) => `${change.kind.type} ${change.path}`).join('\n'), TOOL_CALL_DETAIL_MAXIMUM_CHARACTERS);
}
export function tool_call_started(item: P.ThreadItem): { id: string; title: string; tool_kind: string; detail: string | null } | null {
  if (item.type === 'commandExecution') {
    const given = item as P.CommandExecutionThreadItem;
    return { id: given.id, title: shortened(given.command, TOOL_CALL_TITLE_MAXIMUM_CHARACTERS), tool_kind: 'execute', detail: given.cwd };
  }
  if (item.type === 'fileChange') {
    const given = item as P.FileChangeThreadItem;
    return { id: given.id, title: given.changes.length === 1 ? `Change ${given.changes[0]!.path}` : `Change ${given.changes.length} files`, tool_kind: 'edit', detail: file_detail(given) };
  }
  if (item.type === 'mcpToolCall') {
    const given = item as P.McpToolCallThreadItem;
    return { id: given.id, title: `${given.server}/${given.tool}`, tool_kind: 'other', detail: null };
  }
  return null;
}
export function tool_call_finished(item: P.ThreadItem): { id: string; tool_call_status: ToolCallStatus; detail: string | null } | null {
  if (!['commandExecution', 'fileChange', 'mcpToolCall'].includes(item.type)) return null;
  const given = item as P.CommandExecutionThreadItem | P.FileChangeThreadItem | P.McpToolCallThreadItem;
  if (given.status === 'inProgress') return null;
  const status = given.status === 'completed' ? ToolCallStatus.completed : ToolCallStatus.failed;
  const detail = given.type === 'commandExecution'
    ? given.aggregatedOutput === null ? null : shortened(given.aggregatedOutput, TOOL_CALL_DETAIL_MAXIMUM_CHARACTERS)
    : given.type === 'fileChange' ? file_detail(given) : given.error?.message ?? null;
  return { id: given.id, tool_call_status: status, detail };
}
const SECRET_ASSIGNMENT = /(["']?\b(?:authorization|password|api[ -]key|(?:[a-z0-9]+[_-])*(?:api[_-]?key|authorization|password|secret|token)(?:[_-][a-z0-9]+)*)\b["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:bearer\s+)?[^,;}]+)/gi;
const BEARER_VALUE = /\bbearer\s+(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\S+)/gi;
export function normalized_failure_summary(detail: string | null, http_status: number | null = null): string {
  if (http_status === 401) return 'authentication was rejected (HTTP 401)';
  let text = (detail ?? '').split(/\s+/).filter(Boolean).join(' ');
  text = text.replace(SECRET_ASSIGNMENT, '$1[redacted]').replace(BEARER_VALUE, 'Bearer [redacted]');
  if (!text) return http_status === null ? 'the turn failed for an unknown reason' : `the provider request failed (HTTP ${http_status})`;
  if (http_status !== null && !text.includes(`HTTP ${http_status}`)) text += ` (HTTP ${http_status})`;
  return Array.from(text).length > 240 ? Array.from(text).slice(0, 239).join('').trimEnd() + '…' : text;
}
function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) && !(value instanceof JsonNumber);
}
export function error_summary(error: P.TurnError | null): string {
  if (error === null) return normalized_failure_summary(null);
  const info = error.codexErrorInfo;
  if (info === 'unauthorized') return normalized_failure_summary(error.message, 401);
  let http_status: number | null = null;
  for (const key of ['httpConnectionFailed', 'responseStreamConnectionFailed', 'responseStreamDisconnected', 'responseTooManyFailedAttempts']) {
    const variant = object(info) ? info[key] : null;
    if (object(variant)) {
      if (numeric_kind(variant.httpStatusCode) === 'integer') http_status = numeric_value(variant.httpStatusCode) ?? null;
      break;
    }
  }
  return normalized_failure_summary(error.additionalDetails ? `${error.message}: ${error.additionalDetails}` : error.message, http_status);
}
export function goal_inspection_message(goal: P.ThreadGoal | null): string {
  if (goal === null) return 'No goal is set.';
  return `Goal: ${goal.objective} (${goal.status}, ${goal.tokensUsed}${goal.tokenBudget === null ? '' : '/' + goal.tokenBudget} tokens, ${goal.timeUsedSeconds}s)`;
}
