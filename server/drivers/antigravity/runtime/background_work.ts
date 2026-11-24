/** An explicit async command is a candidate until it outlives the prompt that started it. */
import type { SessionNotification } from '@agentclientprotocol/sdk/unstable';

export function antigravity_background_task_description(
  update: SessionNotification['update'],
): string | null {
  if (update.sessionUpdate !== 'tool_call' && update.sessionUpdate !== 'tool_call_update')
    return null;
  const input = update.rawInput;
  if (typeof input !== 'object' || input === null || Array.isArray(input)) return null;
  const values = input as Record<string, unknown>;
  if (
    typeof values.WaitMsBeforeAsync !== 'number' ||
    !Number.isFinite(values.WaitMsBeforeAsync) ||
    values.WaitMsBeforeAsync < 0 ||
    typeof values.CommandLine !== 'string' ||
    !values.CommandLine.trim()
  )
    return null;
  return values.CommandLine;
}
