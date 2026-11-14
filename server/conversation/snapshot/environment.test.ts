import { expect, test } from 'vitest';
import { claude_executable_override } from './environment.ts';

test('retired executable matching preserves parent components in an override', () => {
  const suffix = 'agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe';
  const environment = { PLAN_APP_ROOT: '/app', PLAN_CLAUDE_EXECUTABLE: `/app/${suffix}` };
  expect(claude_executable_override(environment, (value) => value)).toBeUndefined();
  environment.PLAN_CLAUDE_EXECUTABLE = `/app/link/../${suffix}`;
  expect(claude_executable_override(environment, (value) => value)).toBe(
    environment.PLAN_CLAUDE_EXECUTABLE,
  );
});
