/** Recovery cadence also drives the truthful local connection deadline. */
export const UPDATE_RECOVERY_POLL_MS = 3_000;
/** A press shows Updating… until the server's answer moves, at most this long. */
export const UPDATE_STARTING_LIMIT_MS = 15_000;
/** A committed update is announced briefly in the sidebar. */
export const UPDATE_CONFIRMATION_MS = 8_000;
/** One probe of the server while the connection recovers gives up after this long. */
export const CONNECTION_PROBE_TIMEOUT_MS = 5_000;
/** The server sends a heartbeat on the change stream this often. */
export const STREAM_HEARTBEAT_MS = 15_000;
/** A change stream that sends nothing for this long counts as lost. */
export const STREAM_SILENCE_MS = STREAM_HEARTBEAT_MS * 2.5;
