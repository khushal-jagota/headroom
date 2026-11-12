import { PlannerError } from '../core/contracts.ts';
import type { Database, ReadSql } from '../core/database/contracts.ts';
import { validate_launch_defaults } from './logic/launch_defaults.ts';
export { validate_launch_defaults } from './logic/launch_defaults.ts';
import type { WorkerProfile, WorkerTypeDefinition } from '../worker_types/contracts.ts';
import type { WorkerTypeRegistry } from '../worker_types/registry.ts';
import { configured_worker_type_registry, load_worker_runtime_definitions } from '../worker_types/configuration.ts';
import { read_definition, write_definition, delete_definition } from '../worker_types/store.ts';
import { render_workflow_guide } from '../worker_types/guidance.ts';
import { read_skill, save_skill, normalize_skill_patch, parse_skill, publish_skill, with_skill_publication_lock } from '../skills/service.ts';
import { has_skill, read_skill_source, write_skill_source } from '../skills/data.ts';
import { render_skill_from_existing_frontmatter, render_new_skill } from '../skills/logic.ts';
import type { ManagedChiefSettings, ManagedWorkerLaunchDefaults, ManagedWorkerSettings, WorkerManagementSummary, WorkerManagementDetail } from './contracts.ts';
export const CHIEF_SETTINGS_KEY = 'chief_of_staff';
export const CHIEF_SKILL_NAME = 'panels-chief-of-staff';
const fail = (message: string, detail: Record<string, unknown> = {}): never => { throw new PlannerError('validation', message, detail); };
export function resolve_launch_defaults(sql: ReadSql, raw: unknown): ManagedWorkerLaunchDefaults {
  const defaults = validate_launch_defaults(raw);
  const row = sql.get<{ enabled: number }>('SELECT enabled FROM backend_model_enablement WHERE backend_key = ? AND model_id = ?', [defaults.employee_backend, defaults.employee_launch_model]);
  if (row && !row.enabled) fail(`Model "${defaults.employee_launch_model}" is disabled. Choose an enabled model.`, { employee_backend: defaults.employee_backend, employee_launch_model: defaults.employee_launch_model });
  return defaults;
}
export function launch_defaults(profile: WorkerProfile): ManagedWorkerLaunchDefaults {
  return { employee_backend: profile.default_backend, employee_launch_model: profile.default_model, employee_launch_reasoning_effort: profile.default_reasoning_effort };
}
export function read_worker_settings(sql: ReadSql, registry: WorkerTypeRegistry, worker_type: string): ManagedWorkerSettings {
  const definition = registry.require(worker_type);
  return { worker_type, specialist_skill: read_skill(sql, definition.worker_profile.specialist_skill), launch_defaults: launch_defaults(definition.worker_profile) };
}
export function read_worker_management_index(registry: WorkerTypeRegistry): WorkerManagementSummary[] {
  return registry.supported_worker_types().map(worker_type => {
    const definition = registry.require(worker_type);
    return { worker_type, label: definition.label, specialist_skill_name: definition.worker_profile.specialist_skill, launch_defaults: launch_defaults(definition.worker_profile) };
  });
}
export function read_worker_management_detail(sql: ReadSql, registry: WorkerTypeRegistry, worker_type: string): WorkerManagementDetail {
  return { manifest: registry.manifest(worker_type), settings: read_worker_settings(sql, registry, worker_type) };
}
export function read_worker_launch_defaults_for_ticket_creation(registry: WorkerTypeRegistry, worker_type: string): ManagedWorkerLaunchDefaults { return launch_defaults(registry.require(worker_type).worker_profile); }
export function update_worker_launch_defaults(database: Database, worker_type: string, payload: unknown, now: number): ManagedWorkerSettings {
  const defaults = validate_launch_defaults(payload);
  database.write(tx => {
    const definition = read_definition(tx, worker_type);
    write_definition(tx, { ...definition, worker_profile: { ...definition.worker_profile, default_backend: defaults.employee_backend, default_model: defaults.employee_launch_model, default_reasoning_effort: defaults.employee_launch_reasoning_effort } }, now);
  });
  database.read(load_worker_runtime_definitions);
  return database.read(sql => read_worker_settings(sql, configured_worker_type_registry(), worker_type));
}
export function read_chief_settings(sql: ReadSql): ManagedChiefSettings {
  const row = sql.get<{ label: string; employee_backend: string; employee_launch_model: string; employee_launch_reasoning_effort: string | null; recap: string }>('SELECT label, employee_backend, employee_launch_model, employee_launch_reasoning_effort, recap FROM chief_settings WHERE employee_id = ?', [CHIEF_SETTINGS_KEY]);
  if (!row) fail('managed Chief settings are invalid');
  return { employee_id: CHIEF_SETTINGS_KEY, label: row!.label, recap: row!.recap, skill: read_skill(sql, CHIEF_SKILL_NAME), launch_defaults: { employee_backend: row!.employee_backend, employee_launch_model: row!.employee_launch_model, employee_launch_reasoning_effort: row!.employee_launch_reasoning_effort } };
}
export function update_chief_recap(database: Database, payload: unknown): ManagedChiefSettings {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) || Object.keys(payload).join(',') !== 'recap' || typeof (payload as Record<string, unknown>).recap !== 'string') fail('Chief recap requires exactly one string recap');
  return database.write(tx => { tx.run('UPDATE chief_settings SET recap = ? WHERE employee_id = ?', [(payload as { recap: string }).recap, CHIEF_SETTINGS_KEY]); return read_chief_settings(tx); });
}
export function update_chief_launch_defaults(database: Database, payload: unknown): ManagedChiefSettings {
  const d = validate_launch_defaults(payload);
  return database.write(tx => { tx.run('UPDATE chief_settings SET employee_backend = ?, employee_launch_model = ?, employee_launch_reasoning_effort = ? WHERE employee_id = ?', [d.employee_backend, d.employee_launch_model, d.employee_launch_reasoning_effort, CHIEF_SETTINGS_KEY]); return read_chief_settings(tx); });
}
export function save_worker_type_definition(database: Database, definition: WorkerTypeDefinition, skill: Record<string, unknown> | null, options: { now: number; database_parent: string }): void {
  with_skill_publication_lock(options.database_parent, root => {
    const name = definition.worker_profile.specialist_skill;
    const rendered = database.write(tx => {
      const patch = skill === null ? null : normalize_skill_patch(name, skill);
      const body = render_workflow_guide(definition);
      if (patch && 'markdown_body' in patch && patch.markdown_body !== body) fail('specialist body is generated from the structured workflow', { field: 'markdown_body' });
      const description = patch ? patch.description ?? definition.label : has_skill(tx, name) ? read_skill(tx, name).description : definition.label;
      const text = has_skill(tx, name) ? render_skill_from_existing_frontmatter(read_skill_source(tx, name), { expected_skill_name: name, description, markdown_body: body }) : render_new_skill(name, description, body);
      parse_skill(text, name);
      write_skill_source(tx, name, text, { now: options.now, generated_from_workflow: true });
      write_definition(tx, definition, options.now);
      return text;
    });
    database.read(load_worker_runtime_definitions);
    try { publish_skill(database, root, name, rendered); }
    catch (error) {
      if (error instanceof PlannerError && error.code === 'skill_save_incomplete') throw new PlannerError(error.code, error.message, { ...error.detail, worker_type: definition.worker_type, definition_saved: true });
      throw error;
    }
  });
}
export function delete_worker_type_definition(database: Database, worker_type: string): void { database.write(tx => delete_definition(tx, worker_type)); database.read(load_worker_runtime_definitions); }
export function save_specialist_skill(database: Database, worker_type: string, payload: Record<string, unknown>, options: { now: number; database_parent: string }): ManagedWorkerSettings {
  const registry = configured_worker_type_registry();
  save_skill(database, registry.require(worker_type).worker_profile.specialist_skill, payload, options);
  return database.read(sql => read_worker_settings(sql, registry, worker_type));
}
export function save_chief_skill(database: Database, payload: Record<string, unknown>, options: { now: number; database_parent: string }): ManagedChiefSettings { save_skill(database, CHIEF_SKILL_NAME, payload, options); return database.read(read_chief_settings); }
