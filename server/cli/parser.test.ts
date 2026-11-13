import { expect, test } from 'vitest';
import { commands } from './commands.ts';
import { parse } from './parser.ts';

// The HTTP comparison can return the same empty page after integer rounding.
// Check actual declarations here to preserve exact large values and numeric validation.
test('integer options retain exact decimal values and reject invalid input', () => {
  const parsed = parse(
    ['project', 'list', '--offset', '9007199254740993', '--limit', '12'],
    commands,
  );
  expect(parsed.values).toMatchObject({ offset: 9007199254740993n, limit: 12 });
  expect(() => parse(['project', 'list', '--limit', 'not-a-number'], commands)).toThrow();
});
