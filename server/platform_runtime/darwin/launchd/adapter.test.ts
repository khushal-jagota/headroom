import { describe, expect, test, vi } from 'vitest';
import { DarwinWorkerJobs, service_target } from './adapter.ts';
import { job_plist } from './transport.ts';
import { NICE } from '../../../worker_supervision/config.ts';
import type { WorkerJobIdentity } from '../../../worker_supervision/contracts.ts';
import { WORKER_SWEEP_LIMIT_NOTICE } from './configuration.ts';

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
  test('XML-invalid environment values are omitted with names-only notices', () => {
    const notice = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      const plist = job_plist(
        job.label,
        ['/test'],
        {
          LESS_TERMCAP_mb: '\u001b[01mprivate-value',
          'INVALID\u0001NAME': 'another-private-value',
          GOOD: 'allowed\t\n\r<&',
        },
        '/',
      );
      expect(plist).not.toContain('LESS_TERMCAP_mb');
      expect(plist).not.toContain('INVALID');
      expect(plist).toContain('<key>GOOD</key><string>allowed\t\n\r&lt;&amp;</string>');
      expect(notice.mock.calls.map((call) => call[1])).toEqual([
        '"LESS_TERMCAP_mb"',
        JSON.stringify('INVALID\u0001NAME'),
      ]);
      expect(JSON.stringify(notice.mock.calls)).not.toContain('private-value');
    } finally {
      notice.mockRestore();
    }
  });
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
    const plist = job_plist(first.label, ['/test'], { PANELS_WORKER_JOB: 'inherited-spoof' }, '/');
    expect(plist).toContain(`<key>PANELS_WORKER_JOB</key><string>${first.label}</string>`);
    expect(plist).not.toContain('inherited-spoof');
  });
  test('already absent jobs sweep current marked processes with TERM then a fresh KILL scan', async () => {
    const process = { pid: 42, birth: '10:20' };
    const scan = vi.fn().mockReturnValue({ processes: [process], unreadable: 0 });
    const signal = vi.fn().mockReturnValue(true);
    const notice = vi.fn();
    const jobs = new DarwinWorkerJobs({
      commands: { run: vi.fn() },
      kernel: { scan, signal },
      notice,
    });
    vi.spyOn(jobs, 'status').mockResolvedValue('absent');
    await expect(jobs.stop(job, performance.now() / 1000 + 2)).resolves.toBeUndefined();
    expect(scan).toHaveBeenCalledTimes(2);
    expect(signal.mock.calls.map((call) => call.slice(0, 3))).toEqual([
      [process, job.label, 15],
      [process, job.label, 9],
    ]);
    expect(notice).toHaveBeenCalledWith(WORKER_SWEEP_LIMIT_NOTICE);
  });
  test('process inspection failures after job absence remain notices and cannot fence restart', async () => {
    const notice = vi.fn();
    const signal = vi.fn();
    const jobs = new DarwinWorkerJobs({
      commands: { run: vi.fn() },
      clock: () => 0,
      kernel: {
        scan: () => {
          throw new Error('sysctl denied');
        },
        signal,
      },
      notice,
    });
    vi.spyOn(jobs, 'status').mockResolvedValue('absent');
    await expect(jobs.stop(job, 2)).resolves.toBeUndefined();
    expect(signal).not.toHaveBeenCalled();
    expect(notice).toHaveBeenCalledWith(WORKER_SWEEP_LIMIT_NOTICE);
    expect(notice).toHaveBeenCalledWith(expect.stringContaining('did not finish'));
  });
});
