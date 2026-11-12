import { PlannerError, type Principal } from '../../core/contracts.ts';
import { RETIRED_WORKER_TYPES, type WorkflowDefinition } from '../../worker_types/contracts.ts';
import { stage_definition } from './machine.ts';
export const REVISION_GUIDANCE_MAX_CHARACTERS = 10000;
export const TICKET_FIELDS_ONLY_FROM_ABOVE = new Set([
  'title',
  'project_id',
  'manager_id',
  'manager',
  'field_values',
  'ceiling',
  'ceiling_holder',
]);
export const TICKET_FIELD_THAT_MOVES_THE_PARENT = 'manager_id';
export function validate_manager(manager: Principal): void {
  if (!['chief', 'manager'].includes(manager.kind))
    throw new PlannerError('validation', 'manager must name Chief or a Manager');
}
export function require_creatable_worker_type(worker_type: string): void {
  if (RETIRED_WORKER_TYPES.has(worker_type))
    throw new PlannerError('validation', 'Worker type is retired');
}
export function validate_creation_ceiling(ceiling: string): void {
  if (ceiling === 'done')
    throw new PlannerError(
      'validation',
      'A new Ticket cannot use Done as its ceiling. Choose an earlier stage.',
    );
}
export function check_agent_proposal(
  stage: string,
  field: string,
  {
    worker_type_definition: d,
  }: {
    worker_type_definition: WorkflowDefinition;
  },
): void {
  const s = stage_definition(d, stage);
  if (s.is_terminal)
    throw new PlannerError('validation', 'no proposals on a terminal ticket', { stage });
  if (s.gating_field === null)
    throw new PlannerError('validation', 'ticket stage has no proposal field', { stage });
  if (field !== s.gating_field)
    throw new PlannerError('validation', 'agents may propose only the current gating field', {
      field,
      gating_field: s.gating_field,
      stage,
    });
}
export function validate_title(title: string, max_chars: number): void {
  if (!title) throw new PlannerError('validation', 'title must be non-empty');
  const length = Array.from(title).length;
  if (length > max_chars)
    throw new PlannerError('title_too_long', `title exceeds ${max_chars} characters`, {
      length,
      max: max_chars,
    });
}
export function validate_deadline(deadline: string | null): void {
  if (deadline === null) return;
  let valid = false;
  const calendar = /^(?:\d{8}|\d{4}-\d{2}-\d{2})$/.test(deadline);
  if (calendar) {
    const compact = deadline.replace(/-/g, ''),
      y = Number(compact.slice(0, 4)),
      m = Number(compact.slice(4, 6)),
      d = Number(compact.slice(6));
    const date = new Date(0);
    date.setUTCFullYear(y, m - 1, d);
    valid =
      y >= 1 &&
      date.getUTCFullYear() === y &&
      date.getUTCMonth() === m - 1 &&
      date.getUTCDate() === d;
  } else {
    const week = /^(?:(\d{4})-W(\d{2})(?:-([1-7]))?|(\d{4})W(\d{2})([1-7])?)$/.exec(deadline);
    if (week) {
      const y = Number(week[1] ?? week[4]),
        w = Number(week[2] ?? week[5]),
        day = Number(week[3] ?? week[6] ?? 1);
      const jan = new Date(0);
      jan.setUTCFullYear(y, 0, 1);
      const weekday = (jan.getUTCDay() + 6) % 7;
      const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
      const weeks = weekday === 3 || (weekday === 2 && leap) ? 53 : 52;
      const jan4 = new Date(0);
      jan4.setUTCFullYear(y, 0, 4);
      jan4.setUTCDate(4 - ((jan4.getUTCDay() + 6) % 7) + (w - 1) * 7 + day - 1);
      valid = y >= 1 && w >= 1 && w <= weeks && jan4.getUTCFullYear() <= 9999;
    }
  }
  if (!valid) throw new PlannerError('validation', 'deadline must be an ISO date', { deadline });
}
export function validate_body(body: string, what: string): void {
  if (!body) throw new PlannerError('validation', `${what} must be non-empty`);
}
export function validate_revision_guidance(message: string): void {
  validate_body(message, 'revision guidance');
  if (Array.from(message).length > 10000)
    throw new PlannerError('validation', 'revision guidance must be at most 10000 characters', {
      maximum_characters: 10000,
    });
}
