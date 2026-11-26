/** Antigravity's tunables and where agy keeps its state. */

/**
 * agy's default `--app-data-dir` inside the home, which Panels leaves as it is: Panels shares the
 * CLI's own login, conversations and brain.
 */
export const AGY_APP_DATA_DIRECTORY_NAME = 'antigravity-cli';
/** agy's own logs inside the app data directory. The ACP server logs name the signed-in account. */
export const AGY_LOG_DIRECTORY_NAME = 'log';
/** agy can hang without an error. A turn that hears nothing for this long fails. */
export const PROMPT_INACTIVITY_TIMEOUT_MS = 15 * 60 * 1000;
/** The error text leads an error step's payload; a Go stack trace follows it. */
export const ERROR_STEP_PAYLOAD_READ_LIMIT_BYTES = 16_384;
/** How long a session database read waits on agy's own write lock. */
export const SESSION_DATABASE_BUSY_TIMEOUT_MS = 100;
