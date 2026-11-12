/** Exact Manager launch choices used by the shared message dispatcher. */
import type { ReadSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type {
  ConversationStartOverrides,
  ConversationStartValues,
} from '../runtime/logic/conversation_start_resolution.ts';
import { read_manager } from './data.ts';
import type { ManagerLaunchConfiguration } from './contracts.ts';
export function validate_launch_configuration(
  sql: ReadSql,
  value: ManagerLaunchConfiguration,
): void {
  if (value.employee_backend === 'codex' && value.employee_launch_model === 'sol') {
    throw new PlannerError(
      'validation',
      'The legacy Codex model "sol" cannot run. Choose an explicit Codex model ' +
        "in this Manager's model settings before you retry.",
    );
  }
  const row = sql.get<{
    enabled: number;
  }>('SELECT enabled FROM backend_model_enablement WHERE backend_key=? AND model_id=?', [
    value.employee_backend,
    value.employee_launch_model,
  ]);
  if (row && !row.enabled) {
    throw new PlannerError(
      'validation',
      `Model "${value.employee_launch_model}" is disabled. Choose an enabled model.`,
      {
        employee_backend: value.employee_backend,
        employee_launch_model: value.employee_launch_model,
      },
    );
  }
}
export function manager_reuse_overrides(
  sql: ReadSql,
  manager_id: string,
  values: ConversationStartValues | null,
  overrides: ConversationStartOverrides,
  steer_current_turn: boolean,
): ConversationStartOverrides {
  const item = read_manager(sql, manager_id).item;
  if (!item.launch_configuration_is_explicit || values === null || steer_current_turn)
    return overrides;
  return {
    backend_key: values.backend_key,
    model: values.model,
    reasoning_effort: values.reasoning_effort,
    reasoning_effort_is_set: true,
  };
}
