/** Failure text uses native whitespace rules and retains credential redaction. */
import { expect, test } from 'vitest';
import { normalized_failure_summary } from '../conversation/logic/backend_events.ts';

test('failure summaries normalize whitespace without weakening Unicode credential redaction', () => {
  const cases = [
    [' \tfailed\nnow ', 'failed now'],
    ['\ufefffailed\ufeff', 'failed'],
    ['épassword=secret', 'épassword=secret'],
    ['²password=secret', '²password=secret'],
    ['apı_key=secret', 'apı_key=[redacted]'],
    ['apİ_key=secret', 'apİ_key=[redacted]'],
    ['authorızatıon: Bearer abc', 'authorızatıon: [redacted]'],
    ['password\ufeff=secret', 'password =[redacted]'],
    ['Bearer secret\ufefftail', 'Bearer [redacted] tail'],
  ];
  for (const [input, expected] of cases) {
    expect(normalized_failure_summary(input)).toBe(expected);
  }
});
