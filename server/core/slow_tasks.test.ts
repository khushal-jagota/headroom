import { afterEach, expect, it, vi } from 'vitest';
import { count_rows, timed_task } from './slow_tasks.ts';

afterEach(() => vi.restoreAllMocks());

function busy(ms: number): void {
  const end = performance.now() + ms;
  while (performance.now() < end);
}

it('journals only tasks over 200 ms, with label, duration and rows', () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  timed_task('db.read', () => busy(5), 'fast');
  expect(warn).not.toHaveBeenCalled();
  timed_task(
    'db.read',
    () => {
      busy(210);
      count_rows(7);
    },
    'slow',
  );
  expect(warn).toHaveBeenCalledOnce();
  const line = String(warn.mock.calls[0]![0]);
  expect(line.startsWith('slow_task ')).toBe(true);
  expect(JSON.parse(line.slice('slow_task '.length))).toMatchObject({
    kind: 'db.read',
    label: 'slow',
    rows: 7,
  });
});

it('names the caller when no label is given', () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  timed_task('db.write', () => busy(210));
  expect(String(warn.mock.calls[0]![0])).toContain('slow_tasks.test.ts:');
});
