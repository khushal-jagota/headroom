import { expect, test } from 'vitest';
import { JsonNumber } from '../core/http/numeric_json.ts';
import { python_text } from './python_text.ts';
import { MissingResponseValue } from './errors.ts';

// JSON-formatted records deliberately differ. Native f-string lines must not print null/false
// or lose a retained float kind; ordinary populated route rows do not expose all three risks.
test('native Python scalar text preserves null, booleans, and numeric kinds', () => {
  expect(
    [null, true, false, new JsonNumber('1'), new JsonNumber('1.0'), new JsonNumber('-0.0')].map(
      python_text,
    ),
  ).toEqual(['None', 'True', 'False', '1', '1.0', '-0.0']);
  expect(python_text({ ready: false, values: [null, 'x'] })).toBe(
    "{'ready': False, 'values': [None, 'x']}",
  );
  expect(() => python_text(undefined)).toThrow(MissingResponseValue);
});
