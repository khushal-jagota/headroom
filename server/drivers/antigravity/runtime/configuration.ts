/** Antigravity's tunables and the state directory Panels gives its own agy. */

/** `--app-data-dir` inside the home. The CLI's own login and history stay in `antigravity-cli`. */
export const APP_DATA_DIRECTORY_NAME = 'antigravity-acp';
/** agy can hang without an error. A turn that hears nothing for this long fails. */
export const PROMPT_INACTIVITY_TIMEOUT_MS = 15 * 60 * 1000;
/** The error text leads an error step's payload; a Go stack trace follows it. */
export const ERROR_STEP_PAYLOAD_READ_LIMIT_BYTES = 16_384;
/** How long a session database read waits on agy's own write lock. */
export const SESSION_DATABASE_BUSY_TIMEOUT_MS = 100;
