/** Timings and limits of a server move. The pause limit itself is part of the contract. */

/** In the committing phase, the old server asks the new address this often. */
export const SERVER_MOVE_PROBE_INTERVAL_SECONDS = 3;
/** One HTTPS request to the new address. */
export const SERVER_MOVE_PROBE_TIMEOUT_SECONDS = 10;
/** The pause waits this long for admitted operations to finish before it fails the run. */
export const SERVER_MOVE_DRAIN_SECONDS = 60;
/** How often the old server checks the age of its pause. */
export const SERVER_MOVE_PAUSE_CHECK_SECONDS = 1;
/** One download of the server package from the release host. */
export const SERVER_MOVE_PACKAGE_DOWNLOAD_SECONDS = 1800;
/** One request to the release host for its lists and receipts. */
export const SERVER_MOVE_RELEASE_REQUEST_SECONDS = 30;
/** A move request body on an executor route. */
export const SERVER_MOVE_MAX_REQUEST_BYTES = 64 * 1024;
/** A manifest file read from inside the server package. */
export const SERVER_MOVE_MAX_MANIFEST_BYTES = 1024 * 1024;
/** A held server keeps this many test jobs; older ones are forgotten. */
export const SERVER_MOVE_KEPT_PROBE_JOBS = 8;
