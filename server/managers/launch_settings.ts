import { require_usable_model } from '../conversation/backend_state.ts';
/** Exact Manager launch choices used by the shared message dispatcher. */
import { require_conversation_backend_key } from '../conversation/contracts.ts';
import { validate_launch_defaults } from '../worker_settings/service.ts';
import type { ReadSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { built_in_driver_of } from '../drivers/built_in_manifests.ts';
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
  const refusal = built_in_driver_of(value.employee_backend)?.launch_model_refusal?.(
    value.employee_launch_model,
  );
  if (refusal) throw new PlannerError('validation', refusal);
  require_usable_model(sql, value.employee_backend, value.employee_launch_model, 'vps');
}
export function manager_reuse_overrides(
  sql: ReadSql,
  manager_id: string,
  conversation_id: string,
  values: ConversationStartValues | null,
  overrides: ConversationStartOverrides,
  steer_current_turn: boolean,
): ConversationStartOverrides {
  const item = read_manager(sql, manager_id).item;
  if (!item.launch_configuration_is_explicit || values === null || steer_current_turn)
    return overrides;
  const current = sql.get<{
    backend_key: string;
    model: string | null;
    reasoning_effort: string | null;
  }>('SELECT backend_key,model,reasoning_effort FROM conversations WHERE conversation_id=?', [
    conversation_id,
  ]);
  if (current === undefined) return overrides;
  return {
    ...overrides,
    ...(current.backend_key === values.backend_key ? {} : { backend_key: values.backend_key }),
    ...(current.model === values.model ? {} : { model: values.model }),
    ...(current.reasoning_effort === values.reasoning_effort
      ? {}
      : { reasoning_effort: values.reasoning_effort, reasoning_effort_is_set: true }),
  };
}

export function parse_launch_configuration(raw: unknown): ManagerLaunchConfiguration {
  const defaults = validate_launch_defaults(raw);
  return {
    ...defaults,
    employee_backend: require_conversation_backend_key(defaults.employee_backend),
  };
}
export function launch_configuration_json(
  configuration: ManagerLaunchConfiguration,
): Record<string, unknown> {
  return { ...configuration };
}
