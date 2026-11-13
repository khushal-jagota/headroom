export const MAC_AWAY_AFTER_SECONDS = 60;
/** Pydantic/jiter's fixed JSON input boundary, not the JavaScript stack budget. */
export const EXECUTOR_JSON_MAX_DEPTH = 200;
/** The signed integer lexeme counts toward jiter's 4,300-character integer limit. */
export const EXECUTOR_JSON_MAX_INTEGER_LENGTH = 4300;
export const MAX_ENVELOPE_BYTES = 1024 * 1024;
export const LEASE_INTERVAL_SECONDS = 10;
export const HELD_POLL_EVIDENCE_MS = LEASE_INTERVAL_SECONDS * 1500;
export const MAX_MAILBOX_POLL_COMMANDS = 32;
export const MAX_MAILBOX_POLL_BYTES = 4 * 1024 * 1024;
export const STEER_DELIVERY_LIMIT_SECONDS = 10;
export const MODEL_LIST_LIMIT_SECONDS = 35;

/** Node timers cap delays at a signed 32-bit millisecond value; longer waits need rechecks. */
export const MAX_MAILBOX_WAIT_MS = 2 ** 31 - 1;
