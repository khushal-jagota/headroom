import { AppValidationError } from '../contracts.ts';

/** CPython 3.12 pytime_double_to_denominator with _PyTime_ROUND_FLOOR. */
export function archive_timespec(value: number): readonly [bigint, number] {
  let seconds = Math.trunc(value);
  let nanoseconds = Math.floor((value - seconds) * 1e9);
  if (nanoseconds >= 1e9) {
    nanoseconds -= 1e9;
    seconds += 1;
  } else if (nanoseconds < 0) {
    nanoseconds += 1e9;
    seconds -= 1;
  }
  // The exclusive upper bound matters: Number(INT64_MAX) rounds to 2**63.
  if (!(seconds >= -(2 ** 63) && seconds < 2 ** 63))
    throw new AppValidationError('timestamp out of range for platform time_t');
  return [BigInt(seconds), nanoseconds];
}
