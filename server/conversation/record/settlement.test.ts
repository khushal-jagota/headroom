/** The save retry loop sorts errors: transient ones retry, others give up. */
import { expect, test } from 'vitest';
import { RecordSettlement, save_error_kind } from './settlement.ts';

const coded = (code: string) => Object.assign(new Error(code), { code });

// The sort decides between retrying forever and failing a conversation. SQLITE_ERROR moved:
// schema drift reports itself that way, and waiting does not repair it.
test('save errors sort into transient, permanent, and database unusable', () => {
  for (const code of [
    'SQLITE_BUSY',
    'SQLITE_LOCKED',
    'SQLITE_IOERR_WRITE',
    'SQLITE_FULL',
    'ENOSPC',
  ])
    expect(save_error_kind(coded(code))).toBe('transient');
  for (const code of ['SQLITE_CONSTRAINT_UNIQUE', 'SQLITE_ERROR', 'SQLITE_MISMATCH'])
    expect(save_error_kind(coded(code))).toBe('permanent');
  expect(save_error_kind(new TypeError('a JavaScript error inside the write'))).toBe('permanent');
  for (const code of ['SQLITE_CORRUPT', 'SQLITE_CORRUPT_INDEX', 'SQLITE_NOTADB'])
    expect(save_error_kind(coded(code))).toBe('database_unusable');
});

// An unusable database gives up at once, and every later write refuses without running.
test('an unusable database refuses every later write without running it', async () => {
  const settlement = new RecordSettlement();
  const corrupt = coded('SQLITE_CORRUPT');
  let attempts = 0;
  await expect(
    settlement.settle_recording('one', () => {
      attempts += 1;
      throw corrupt;
    }),
  ).rejects.toBe(corrupt);
  expect(attempts).toBe(1);
  expect(settlement.database_unusable_error).toBe(corrupt);
  let ran = false;
  await expect(
    settlement.write_recording(() => {
      ran = true;
    }),
  ).rejects.toBe(corrupt);
  expect(ran).toBe(false);
});
