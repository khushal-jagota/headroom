/** The one list of drivers this build carries. The only door into a driver package. */
import type { AnyAgentDriver } from './core/driver.ts';
import { default_instance_id_for_driver } from './core/identity.ts';
import { claude_driver } from './claude/driver.ts';
import { codex_driver } from './codex/driver.ts';

/** Card order and the order of the unknown-backend error detail. */
export const BUILT_IN_DRIVERS: readonly AnyAgentDriver[] = [codex_driver, claude_driver];
/** The backend a conversation starts on when nothing chooses one. */
export const DEFAULT_BACKEND_INSTANCE_ID = default_instance_id_for_driver(codex_driver.driver_kind);
/** The default backend, with a model and effort it can run. */
export const DEFAULT_LAUNCH = {
  employee_backend: DEFAULT_BACKEND_INSTANCE_ID,
  employee_launch_model: 'gpt-6.1-sol',
  employee_launch_reasoning_effort: 'medium',
} as const;
