/** Existing Python core timings and prompt composition constants. */
export const MODEL_THINKING_PULSE_INTERVAL_SECONDS = 0.25;
export const ROLE_TEXT_PROMPT_SEPARATOR = '\n\n';
export const AUTOMATIC_COMPACTION_AFTER_SECONDS = 50 * 60;
export const AUTOMATIC_COMPACTION_BEFORE_SECONDS = 60 * 60;
export const AUTOMATIC_COMPACTION_PROMPT = '/compact';
export const AUTOMATIC_COMPACTION_SENDER_LABEL = 'Panels';
export const IDLE_CHILD_STOP_AFTER_SECONDS = 30 * 60;
export const IDLE_CHILD_SWEEP_INTERVAL_SECONDS = 5 * 60;
export const LOST_BACKGROUND_TASK_SENDER_LABEL = 'Panels';
export const BACKGROUND_TASK_ENDINGS_OF_THEIR_OWN = new Set(['completed', 'failed', 'ambient']);
export const STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS = 2048;

/** Python CONVERSATION_WRITE_LOCK_RETRY_SECONDS, in the Node timer unit. */
export const WRITE_LOCK_RETRY_MILLISECONDS = 50;
