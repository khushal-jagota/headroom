import { PlannerError, type Principal } from '../../core/contracts.ts';
import type { WorkflowDefinition } from '../../worker_types/contracts.ts';
import { python_json_dumps } from '../../worker_types/store.ts';
import type {
  PendingTicketProposal,
  TicketFieldApproval,
  TicketFieldApprovals,
  TicketFieldValues,
} from '../contracts.ts';
import { gating_field } from './machine.ts';
const object = (v: unknown): v is Record<string, unknown> =>
  v !== null && typeof v === 'object' && !Array.isArray(v);
function require(condition: unknown): asserts condition {
  if (!condition) throw new PlannerError('validation', 'corrupt ticket field state');
}
const exact = (v: Record<string, unknown>, keys: string[]) =>
  Object.keys(v).sort().join('\0') === keys.sort().join('\0');
export function values_to_json(values: TicketFieldValues): string {
  return python_json_dumps(values);
}
export function stored_values_from_json(raw: string): TicketFieldValues {
  const v: unknown = JSON.parse(raw);
  require(object(v) && Object.values(v).every((b) => typeof b === 'string'));
  return v as TicketFieldValues;
}
export function values_from_json(raw: string, field_ids: readonly string[]): TicketFieldValues {
  const v = stored_values_from_json(raw);
  return Object.fromEntries(field_ids.filter((k) => k in v).map((k) => [k, v[k]!]));
}
export function replace_declared_values(
  raw: string,
  values: TicketFieldValues,
  field_ids: readonly string[],
): string {
  require(Object.entries(values).every(([k, b]) => field_ids.includes(k) && typeof b === 'string'));
  return values_to_json({
    ...Object.fromEntries(
      Object.entries(stored_values_from_json(raw)).filter(([k]) => !field_ids.includes(k)),
    ),
    ...values,
  });
}
export function proposal_to_json(proposal: PendingTicketProposal | null): string | null {
  return proposal === null ? null : python_json_dumps(proposal);
}
export function proposal_from_json(raw: string | null): PendingTicketProposal | null {
  if (raw === null) return null;
  const v: unknown = JSON.parse(raw);
  require(object(v) && exact(v, ['field', 'body', 'proposed_by', 'created_at']));
  require(
    ['field', 'body', 'proposed_by'].every((k) => typeof v[k] === 'string') &&
      Number.isInteger(v.created_at),
  );
  return v as unknown as PendingTicketProposal;
}
export function validate_state(
  values: TicketFieldValues,
  proposal: PendingTicketProposal | null,
  stage: string,
  {
    worker_type_definition: d,
  }: {
    worker_type_definition: WorkflowDefinition;
  },
): void {
  require(
    Object.entries(values).every(
      ([k, b]) => d.fields.some((f) => f.id === k) && typeof b === 'string',
    ),
  );
  if (proposal !== null)
    require(
      proposal.field === gating_field(d, stage) &&
        typeof proposal.body === 'string' &&
        typeof proposal.proposed_by === 'string' &&
        Number.isInteger(proposal.created_at),
    );
}
export function field_value(
  values: TicketFieldValues,
  field: string,
  {
    worker_type_definition: d,
  }: {
    worker_type_definition: WorkflowDefinition;
  },
): string | null {
  if (!d.fields.some((f) => f.id === field))
    throw new PlannerError('validation', 'unknown ticket field', { field });
  return values[field] ?? null;
}
export function approvals_to_json(approvals: TicketFieldApprovals): string {
  return python_json_dumps(approvals);
}
export function approvals_from_json(raw: string, values: TicketFieldValues): TicketFieldApprovals {
  const obj: unknown = JSON.parse(raw);
  require(object(obj));
  const approvals: TicketFieldApprovals = {};
  for (const [key, item] of Object.entries(obj)) {
    require(object(item) && exact(item, ['method', 'principal', 'display_label']));
    require(['explicit_accept', 'auto_accept', 'unknown'].includes(String(item.method)));
    let principal: Principal | null = null;
    if (item.principal !== null) {
      require(object(item.principal) && exact(item.principal, ['kind', 'id']));
      require(
        typeof item.principal.id === 'string' &&
          ['owner', 'chief', 'manager', 'ticket', 'goal'].includes(String(item.principal.kind)),
      );
      principal = item.principal as unknown as Principal;
    }
    require(
      typeof item.display_label === 'string' &&
        (item.method === 'explicit_accept') === (principal !== null),
    );
    approvals[key] = {
      method: item.method,
      principal,
      display_label: item.display_label,
    } as TicketFieldApproval;
  }
  return Object.fromEntries(
    Object.keys(values).map((k) => [
      k,
      approvals[k] ?? { method: 'unknown', principal: null, display_label: 'Approval unknown' },
    ]),
  );
}
export function replace_declared_approvals(
  raw: string,
  approvals: TicketFieldApprovals,
  field_ids: readonly string[],
): string {
  return python_json_dumps({
    ...Object.fromEntries(
      Object.entries(JSON.parse(raw) as Record<string, unknown>).filter(
        ([k]) => !field_ids.includes(k),
      ),
    ),
    ...approvals,
  });
}
