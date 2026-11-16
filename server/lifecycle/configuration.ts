/** One application stop: graceful work, hard phase, then the external supervisor. */
export const SHUTDOWN_GRACE_SECONDS = 30;
export const SHUTDOWN_HARD_SECONDS = 45;
export const SUPERVISOR_APP_STOP_SECONDS = 60;
export const MAX_CONTROL_MESSAGE_BYTES = 4096;
export const CONTROL_EXCHANGE_SECONDS = 10;
export const STOP_INTENT_SECONDS = 5;
export const CAPABILITY_FILENAME = 'stop-capability.json';
export const monotonic_seconds = (): number => performance.now() / 1000;
/** One control exchange, APP fallback, then an exchange interval for cleanup. */
export const MANAGED_STOP_CLIENT_SECONDS =
  SUPERVISOR_APP_STOP_SECONDS + 2 * CONTROL_EXCHANGE_SECONDS;
/** systemd applies this per phase. It is not a total-stop bound. */
export const UNIT_TIMEOUT_STOP_SECONDS = SUPERVISOR_APP_STOP_SECONDS + SHUTDOWN_GRACE_SECONDS;
