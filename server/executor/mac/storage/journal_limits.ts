/** Fixed resource admission bounds, independent of executor wire/body limits. */
import { MAX_MAILBOX_POLL_BYTES } from '../configuration.ts';
export const JOURNAL_OPERATIONS = 32;
export const JOURNAL_CURSORS = 32;
export const JOURNAL_READERS = 64;
export const JOURNAL_INPUT_BYTES = MAX_MAILBOX_POLL_BYTES;
/** Results retain SQLite's existing ceiling, including an oversized first report. */
export const JOURNAL_RESULT_BYTES = 0x7fffffff;
