import { PlannerError } from '../core/contracts.ts';
import { require_conversation_backend_key } from '../conversation/contracts.ts';
import { RETIRED_WORKER_TYPES } from './contracts.ts';
import type {
  WorkflowDefinition,
  WorkerTypeDefinition,
  WorkflowManifest,
  WorkerTypeManifest,
} from './contracts.ts';
export const KNOWN_TOOLSET_PROFILES: ReadonlySet<string> = new Set(['default']);
export function validate_workflow(definition: WorkflowDefinition): void {
  const worker_type = definition.label;
  const fail = (message: string, detail: Record<string, unknown> = {}): never => {
    throw new PlannerError('validation', message, detail);
  };
  if (typeof definition.guidance !== 'string') fail('workflow guidance must be text');
  if (typeof definition.label !== 'string' || !definition.label.trim())
    fail('workflow label must be non-empty text');
  if (definition.stages.some((s) => typeof s.instructions !== 'string'))
    fail('stage instructions must be text');
  if (!definition.stages.length) fail('definition has no stages', { worker_type });
  if (!definition.fields.length) fail('definition has no fields', { worker_type });
  const seenStages = new Set<string>();
  for (const stage of definition.stages) {
    if (seenStages.has(stage.id)) fail('duplicate stage id', { worker_type, stage: stage.id });
    seenStages.add(stage.id);
  }
  const seenFields = new Set<string>();
  for (const field of definition.fields) {
    if (seenFields.has(field.id)) fail('duplicate field id', { worker_type, field: field.id });
    seenFields.add(field.id);
  }
  const first = definition.stages[0],
    last = definition.stages.at(-1)!;
  const briefStages = definition.stages.flatMap((s, i) => (s.id === 'needs_brief' ? [i] : []));
  const briefFields = definition.fields.flatMap((f, i) => (f.id === 'brief' ? [i] : []));
  if (
    (briefStages.length && (briefStages.length !== 1 || briefStages[0] !== 0)) ||
    (briefFields.length && (briefFields.length !== 1 || briefFields[0] !== 0)) ||
    !!briefStages.length !== !!briefFields.length ||
    (briefStages.length && first.gating_field !== 'brief')
  )
    fail('brief stage and field must be paired first', { worker_type });
  const terminals = definition.stages.filter((s) => s.is_terminal === true);
  if (terminals.length !== 1 || terminals[0] !== last)
    fail('linear order must have exactly one terminal', {
      worker_type,
      stage: terminals[0]?.id ?? last.id,
    });
  if (last.id !== 'done') fail('last stage must be done', { worker_type, last: last.id });
  for (const stage of definition.stages) {
    if (stage.id === 'cancelled')
      fail('cancelled is not a stage', { worker_type, stage: stage.id });
    if (stage.id === 'dropped') fail('dropped is not a stage', { worker_type, stage: 'dropped' });
  }
  for (const stage of definition.stages.slice(0, -1))
    if (stage.id === 'done') fail('done may not be a mid stage', { worker_type, stage: 'done' });
  if (last.gating_field !== null)
    fail('terminal stage may not gate a field', { worker_type, stage: 'done' });
  for (const stage of definition.stages) {
    if (!stage.is_terminal && stage.gating_field === null)
      fail('non-terminal stage must gate a field', { worker_type, stage: stage.id });
    if (!stage.is_terminal && stage.ownership_mode === null)
      fail('non-terminal stage must declare ownership', { worker_type, stage: stage.id });
    if (stage.is_terminal && stage.ownership_mode !== null)
      fail('terminal stage may not declare ownership', { worker_type, stage: stage.id });
    if (
      stage.ownership_mode !== null &&
      !['worker', 'user', 'paired'].includes(stage.ownership_mode)
    )
      fail('stage ownership must be a known mode', { worker_type, stage: stage.id });
  }
  for (const stage of definition.stages)
    if (!stage.is_terminal && !seenFields.has(stage.gating_field!))
      fail('gating field references an undeclared field', {
        worker_type,
        stage: stage.id,
        gating_field: stage.gating_field,
      });
  const counts = new Map<string, number>();
  for (const stage of definition.stages)
    if (!stage.is_terminal && stage.gating_field !== null)
      counts.set(stage.gating_field, (counts.get(stage.gating_field) ?? 0) + 1);
  for (const stage of definition.stages)
    if (!stage.is_terminal && stage.gating_field !== null && counts.get(stage.gating_field)! > 1)
      fail('field gated by more than one stage', { worker_type, field: stage.gating_field });
  for (const field of definition.fields)
    if (!counts.get(field.id))
      fail('declared field is never gated', { worker_type, field: field.id });
  if (!seenFields.has('consequences'))
    fail('every worker type must declare a consequences field', {
      worker_type,
      field: 'consequences',
    });
}
export function validate_definition(
  definition: WorkerTypeDefinition,
  known_skills: ReadonlySet<string>,
  known_toolset_profiles: ReadonlySet<string> = KNOWN_TOOLSET_PROFILES,
): void {
  validate_workflow(definition);
  const worker_type = definition.worker_type,
    profile = definition.worker_profile;
  if (!known_skills.has(profile.specialist_skill))
    throw new PlannerError('validation', 'worker profile references an unknown skill', {
      worker_type,
      specialist_skill: profile.specialist_skill,
    });
  if (!known_toolset_profiles.has(profile.toolset_profile))
    throw new PlannerError('validation', 'worker profile references an unknown toolset profile', {
      worker_type,
      toolset_profile: profile.toolset_profile,
    });
  require_conversation_backend_key(profile.default_backend);
  if (typeof profile.default_model !== 'string' || !profile.default_model.trim())
    throw new PlannerError('validation', 'default_model must be a non-empty string', {
      worker_type,
      default_model: profile.default_model,
    });
  if (
    profile.default_reasoning_effort !== null &&
    (typeof profile.default_reasoning_effort !== 'string' ||
      !profile.default_reasoning_effort.trim())
  )
    throw new PlannerError(
      'validation',
      'default_reasoning_effort must be null or a non-empty string',
      { worker_type, default_reasoning_effort: profile.default_reasoning_effort },
    );
}
export function workflow_manifest(
  definition: WorkflowDefinition,
  worker_type: string | null = null,
  specialist_skill: string | null = null,
): WorkflowManifest {
  return {
    worker_type,
    label: definition.label,
    guidance: definition.guidance,
    stages: definition.stages.map((s) => ({ ...s })),
    fields: definition.fields.map((f) => ({ ...f })),
    advance: Object.fromEntries(
      definition.stages.flatMap((s, i) =>
        s.is_terminal ? [] : [[s.id, definition.stages[i + 1].id]],
      ),
    ),
    ceiling_range: definition.stages.map((s) => s.id),
    default_ceiling: definition.stages[0].id,
    worker_profile_id: specialist_skill,
  };
}
export class WorkerTypeRegistry {
  private readonly definitions: ReadonlyMap<string, WorkerTypeDefinition>;
  constructor(
    definitions: Iterable<WorkerTypeDefinition>,
    known_skills: ReadonlySet<string>,
    known_toolset_profiles: ReadonlySet<string> = KNOWN_TOOLSET_PROFILES,
  ) {
    const table = new Map<string, WorkerTypeDefinition>();
    for (const definition of definitions) {
      if (table.has(definition.worker_type))
        throw new PlannerError('validation', 'duplicate worker type id', {
          worker_type: definition.worker_type,
        });
      validate_definition(definition, known_skills, known_toolset_profiles);
      // The configured registry is immutable, just like Python's frozen declarations.
      const value = structuredClone(definition);
      value.stages.forEach(Object.freeze);
      value.fields.forEach(Object.freeze);
      Object.freeze(value.stages);
      Object.freeze(value.fields);
      Object.freeze(value.worker_profile);
      Object.freeze(value);
      table.set(value.worker_type, value);
    }
    this.definitions = table;
  }
  registered_worker_types(): string[] {
    return [...this.definitions.keys()];
  }
  supported_worker_types(): string[] {
    return this.registered_worker_types().filter((t) => !RETIRED_WORKER_TYPES.has(t));
  }
  require(worker_type: string): WorkerTypeDefinition {
    const definition = this.definitions.get(worker_type);
    if (!definition) throw new PlannerError('not_found', 'unknown worker type', { worker_type });
    return definition;
  }
  manifest(worker_type: string): WorkerTypeManifest {
    const definition = this.require(worker_type),
      profile = definition.worker_profile;
    return {
      ...workflow_manifest(definition, worker_type, profile.specialist_skill),
      worker_type,
      worker_profile_id: profile.specialist_skill,
      creatable: !RETIRED_WORKER_TYPES.has(worker_type),
      default_backend: profile.default_backend,
      default_model: profile.default_model,
      default_reasoning_effort: profile.default_reasoning_effort,
    };
  }
}
