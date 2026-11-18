import { afterEach, expect, it, vi } from 'vitest';
import { ended_source_error } from './errors.ts';

afterEach(() => vi.restoreAllMocks());

it('stays silent for ended processes, summarizes denied reads hourly, and logs real failures', () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const fail = (code: string, path: string) =>
    Object.assign(new Error(`${code}: open '${path}'`), { code });
  expect(ended_source_error(fail('ENOENT', '/proc/1/io'), ['EIO'])).toBe('ENOENT');
  expect(ended_source_error(fail('ESRCH', '/proc/2/stat'), ['EIO'])).toBe('ESRCH');
  expect(warn).not.toHaveBeenCalled();

  const t0 = Date.now() + 3600_000;
  for (let i = 0; i < 5; i++)
    ended_source_error(fail('EACCES', `/proc/${i}/io`), ['EIO'], true, t0 + i);
  expect(warn).toHaveBeenCalledTimes(1);
  expect(String(warn.mock.calls[0]![0])).toContain('1 reads');
  ended_source_error(fail('EACCES', '/proc/9/io'), ['EIO'], true, t0 + 3600_000 + 10);
  expect(warn).toHaveBeenCalledTimes(2);
  expect(String(warn.mock.calls[1]![0])).toContain('5 reads');

  // Without the per-process flag, EACCES is a real failure and logs its text.
  ended_source_error(fail('EACCES', '/sys/fs/cgroup/x/cpu.stat'), ['EIO']);
  expect(warn).toHaveBeenCalledTimes(3);
  expect(String(warn.mock.calls[2]![1])).toContain('/sys/fs/cgroup/x/cpu.stat');
});
