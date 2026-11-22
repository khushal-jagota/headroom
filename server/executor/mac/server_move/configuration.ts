/** Tunable limits and timings of a server move on the target machine. */
export { ARCHIVE_MAX_COMPRESSED_BYTES as MAX_SERVER_MOVE_PACKAGE_BYTES } from '../../../environments/release/configuration.ts';

/** How often the run asks again while the old server prepares the package. */
export const SERVER_MOVE_INSTRUCTION_POLL_SECONDS = 5;
/** How soon a leftover move folder is settled again after a failed attempt. */
export const SERVER_MOVE_RECOVERY_RETRY_SECONDS = 10;
/**
 * After `checked`, how long past the old server's pause limit the run keeps asking for a
 * settled phase before it reports a failure.
 */
export const SERVER_MOVE_SETTLE_MARGIN_SECONDS = 60;
/** Silence allowed on one ordinary move request or download. */
export const SERVER_MOVE_REQUEST_SECONDS = 30;
/** The old server drains its work and copies the database before it answers the pause. */
export const SERVER_MOVE_PAUSE_REQUEST_SECONDS = 600;
/** The old server checks the new address before it answers the report. */
export const SERVER_MOVE_REPORT_REQUEST_SECONDS = 60;
/** The installer starts the new server and waits for its health check. */
export const SERVER_MOVE_INSTALLER_SECONDS = 600;
export const MAX_SERVER_MOVE_REPLY_BYTES = 64 * 1024;
export const MAX_SERVER_MOVE_FILE_LIST_BYTES = 64 * 1024 * 1024;
export const MAX_SERVER_MOVE_INSTALLER_OUTPUT_BYTES = 64 * 1024;
export const MAX_SERVER_MOVE_REASON_CHARACTERS = 500;
/** The folder under the executor root that holds one folder per move. */
export const SERVER_MOVE_WORK_DIRECTORY = 'server-move';
export const SERVER_MOVE_RUN_FILE = 'run.json';
export const SERVER_MOVE_PACKAGE_FILE = 'package.tar.gz';
export const SERVER_MOVE_DATA_DIRECTORY = 'data';
export const SERVER_MOVE_PAYLOAD_DIRECTORY = 'payload';
export const SERVER_MOVE_INSTALLER = 'server/installation/move_main.ts';
