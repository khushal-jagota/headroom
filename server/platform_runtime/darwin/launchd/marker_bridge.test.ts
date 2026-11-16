/** Synthetic ABI bytes prove the final signal door without touching host processes. */
import { afterEach, expect, test, vi } from 'vitest';
const fake = vi.hoisted(() => ({
  load: vi.fn<() => { func: (signature: string) => unknown }>(() => ({ func: () => () => 0 })),
  errno: vi.fn(() => 0),
}));
vi.mock('koffi', () => ({ default: fake }));
import { darwin_signal_marked_worker_process } from '../../../executor/mac/platform.ts';
const original_platform = process.platform;
afterEach(() => {
  Object.defineProperty(process, 'platform', { value: original_platform });
  vi.restoreAllMocks();
});
test('each signal requires current matching UID, birth, and an exact environment marker', () => {
  Object.defineProperty(process, 'platform', { value: 'darwin' });
  const label = 'com.panels.worker.test';
  const argc = Buffer.alloc(4);
  argc.writeInt32LE(1);
  let environment = `PANELS_WORKER_JOB=${label}`;
  let uid = process.getuid!();
  let seconds = 10n;
  fake.load.mockReturnValue({
    func: (signature: string) => {
      if (signature.includes('proc_pidinfo'))
        return (_pid: number, _flavor: number, _arg: number, bytes: Buffer) => {
          bytes.writeUInt32LE(uid, 20);
          bytes.writeBigUInt64LE(seconds, 120);
          bytes.writeBigUInt64LE(20n, 128);
          return bytes.length;
        };
      if (signature.includes('sysctl('))
        return (mib: Int32Array, _count: number, output: Buffer, length: BigUint64Array) => {
          if (mib[1] === 8) {
            output.writeInt32LE(1024);
            return 0;
          }
          const args = Buffer.concat([
            argc,
            Buffer.from(`/program\0\0/program\0${environment}\0\0`),
          ]);
          args.copy(output);
          length[0] = BigInt(args.length);
          return 0;
        };
      throw new Error(`Unexpected native binding ${signature}`);
    },
  });
  const signal = vi.spyOn(process, 'kill').mockReturnValue(true);
  const target = { pid: 42, birth: '10:20' };
  expect(darwin_signal_marked_worker_process(target, label, 15, 2, () => 0)).toBe(true);
  expect(signal).toHaveBeenCalledWith(42, 15);
  signal.mockClear();
  uid++;
  expect(darwin_signal_marked_worker_process(target, label, 9, 2, () => 0)).toBe(false);
  uid--;
  seconds++;
  expect(darwin_signal_marked_worker_process(target, label, 9, 2, () => 0)).toBe(false);
  seconds--;
  environment = `OTHER=${label}`;
  expect(darwin_signal_marked_worker_process(target, label, 9, 2, () => 0)).toBe(false);
  expect(signal).not.toHaveBeenCalled();
});
