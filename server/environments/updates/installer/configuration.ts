export const SERVICE_COMMAND_TIMEOUT_SECONDS = 120;
export const INTERRUPTION_CAPTURE_BUDGET_SECONDS = 2;
export const DATABASE_RESTORE_TIMEOUT_SECONDS = 900;
export const HEALTH_POLL_SECONDS = 0.5;
export const HEALTH_INACTIVITY_SECONDS = 5;
export { STAGED_APP_PREFIX, PREVIOUS_APP_PREFIX } from '../configuration.ts';
import { testModeFromEnvironment } from '../../../core/config.ts';
import { assert_temporary_path } from '../../../core/test_paths.ts';

/** An invocation snapshot cannot turn off the actual process's test guard. */
export function installer_test_mode(environment: NodeJS.ProcessEnv = process.env): boolean {
  return (
    process.env.VITEST === 'true' ||
    process.env.PYTEST_CURRENT_TEST !== undefined ||
    testModeFromEnvironment() ||
    environment.VITEST === 'true' ||
    environment.PYTEST_CURRENT_TEST !== undefined ||
    testModeFromEnvironment(environment)
  );
}
export function assert_installer_path(path: string, environment?: NodeJS.ProcessEnv): void {
  if (installer_test_mode(environment)) assert_temporary_path(path);
}
