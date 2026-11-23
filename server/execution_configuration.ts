/** Current wire versions shared by the home and executor runtimes. */
export const PROTOCOL_VERSION = 28;
/** Adjacent releases differ by at most one protocol version, and each accepts the other's. */
export const MIN_SUPPORTED_PROTOCOL_VERSION = PROTOCOL_VERSION - 1;
export const MAX_SUPPORTED_PROTOCOL_VERSION = PROTOCOL_VERSION + 1;
export const SPOOL_FORMAT_VERSION = 5;

/** First machine protocol with source-work preservation checks. */
export const WORK_MOVE_PROTOCOL_VERSION = 28;
