/** Portable priorities and absolute-operation budgets, in seconds. */
export const NICE = 10;
export const STOP_SECONDS = 10;
export const TERM_SECONDS = 5;
export const NATIVE_STOP_SECONDS = 4;
export const INSPECTION_SECONDS = 2;
export const START_SECONDS = 5;
export const OBSERVATION_SECONDS = 2;
export const OBSERVATION_ERROR_LOG_SECONDS = 60;
export const STOP_OBSERVATION_BATCH_SECONDS = 0.05;
export const RECORD_ROOT_ENV = 'PLAN_WORKER_PROCESS_ROOT';
/** The environment entry that points a launch at the canonical Worker process root. */
export function worker_process_root_environment(
  root: string,
): readonly [typeof RECORD_ROOT_ENV, string] {
  return [RECORD_ROOT_ENV, root];
}
