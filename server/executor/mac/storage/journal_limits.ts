/** Fixed resource admission bounds, independent of executor wire/body limits. */
export const JOURNAL_OPERATIONS = 32;
export const JOURNAL_CURSORS = 32;
export const JOURNAL_READERS = 64;
export const JOURNAL_INPUT_BYTES = 0x7fffffff;
