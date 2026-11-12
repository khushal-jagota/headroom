import { PlannerError } from '../core/contracts.ts';
import { pythonRepr } from '../core/config.ts';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type {
  FieldDefinition,
  StageDefinition,
  WorkerProfile,
  WorkerTypeDefinition,
  TicketWorkflowDefinition,
} from './contracts.ts';
import { validate_definition, validate_workflow } from './registry.ts';

/** json.dumps separators and Unicode handling are part of stored row parity. */
export function python_json_dumps(value: unknown, sort_keys = false, ensure_ascii = false): string {
  const encode = (item: unknown): string => {
    if (Array.isArray(item)) return '[' + item.map(encode).join(', ') + ']';
    if (item !== null && typeof item === 'object') {
      const record = item as Record<string, unknown>;
      const keys = Object.keys(record);
      if (sort_keys)
        keys.sort((a, b) => {
          const left = Array.from(a, (c) => c.codePointAt(0)!),
            right = Array.from(b, (c) => c.codePointAt(0)!);
          for (let i = 0; i < Math.min(left.length, right.length); i++)
            if (left[i] !== right[i]) return left[i] - right[i];
          return left.length - right.length;
        });
      return '{' + keys.map((k) => encode(k) + ': ' + encode(record[k])).join(', ') + '}';
    }
    const result = JSON.stringify(item);
    if (result === undefined) throw new TypeError('not JSON serializable');
    return ensure_ascii
      ? result.replace(
          /[\u007f-\uffff]/g,
          (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'),
        )
      : result;
  };
  return encode(value);
}
const fail = (message: string, detail: Record<string, unknown> = {}): never => {
  throw new PlannerError('validation', message, detail);
};
const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
function exact(value: unknown, keys: readonly string[], what: string): Record<string, unknown> {
  if (!record(value) || Object.keys(value).sort().join('\0') !== [...keys].sort().join('\0'))
    return fail(`stored worker type has a malformed ${what}`, {
      expected: [...keys].sort(),
      found: record(value) ? Object.keys(value).sort() : null,
    });
  return value;
}
function text(value: unknown, what: string): string {
  if (typeof value !== 'string' || !value || value !== value.trim())
    return fail(`stored worker type has a malformed ${what}`, { value: pythonRepr(value) });
  return value;
}
function stage(raw: unknown): StageDefinition {
  const payload = exact(
    record(raw) ? { instructions: '', ...raw } : raw,
    ['id', 'label', 'gating_field', 'is_terminal', 'ownership_mode', 'instructions'],
    'stage',
  );
  if (typeof payload.instructions !== 'string') return fail('stage instructions must be text');
  const gating_field =
    payload.gating_field === null ? null : text(payload.gating_field, 'stage gating field');
  if (typeof payload.is_terminal !== 'boolean')
    return fail('stored worker type has a malformed stage', { stage: payload.id });
  const ownership = payload.ownership_mode;
  if (
    ownership !== null &&
    ownership !== 'worker' &&
    ownership !== 'user' &&
    ownership !== 'paired'
  )
    return fail('stored worker type names an unknown ownership mode', {
      ownership_mode: pythonRepr(ownership),
    });
  return {
    id: text(payload.id, 'stage id'),
    label: text(payload.label, 'stage label'),
    gating_field,
    is_terminal: payload.is_terminal,
    ownership_mode: ownership,
    instructions: payload.instructions,
  };
}
function field(raw: unknown): FieldDefinition {
  const p = exact(raw, ['id', 'label'], 'field');
  return { id: text(p.id, 'field id'), label: text(p.label, 'field label') };
}
function profile(raw: unknown): WorkerProfile {
  const p = exact(
    raw,
    [
      'specialist_skill',
      'default_model',
      'default_reasoning_effort',
      'toolset_profile',
      'default_backend',
    ],
    'worker profile',
  );
  const reasoning =
    p.default_reasoning_effort === null
      ? null
      : text(p.default_reasoning_effort, 'reasoning effort');
  return {
    specialist_skill: text(p.specialist_skill, 'specialist skill'),
    default_model: text(p.default_model, 'default model'),
    default_reasoning_effort: reasoning,
    toolset_profile: text(p.toolset_profile, 'toolset profile'),
    default_backend: text(p.default_backend, 'default backend'),
  };
}
export function definition_to_json(definition: WorkerTypeDefinition): string {
  return python_json_dumps({
    worker_type: definition.worker_type,
    label: definition.label,
    guidance: definition.guidance,
    stages: definition.stages.map((s) => ({
      id: s.id,
      label: s.label,
      gating_field: s.gating_field,
      is_terminal: s.is_terminal,
      ownership_mode: s.ownership_mode,
      instructions: s.instructions,
    })),
    fields: definition.fields.map((f) => ({ id: f.id, label: f.label })),
    profile: {
      specialist_skill: definition.worker_profile.specialist_skill,
      default_model: definition.worker_profile.default_model,
      default_reasoning_effort: definition.worker_profile.default_reasoning_effort,
      toolset_profile: definition.worker_profile.toolset_profile,
      default_backend: definition.worker_profile.default_backend,
    },
  });
}
export function definition_from_json(raw: string): WorkerTypeDefinition {
  let decoded: unknown;
  try {
    decoded = JSON.parse(raw);
  } catch {
    return fail('stored worker type is not valid JSON');
  }
  const p = exact(
    record(decoded) ? { guidance: '', ...decoded } : decoded,
    ['worker_type', 'label', 'stages', 'fields', 'profile', 'guidance'],
    'record',
  );
  if (typeof p.guidance !== 'string') return fail('workflow guidance must be text');
  if (!Array.isArray(p.stages) || !Array.isArray(p.fields))
    return fail('stored worker type has malformed stages or fields');
  return {
    worker_type: text(p.worker_type, 'worker type id'),
    label: text(p.label, 'label'),
    stages: p.stages.map(stage),
    fields: p.fields.map(field),
    worker_profile: profile(p.profile),
    guidance: p.guidance,
  };
}
export function worker_types_table_exists(sql: ReadSql): boolean {
  return (
    sql.get("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'worker_types'") !==
    undefined
  );
}
export function read_definitions(sql: ReadSql): WorkerTypeDefinition[] {
  return sql
    .all<{ definition_json: string }>(
      'SELECT definition_json FROM worker_types ORDER BY position, worker_type',
    )
    .map((row) => definition_from_json(row.definition_json));
}
export function read_definition(sql: ReadSql, worker_type: string): WorkerTypeDefinition {
  const row = sql.get<{ definition_json: string }>(
    'SELECT definition_json FROM worker_types WHERE worker_type = ?',
    [worker_type],
  );
  if (!row) throw new PlannerError('not_found', 'unknown worker type', { worker_type });
  return definition_from_json(row.definition_json);
}
export function write_definition(
  tx: TransactionSql,
  definition: WorkerTypeDefinition,
  now: number,
): void {
  validate_definition(
    definition,
    new Set(
      tx
        .all<{ skill_name: string }>('SELECT skill_name FROM managed_skills ORDER BY skill_name')
        .map((r) => r.skill_name),
    ),
  );
  const position = tx.get<{ next: number }>(
    'SELECT COALESCE(MAX(position), -1) + 1 AS next FROM worker_types',
  )!.next;
  tx.run(
    'INSERT INTO worker_types (worker_type, position, definition_json, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(worker_type) DO UPDATE SET definition_json = excluded.definition_json, updated_at = excluded.updated_at',
    [definition.worker_type, position, definition_to_json(definition), now],
  );
}
export function delete_definition(tx: TransactionSql, worker_type: string): void {
  read_definition(tx, worker_type);
  const ticket_ids = tx
    .all<{ id: string }>(
      "SELECT id FROM tickets WHERE worker_type = ? AND stage != 'done' AND employee_launch_model IS NULL ORDER BY id",
      [worker_type],
    )
    .map((r) => r.id);
  if (ticket_ids.length)
    fail(
      'unfinished Tickets of this Worker type have no saved model and launch from its defaults',
      { worker_type, ticket_ids },
    );
  tx.run('DELETE FROM worker_types WHERE worker_type = ?', [worker_type]);
}
export function workflow_from_preset(definition: WorkerTypeDefinition): TicketWorkflowDefinition {
  return {
    label: definition.label,
    stages: definition.stages,
    fields: definition.fields,
    guidance: definition.guidance,
    specialist_skill: definition.worker_profile.specialist_skill,
  };
}
export function workflow_to_json(definition: TicketWorkflowDefinition): string {
  return python_json_dumps({
    label: definition.label,
    guidance: definition.guidance,
    stages: definition.stages,
    fields: definition.fields,
    specialist_skill: definition.specialist_skill,
  });
}
function workflow_from_dict(raw: unknown, saved: boolean): TicketWorkflowDefinition {
  const p = exact(
    raw,
    ['label', 'guidance', 'stages', 'fields', ...(saved ? ['specialist_skill'] : [])],
    'Ticket workflow',
  );
  if (typeof p.guidance !== 'string') return fail('workflow guidance must be text');
  if (!Array.isArray(p.stages) || !Array.isArray(p.fields))
    return fail('workflow stages and fields must be arrays');
  const specialist_skill =
    p.specialist_skill == null ? null : text(p.specialist_skill, 'specialist skill');
  if (saved && p.stages.some((s) => !record(s) || !('instructions' in s)))
    return fail('saved workflow stage lacks instructions');
  const result = {
    label: text(p.label, 'workflow label'),
    guidance: p.guidance,
    stages: p.stages.map(stage),
    fields: p.fields.map(field),
    specialist_skill,
  };
  validate_workflow(result);
  return result;
}
export function custom_workflow_from_dict(raw: unknown): TicketWorkflowDefinition {
  return workflow_from_dict(raw, false);
}
export function workflow_from_json(raw: string): TicketWorkflowDefinition {
  let p: unknown;
  try {
    p = JSON.parse(raw);
  } catch {
    return fail('saved Ticket workflow is not valid JSON');
  }
  return workflow_from_dict(p, true);
}
export function workflow_from_row(row: { workflow_json: string }): TicketWorkflowDefinition {
  return workflow_from_json(row.workflow_json);
}
