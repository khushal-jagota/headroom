import { afterEach, expect, it, vi } from 'vitest';
import { ended_source_error } from './errors.ts';

afterEach(() => vi.restoreAllMocks());

it('stays silent for a process that ended and still logs real failures with their text', () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const ended = (code: string, path: string) =>
    Object.assign(new Error(`${code}: open '${path}'`), { code });
  expect(ended_source_error(ended('ENOENT', '/proc/1/io'), ['EIO'])).toBe('ENOENT');
  expect(ended_source_error(ended('ESRCH', '/proc/2/stat'), ['EIO'])).toBe('ESRCH');
  expect(warn).not.toHaveBeenCalled();
  expect(ended_source_error(ended('EACCES', '/proc/3/io'), ['EIO'])).toBe('EACCES');
  expect(warn).toHaveBeenCalledTimes(1);
  expect(String(warn.mock.calls[0]![1])).toContain('/proc/3/io');
});
