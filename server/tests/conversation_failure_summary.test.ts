/** Python Unicode rules determine stored error text, not JavaScript regex defaults. */
import { expect, test } from 'vitest';
import { normalized_failure_summary } from '../conversation/logic/backend_events.ts';

// These actual Python/TS differences are absent from the ASCII adapter summaries.
test('failure summaries retain Python Unicode whitespace, word boundaries, and case folding', () => {
  const cases = [
    ['\u0085failed\u001cnow\u0085', 'failed now'],
    ['\ufefffailed\ufeff', '\ufefffailed\ufeff'],
    ['épassword=secret', 'épassword=secret'],
    ['²password=secret', '²password=secret'],
    ['apı_key=secret', 'apı_key=[redacted]'],
    ['apİ_key=secret', 'apİ_key=[redacted]'],
    ['authorızatıon: Bearer abc', 'authorızatıon: [redacted]'],
    ['password\ufeff=secret', 'password\ufeff=secret'],
    ['Bearer secret\ufefftail', 'Bearer [redacted]'],
  ];
  for (const [input, expected] of cases) {
    expect(normalized_failure_summary(input)).toBe(expected);
  }
});
