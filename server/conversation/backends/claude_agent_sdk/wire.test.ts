import { once } from 'node:events';
import { expect, it } from 'vitest';
import { ObservedStdout, ObservedStderr } from './wire.ts';
import { CLAUDE_SDK_MAX_BUFFER_SIZE } from './configuration.ts';

it('keeps UTF-8 chunk boundaries intact and reports raw receipts before forwarding the frame', async () => {
  const observations: unknown[] = [];
  const stream = new ObservedStdout((frame) => observations.push(frame));
  const forwarded: Buffer[] = [];
  stream.on('data', (chunk: Buffer) => {
    if (chunk.includes(Buffer.from('\n'))) expect(observations).toHaveLength(1);
    forwarded.push(chunk);
  });
  const frame = Buffer.from('{"type":"command_lifecycle","command_uuid":"🦊","state":"queued"}\n');
  const split = frame.indexOf(Buffer.from('🦊')) + 2;
  stream.write(frame.subarray(0, split));
  stream.end(frame.subarray(split));
  await once(stream, 'end');
  expect(observations).toEqual([
    { type: 'command_lifecycle', command_uuid: '🦊', state: 'queued' },
  ]);
  expect(Buffer.concat(forwarded)).toEqual(frame);
});

it('distinguishes malformed lines from truncated EOF and bounds an unfinished line before SDK readline', async () => {
  const broken = new ObservedStdout(() => {});
  broken.resume();
  const malformed = once(broken, 'error');
  broken.end('{broken}\n');
  expect((await malformed)[0].message).toBe('Failed to decode JSON: {broken}...');
  const tails: unknown[] = [];
  const truncated = new ObservedStdout((frame) => tails.push(frame));
  const forwarded: Buffer[] = [];
  truncated.on('data', (chunk: Buffer) => forwarded.push(chunk));
  truncated.write('[SandboxDebug] noise\n');
  truncated.end('{\"type\":\"result\"');
  await once(truncated, 'end');
  expect(tails).toEqual([]);
  expect(forwarded).toEqual([]);
  const valid_tail = new ObservedStdout((frame) => tails.push(frame));
  const valid_forwarded: Buffer[] = [];
  valid_tail.on('data', (chunk: Buffer) => valid_forwarded.push(chunk));
  valid_tail.end('{\"type\":\"result\",\"uuid\":\"tail\"}');
  await once(valid_tail, 'end');
  expect(tails).toEqual([{ type: 'result', uuid: 'tail' }]);
  expect(Buffer.concat(valid_forwarded).toString('utf8')).toBe(
    '{\"type\":\"result\",\"uuid\":\"tail\"}',
  );
  const oversized = new ObservedStdout(() => {});
  oversized.resume();
  const failure = once(oversized, 'error');
  oversized.write(' '.repeat(CLAUDE_SDK_MAX_BUFFER_SIZE));
  oversized.end('x');
  expect((await failure)[0].message).toBe(
    'Failed to decode JSON: JSON message exceeded maximum buffer size of 4194304 bytes...',
  );
});

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

it('parses native usage numbers without changing tool inputs or forwarded bytes', async () => {
  const observed: Record<string, unknown>[] = [];
  const forwarded: Buffer[] = [];
  const stream = new ObservedStdout((frame) => observed.push(frame));
  stream.on('data', (chunk: Buffer) => forwarded.push(chunk));
  const result =
    '{"type":"result","uuid":"counts","usage":{"input_tokens":5.0,"output_tokens":5,"cache_read_input_tokens":5e0},"total_cost_usd":5.0,"tool_metadata":{"input_tokens":5.0}}';
  const tool =
    '{"type":"assistant","message":{"content":[{"type":"tool_use","input":{"input_tokens":5.0,"output_tokens":5e0,"cache_read_input_tokens":5}}]}}';
  const wire = result + '\n' + tool + '\n';
  stream.end(wire);
  await once(stream, 'end');
  expect(observed[0]).toEqual({
    type: 'result',
    uuid: 'counts',
    usage: { input_tokens: 5, output_tokens: 5, cache_read_input_tokens: 5 },
    total_cost_usd: 5,
    tool_metadata: { input_tokens: 5 },
  });
  expect(observed[1]).toEqual(JSON.parse(tool));
  expect(Buffer.concat(forwarded).toString('utf8')).toBe(wire);
});
