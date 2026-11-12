import { PlannerError } from '../../core/contracts.ts';
import type { EmployeeLaunchConfiguration } from '../contracts.ts';
export function launch_configuration_for_a_new_ticket(o: {
    default_backend: string;
    default_model: string;
    default_reasoning_effort: string | null;
    employee_backend: string;
    employee_launch_model: string | null;
    employee_launch_reasoning_effort?: string | null;
    reasoning_effort_is_set?: boolean;
}): EmployeeLaunchConfiguration { const effort = o.employee_launch_reasoning_effort ?? null; if (effort !== null && (typeof effort !== 'string' || !effort.trim()))
    throw new PlannerError('validation', 'employee_launch_reasoning_effort must be a nonblank string'); let model = o.employee_launch_model; if (model === null || !model.trim()) {
    if (o.employee_backend !== o.default_backend)
        throw new PlannerError('validation', "a Ticket created on a different backend has to name that backend's model", { employee_backend: o.employee_backend });
    model = o.default_model;
} const own = o.employee_backend === o.default_backend && model === o.default_model; return { employee_backend: o.employee_backend, employee_launch_model: model, employee_launch_reasoning_effort: effort !== null || o.reasoning_effort_is_set ? effort : own ? o.default_reasoning_effort : null }; }
export function normalize_employee_launch_configuration(current: EmployeeLaunchConfiguration, candidate: EmployeeLaunchConfiguration): EmployeeLaunchConfiguration { if (current.employee_backend === candidate.employee_backend && current.employee_launch_model === candidate.employee_launch_model && current.employee_launch_reasoning_effort === candidate.employee_launch_reasoning_effort)
    return current; const model = candidate.employee_launch_model; if (model === null || !model.trim())
    throw new PlannerError('validation', "a Ticket's launch configuration has to name a model", { employee_backend: candidate.employee_backend }); if (candidate.employee_launch_reasoning_effort !== null && !candidate.employee_launch_reasoning_effort.trim())
    throw new PlannerError('validation', 'employee_launch_reasoning_effort must be a nonblank string'); return { ...candidate }; }
