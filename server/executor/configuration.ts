export const MAC_AWAY_AFTER_SECONDS = 60;
/** Pydantic/jiter's fixed JSON input boundary, not the JavaScript stack budget. */
export const EXECUTOR_JSON_MAX_DEPTH = 200;
/** The signed integer lexeme counts toward jiter's 4,300-character integer limit. */
export const EXECUTOR_JSON_MAX_INTEGER_LENGTH = 4300;
export const MAX_ENVELOPE_BYTES = 1024 * 1024;
export const FILE_CHUNK_BYTES = 256 * 1024;
export const MAX_FILES_PER_PUBLICATION_TRANSACTION = 32;
export const MAX_ITEM_BATCH_ITEMS = 32;
export const MAC_ITEM_RETRY_LATER_SECONDS = 20;
export const TRANSFER_CHUNK_BYTES = 64 * 1024;
export const COMMAND_POLL_HOLD_SECONDS = 50;
export const LEASE_SERVER_LIMIT_SECONDS = 5;
export const COMMAND_OUTCOME_SERVER_LIMIT_SECONDS = 5;
export const MAX_COMMAND_OUTCOMES_PER_REPORT = 32;
export const MAX_COMMAND_OUTCOME_REPORT_BYTES = 4 * 1024 * 1024;
export const MAX_ITEM_BATCH_BYTES = 4 * 1024 * 1024;
export const LIVE_READ_SERVER_LIMIT_SECONDS = 20;
export const LIVE_EVENT_SERVER_LIMIT_SECONDS = 5;
export const MAX_LIVE_EVENTS_PER_BATCH = 64;
export const MAX_MAC_UPDATE_RESPONSE_BYTES = 64 * 1024;
export const FILE_TRANSFER_STALL_SECONDS = 60;
export const LEASE_INTERVAL_SECONDS = 10;
export const LEASE_CONTINUITY_SECONDS = 25;
export const CONTACT_FLUSH_INTERVAL_SECONDS = 10;
export const HELD_POLL_EVIDENCE_MS = LEASE_INTERVAL_SECONDS * 1500;
export const MAX_MAILBOX_POLL_COMMANDS = 32;
export const MAX_MAILBOX_POLL_BYTES = 4 * 1024 * 1024;
export const STEER_DELIVERY_LIMIT_SECONDS = 10;
export const DRAIN_RETRY_INITIAL_SECONDS = 1;
export const DRAIN_RETRY_MAX_SECONDS = 60;
export const MODEL_LIST_LIMIT_SECONDS = 35;
export const CATALOG_CLEANUP_SECONDS = 2;
export const MAC_AGENT_PROCESS_PROBE_TIMEOUT_SECONDS = 10;
export const MAC_AGENT_PROCESS_POLL_MS = 100;
export const MAC_AGENT_PROCESS_KILL_WAIT_SECONDS = 2;

/** Node timers cap delays at a signed 32-bit millisecond value; longer waits need rechecks. */
export const MAX_MAILBOX_WAIT_MS = 2 ** 31 - 1;

/** Shared Mac update manifest contract version. */
export const MAC_UPDATE_SCHEMA_VERSION = 1;
