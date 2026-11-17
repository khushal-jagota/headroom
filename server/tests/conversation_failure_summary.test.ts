/** Failure text uses native whitespace rules and retains credential redaction. */
import { expect, test } from 'vitest';
import {
  normalized_failure_summary,
  recorded_failure_detail,
} from '../conversation/logic/backend_events.ts';

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
    [
      'password\u0085=\u001fsynthetic; api\u0085key=synthetic; Bearer\u001csynthetic',
      'password\u0085=\u001f[redacted]; api\u0085key=[redacted]; Bearer [redacted]',
    ],
  ];
  for (const [input, expected] of cases) {
    expect(normalized_failure_summary(input)).toBe(expected);
  }
});

test('a cut recorded failure detail keeps the start and the end', () => {
  const start = 'Error: claude exited ' + 'a'.repeat(200);
  const end = 'b'.repeat(200) + ' cause: rate limit reached';
  const recorded = recorded_failure_detail(`${start} ${end}`)!;
  expect(Array.from(recorded)).toHaveLength(240);
  expect(recorded.startsWith('Error: claude exited aaa')).toBe(true);
  expect(recorded).toContain('a … b');
  expect(recorded.endsWith('bbb cause: rate limit reached')).toBe(true);
  expect(recorded_failure_detail('short failure')).toBe('short failure');
});
