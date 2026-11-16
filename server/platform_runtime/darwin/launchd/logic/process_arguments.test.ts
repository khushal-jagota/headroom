import { expect, test } from 'vitest';
import { has_worker_job_marker } from './process_arguments.ts';
const label = 'com.panels.worker.test';
function bytes(argv: string[], environment: string[]) {
  const argc = Buffer.alloc(4);
  argc.writeInt32LE(argv.length);
  return Buffer.concat([
    argc,
    Buffer.from('/program\0\0\0' + [...argv, ...environment, ''].join('\0') + '\0'),
  ]);
}
test('only an exact environment entry after argc arguments authorizes a marker', () => {
  expect(
    has_worker_job_marker(
      bytes(['/program', '--test'], ['OTHER=1', `PANELS_WORKER_JOB=${label}`]),
      label,
    ),
  ).toBe(true);
  expect(
    has_worker_job_marker(bytes(['/program', `PANELS_WORKER_JOB=${label}`], ['OTHER=1']), label),
  ).toBe(false);
  expect(
    has_worker_job_marker(bytes(['/program'], [`OTHER=PANELS_WORKER_JOB=${label}`]), label),
  ).toBe(false);
  expect(
    has_worker_job_marker(bytes(['/program'], [`PANELS_WORKER_JOB=${label}-different`]), label),
  ).toBe(false);
});
test('truncated and invalid argc responses grant no marker authority', () => {
  const valid = bytes(['/program'], [`PANELS_WORKER_JOB=${label}`]);
  expect(has_worker_job_marker(valid.subarray(0, valid.length - 2), label)).toBe(false);
  valid.writeInt32LE(-1);
  expect(has_worker_job_marker(valid, label)).toBe(false);
  expect(has_worker_job_marker(Buffer.alloc(3), label)).toBe(false);
});
