import { expect, test } from 'vitest';
import { antigravity_background_task_description } from './background_work.ts';

// Risk: a subagent launch acknowledgment or ordinary shell tool becomes a false background task.
// The live ACP trace exposes async intent through this command input, not the execute tool kind.
test('only an explicit async command supplies a background candidate', () => {
  const update = {
    sessionUpdate: 'tool_call' as const,
    toolCallId: 'async-command',
    title: 'sleep 45; echo PANELS_ASYNC_DONE',
    status: 'in_progress' as const,
    kind: 'execute' as const,
  };
  expect(
    antigravity_background_task_description({
      ...update,
      rawInput: { CommandLine: update.title, WaitMsBeforeAsync: 500 },
    }),
  ).toBe(update.title);
  expect(
    antigravity_background_task_description({
      ...update,
      rawInput: { CommandLine: 'echo foreground' },
    }),
  ).toBeNull();
  expect(
    antigravity_background_task_description({
      ...update,
      kind: 'other',
      rawInput: { Prompt: 'SUBAGENT_DONE', Role: 'Probe', TypeName: 'self' },
    }),
  ).toBeNull();
});
