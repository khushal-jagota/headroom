/** How long one request waits for the server, shared by the fetch boundary and tests. */
export const REQUEST_LIMIT_MS = 25_000;
/** A message send: a cold backend starts, or a running turn confirms that it stopped. */
export const SEND_REQUEST_LIMIT_MS = 30_000;
/**
 * A read of the agent backends asks each CLI for its version, identity and models. The
 * server allows 20 s, 30 s and 30 s for those, and 35 s for the models of a remote machine.
 */
export const BACKEND_READ_LIMIT_MS = 100_000;
/** The server runs the CLI update inside the request and allows it 600 s. */
export const BACKEND_UPDATE_LIMIT_MS = 660_000;
/** The server allows two 20 s probes and a 60 s exchange for a sign-in step. */
export const BACKEND_SIGN_IN_LIMIT_MS = 120_000;
/** A usage window scans the recorded transcripts, and the server sets no limit for it. */
export const USAGE_SCAN_LIMIT_MS = 120_000;
/** The server allows 60 s for a transcription after 30 s for the silence trim. */
export const VOICE_TRANSCRIPTION_LIMIT_MS = 120_000;
/** The server waits up to 30 s for the Ticket's machine before it answers. */
export const WORKER_CLEANUP_RECHECK_LIMIT_MS = 60_000;
/**
 * A work move stops the source conversation, waits up to 60 s for the source machine
 * to prove that the work stopped, and starts a conversation on the destination.
 */
export const WORK_MOVE_LIMIT_MS = 120_000;
/** Move everything moves one owner after another, and the server sets no limit for it. */
export const WORK_MOVE_EVERYTHING_LIMIT_MS = 600_000;
/** A kept picture or file of up to ten mebibytes, read back to send it again. */
export const ATTACHMENT_READ_LIMIT_MS = 120_000;
/** How long a control that closes on save waits for its screen to show the saved value. */
export const SCREEN_REFRESH_WAIT_MS = 2_000;
