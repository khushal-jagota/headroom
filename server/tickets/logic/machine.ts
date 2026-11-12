import { PlannerError } from '../../core/contracts.ts';
import type { WorkflowDefinition } from '../../worker_types/contracts.ts';
export function stage_definition(definition: WorkflowDefinition, stage: string) {
  const value = definition.stages.find((s) => s.id === stage);
  if (!value) throw new PlannerError('validation', 'stage outside the linear order', { stage });
  return value;
}
export function stage_index(definition: WorkflowDefinition, stage: string): number {
  stage_definition(definition, stage);
  return definition.stages.findIndex((s) => s.id === stage);
}
export function gating_field(definition: WorkflowDefinition, stage: string) {
  return stage_definition(definition, stage).gating_field;
}
export function advance_target(definition: WorkflowDefinition, stage: string): string | null {
  if (stage_definition(definition, stage).is_terminal) return null;
  const index = stage_index(definition, stage);
  return definition.stages[index + 1]?.id ?? null;
}
export function field_is_passed(
  field: string,
  stage: string,
  {
    worker_type_definition: d,
  }: {
    worker_type_definition: WorkflowDefinition;
  },
): boolean {
  const gate = d.stages.find((s) => s.gating_field === field);
  if (!gate) throw new PlannerError('validation', 'field gates no stage', { field });
  return stage_index(d, stage) > stage_index(d, gate.id);
}
export function at_or_beyond_ceiling(
  stage: string,
  ceiling: string,
  {
    worker_type_definition: d,
  }: {
    worker_type_definition: WorkflowDefinition;
  },
): boolean {
  return stage_index(d, stage) >= stage_index(d, ceiling);
}
export function resolve_next_ceiling(
  new_stage: string,
  next_ceiling: string | null,
  {
    worker_type_definition: d,
  }: {
    worker_type_definition: WorkflowDefinition;
  },
): string {
  if (next_ceiling === null)
    throw new PlannerError('scope_missing', 'accept requires the onward ceiling', {
      missing: ['next_ceiling'],
    });
  if (next_ceiling === 'none') return new_stage;
  if (!d.stages.some((s) => s.id === next_ceiling))
    throw new PlannerError('scope_invalid', 'unknown next_ceiling', { next_ceiling });
  if (stage_index(d, next_ceiling) < stage_index(d, new_stage))
    throw new PlannerError('scope_invalid', 'next_ceiling must be at or beyond the new stage', {
      next_ceiling,
      new_stage,
    });
  return next_ceiling;
}
export function stage_ownership_mode(
  stage: string,
  {
    worker_type_definition: d,
  }: {
    worker_type_definition: WorkflowDefinition;
  },
) {
  const s = stage_definition(d, stage);
  if (s.is_terminal) return null;
  if (s.ownership_mode === null)
    throw new PlannerError('validation', 'non-terminal stage has no ownership mode', { stage });
  return s.ownership_mode;
}
export function resolve_ceiling(d: WorkflowDefinition, raw: string): string {
  if (d.stages.some((s) => s.id === raw)) return raw;
  const gate = d.stages.find((s) => s.gating_field === raw);
  if (gate) return gate.id;
  throw new PlannerError('scope_invalid', 'unknown ceiling', { ceiling: raw });
}
export function default_ceiling(d: WorkflowDefinition): string {
  return d.stages[0]!.id;
}
export function validate_ticket_position(
  d: WorkflowDefinition,
  stage: string,
  ceiling: string,
  allow_historical_terminal_ceiling = false,
): void {
  const s = stage_definition(d, stage);
  if (allow_historical_terminal_ceiling && s.is_terminal) return;
  if (!d.stages.some((s) => s.id === ceiling))
    throw new PlannerError('scope_invalid', "ceiling outside the type's range", {
      workflow: d.label,
      ceiling,
    });
}
