import { describe, expect, test, vi } from 'vitest';
import { DarwinWorkerJobs, service_target } from './adapter.ts';
import { job_plist } from './transport.ts';
import { NICE } from '../../../worker_supervision/config.ts';
import type { WorkerJobIdentity } from '../../../worker_supervision/contracts.ts';

const job: WorkerJobIdentity = {
  domain: `gui/${process.getuid!()}`,
  label: `com.panels.worker.${'a'.repeat(20)}.${'b'.repeat(32)}`,
};
const outcome = (exit_code: number, standard_output = '', standard_error = '') => ({
  exit_code,
  standard_output,
  standard_error,
});
describe('Mac Worker launchd custody', () => {
  test('ambiguous failures never prove absence, and missing service requires a reachable domain', async () => {
    const run = vi.fn().mockResolvedValueOnce(outcome(1, '', 'Operation not permitted'));
    const jobs = new DarwinWorkerJobs({ commands: { run }, clock: () => 0 });
    await expect(jobs.status(job, 2)).rejects.toThrow('Cannot inspect');
    expect(run).toHaveBeenCalledTimes(1);
    run
      .mockResolvedValueOnce(
        outcome(113, '', `Could not find service "${job.label}" in domain for user gui: 501`),
      )
      .mockResolvedValueOnce(outcome(1, '', 'Could not find domain'));
    await expect(jobs.status(job, 2)).rejects.toThrow('domain is unavailable');
    run
      .mockResolvedValueOnce(
        outcome(113, '', `Could not find service "${job.label}" in domain for user gui: 501`),
      )
      .mockResolvedValueOnce(outcome(0, 'gui = {}'));
    await expect(jobs.status(job, 2)).resolves.toBe('absent');
  });
  test('loaded without a running process remains custody until bootout proves service absence', async () => {
    const run = vi
      .fn()
      .mockResolvedValueOnce(outcome(0, 'state = exited'))
      .mockResolvedValueOnce(outcome(0))
      .mockResolvedValueOnce(outcome(0, 'state = exited'));
    const jobs = new DarwinWorkerJobs({ commands: { run }, clock: () => 0 });
    await expect(jobs.stop(job, 2)).rejects.toThrow('removal is not proven');
    expect(run.mock.calls.map((call) => call[0])).toEqual([
      ['/bin/launchctl', 'print', service_target(job)],
      ['/bin/launchctl', 'bootout', service_target(job)],
      ['/bin/launchctl', 'print', service_target(job)],
    ]);
  });
  test('job identity isolates executor roots and launch attempts without PID authority', () => {
    const jobs = new DarwinWorkerJobs({ commands: { run: vi.fn() }, clock: () => 0 });
    const first = jobs.identity(process.cwd(), 'a'.repeat(32));
    expect(jobs.identity(process.cwd(), 'a'.repeat(32))).toEqual(first);
    expect(jobs.identity(process.cwd(), 'b'.repeat(32))).not.toEqual(first);
    expect(jobs.identity('/', 'a'.repeat(32))).not.toEqual(first);
    expect(() => service_target({ ...job, label: 'com.apple.example' })).toThrow('Invalid');
    expect(job_plist(first.label, ['/test'], {}, '/')).toContain(
      `<key>Nice</key><integer>${NICE}</integer>`,
    );
  });
});
