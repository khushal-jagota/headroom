/** KERN_PROCARGS2: argc, executable, NUL padding, argc argv strings, then environment. */
import { WORKER_JOB_ENVIRONMENT_KEY } from '../configuration.ts';
export function has_worker_job_marker(bytes: Uint8Array, label: string): boolean {
  const raw = Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (raw.length < 5) return false;
  const argc = raw.readInt32LE(0);
  if (argc <= 0 || argc > raw.length) return false;
  let cursor = raw.indexOf(0, 4);
  if (cursor < 0) return false;
  while (cursor < raw.length && raw[cursor] === 0) cursor++;
  for (let argument = 0; argument < argc; argument++) {
    const end = raw.indexOf(0, cursor);
    if (end < 0) return false;
    cursor = end + 1;
  }
  const expected = Buffer.from(`${WORKER_JOB_ENVIRONMENT_KEY}=${label}`);
  let found = false;
  while (cursor < raw.length) {
    const end = raw.indexOf(0, cursor);
    if (end < 0) return false;
    if (end === cursor) return found;
    if (raw.subarray(cursor, end).equals(expected)) found = true;
    cursor = end + 1;
  }
  return found;
}
