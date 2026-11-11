import { PlannerError } from '../../core/contracts.ts';
import { require_conversation_backend_key } from '../../conversation/contracts.ts';
import type { ManagedWorkerLaunchDefaults } from '../contracts.ts';
const fail = (message: string, detail: Record<string, unknown> = {}): never => { throw new PlannerError('validation', message, detail); };
export function validate_launch_defaults(raw: unknown): ManagedWorkerLaunchDefaults {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw) || Object.keys(raw).sort().join(',') !== 'employee_backend,employee_launch_model,employee_launch_reasoning_effort') fail('employee launch defaults must be an exact object');
  const value = raw as Record<string, unknown>;
  if (typeof value.employee_backend !== 'string') fail('employee backend must be a string');
  const backend = require_conversation_backend_key(value.employee_backend);
  const model = value.employee_launch_model;
  if (typeof model !== 'string' || !model || model !== model.trim()) fail('employee_launch_model must be trimmed text naming a model', { employee_backend: backend });
  const effort = value.employee_launch_reasoning_effort;
  if (effort !== null && (typeof effort !== 'string' || !effort || effort !== effort.trim())) fail('employee_launch_reasoning_effort must be null or trimmed text');
  return { employee_backend: backend, employee_launch_model: model as string, employee_launch_reasoning_effort: effort as string | null };
}
