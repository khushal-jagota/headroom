/** Bounds and SDK contract of the ACP connection. */
export const ACP_SDK_VERSION = '2.6.0';
export const CHILD_OUTPUT_LINE_LIMIT_BYTES = 50 * 1024 * 1024;
export const STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS = 8192;
export const CANCELLED_TURN_ENDING_TIMEOUT_SECONDS = 15;
export const ANSWER_ON_THE_WIRE_TIMEOUT_SECONDS = 15;
export const AVAILABLE_COMMANDS_WAIT_TIMEOUT_SECONDS = 15;
export const CONTROL_REQUEST_TIMEOUT_SECONDS = 15;
/**
 * How many silence limits an open tool call holds a turn's silence clock for, counted from when
 * the agent was last heard. An agent can leave a tool call open for good and then hang.
 */
export const OPEN_TOOL_CALL_SILENCE_LIMITS = 8;
