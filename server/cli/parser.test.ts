import { expect, test } from 'vitest';
import { create_commands } from './commands.ts';
import { parse } from './parser.ts';

// The HTTP comparison can return the same empty page after integer rounding.
// Check actual declarations here to preserve exact large values and numeric validation.
test('integer options retain exact decimal values and reject invalid input', () => {
  const parsed = parse(
    ['project', 'list', '--offset', '9007199254740993', '--limit', '12'],
    create_commands,
  );
  expect(parsed.values).toMatchObject({ offset: 9007199254740993n, limit: 12 });
  expect(() => parse(['project', 'list', '--limit', 'not-a-number'], create_commands)).toThrow();
});

// Commander accepts bare negative operands, which would reach an HTTP handler
// instead of returning usage. The HTTP comparisons do not pin this distinction.
test('dash-prefixed operands require a terminator without changing option values', () => {
  const usage_failure = expect.objectContaining({
    result: expect.objectContaining({ exit_code: 2 }),
  });
  expect(() => parse(['ticket', 'show', '-1'], create_commands, {})).toThrow(usage_failure);
  expect(parse(['ticket', 'show', '--', '-1'], create_commands, {}).values.ticket_id).toBe('-1');
  expect(
    parse(['project', 'show', '--json', '--', '--json'], create_commands, {}).values.project_id,
  ).toBe('--json');
  expect(parse(['attention', 'summary', '--limit', '-1'], create_commands, {}).values.limit).toBe(
    -1,
  );
  expect(() =>
    parse(['project', 'set', '--value', '--', '-1', 'summary'], create_commands, {}),
  ).toThrow(usage_failure);
});
