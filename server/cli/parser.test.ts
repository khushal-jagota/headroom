import { expect, test } from 'vitest';
import { commands } from './commands.ts';
import { parse } from './parser.ts';

// The HTTP comparison can return the same empty page after integer rounding.
// Check actual declarations here to preserve large values and Python integer spellings.
test('integer options retain exact values across Unicode digits and underscore separators', () => {
  const parsed = parse(
    ['project', 'list', '--offset', '9_007_199_254_740_993', '--limit', '١_٢'],
    commands,
  );
  expect(parsed.values).toMatchObject({ offset: 9007199254740993n, limit: 12 });
});
