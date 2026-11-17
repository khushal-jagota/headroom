import { expect, it } from 'vitest';
import { ObservedStderr } from './wire.ts';
import { CLAUDE_SDK_MAX_BUFFER_SIZE } from './configuration.ts';

it('frames stderr chunks, right-trims, ignores blanks and isolates callback failures', () => {
  const lengths: number[] = [];
  const lines: string[] = [];
  const stderr = new ObservedStderr((line) => {
    lengths.push(line.length);
    if (line.length < 100) lines.push(line);
    if (line === ' first') throw new Error('callback failed');
  });
  const text = Buffer.from(' first  \r\n\t\n🦊  \n');
  const split = text.indexOf(Buffer.from('🦊')) + 2;
  stderr.write(text.subarray(0, split));
  stderr.write(text.subarray(split));
  stderr.write(Buffer.from('x'.repeat(CLAUDE_SDK_MAX_BUFFER_SIZE + 1)));
  expect(lengths.at(-1)).toBe(CLAUDE_SDK_MAX_BUFFER_SIZE + 1);
  stderr.write(Buffer.from('tail  '));
  stderr.end();
  stderr.write(Buffer.from('ignored after cancellation'));
  expect(lines).toEqual([' first', '🦊', 'tail']);
});
