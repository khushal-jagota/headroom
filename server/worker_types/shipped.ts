import { readFileSync } from 'node:fs';
import type { TransactionSql } from '../core/database/contracts.ts';
import { definition_from_json, definition_to_json } from './store.ts';
import { convert_existing_skill } from './guidance.ts';
export const SHIPPED_WORKER_TYPES_FILE = new URL(
  '../../src/planner/worker_types/shipped_worker_types.json',
  import.meta.url,
);
export const CHIEF_EMPLOYEE_ID = 'chief_of_staff';
export const CHIEF_LABEL = 'Chief of Staff';
export const DEFAULT_CHIEF_LAUNCH = {
  employee_backend: 'codex',
  employee_launch_model: 'gpt-6.1-sol',
  employee_launch_reasoning_effort: 'medium',
};
export function shipped_worker_types(): unknown[] {
  return JSON.parse(readFileSync(SHIPPED_WORKER_TYPES_FILE, 'utf8')) as unknown[];
}
export function seed_shipped_worker_types(tx: TransactionSql): void {
  if (tx.get('SELECT 1 FROM worker_types LIMIT 1') !== undefined) return;
  shipped_worker_types().forEach((raw, position) => {
    const decoded = definition_from_json(JSON.stringify(raw));
    const source = tx.get<{ source_text: string }>(
      'SELECT source_text FROM managed_skills WHERE skill_name = ?',
      [decoded.worker_profile.specialist_skill],
    );
    if (!source) throw new Error(`missing seed skill: ${decoded.worker_profile.specialist_skill}`);
    const converted = convert_existing_skill(decoded, source.source_text);
    tx.run(
      'INSERT INTO worker_types (worker_type, position, definition_json, updated_at) VALUES (?, ?, ?, 0)',
      [converted.worker_type, position, definition_to_json(converted)],
    );
  });
}
export function seed_chief_settings(tx: TransactionSql): void {
  if (tx.get('SELECT 1 FROM chief_settings LIMIT 1') !== undefined) return;
  tx.run(
    'INSERT INTO chief_settings (employee_id, label, employee_backend, employee_launch_model, employee_launch_reasoning_effort) VALUES (?, ?, ?, ?, ?)',
    [
      CHIEF_EMPLOYEE_ID,
      CHIEF_LABEL,
      DEFAULT_CHIEF_LAUNCH.employee_backend,
      DEFAULT_CHIEF_LAUNCH.employee_launch_model,
      DEFAULT_CHIEF_LAUNCH.employee_launch_reasoning_effort,
    ],
  );
}
