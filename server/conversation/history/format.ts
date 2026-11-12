/** Use the same system ICU date formatter as PyICU, without Intl emulation. */
import { realpathSync } from 'node:fs';
import koffi from 'koffi';

// Resolve the system library major, which need not match Node's bundled ICU.
const architecture =
  process.arch === 'x64' ? 'x86_64' : process.arch === 'arm64' ? 'aarch64' : process.arch;
const library_path = realpathSync(`/usr/lib/${architecture}-linux-gnu/libicui18n.so`);
const suffix = '_' + /\.so\.(\d+)/.exec(library_path)![1];
const library = koffi.load(library_path);
const open = library.func(
  `void *udat_open${suffix}(int timeStyle, int dateStyle, const char *locale, const uint16_t *tzID, int32_t tzIDLength, const uint16_t *pattern, int32_t patternLength, _Inout_ int32_t *status)`,
);
const format = library.func(
  `int32_t udat_format${suffix}(const void *format, double date, uint16_t *result, int32_t capacity, void *position, _Inout_ int32_t *status)`,
);
const close = library.func(`void udat_close${suffix}(void *format)`);

export interface HistoryFormatter {
  format(created_at: number): string;
  close(): void;
}

export function history_formatter(locale: string, timezone: string): HistoryFormatter {
  const status = [0];
  // Keep even an empty ID non-null: null asks ICU for the host's default zone.
  const zone = new Uint16Array(timezone.length + 1);
  for (let index = 0; index < timezone.length; index++) zone[index] = timezone.charCodeAt(index);
  // ICU's enum order is FULL=0, LONG=1, MEDIUM=2, SHORT=3. C API time comes first.
  const handle: unknown = open(3, 2, locale, zone, timezone.length, null, 0, status);
  if (status[0] > 0 || handle === null) {
    if (handle !== null) close(handle);
    throw new Error(`ICU history formatter failed: ${status[0]}`);
  }
  return {
    format(created_at) {
      // Overflow (15) is the documented result when querying the UTF-16 size.
      const state = [0];
      const length = format(handle, created_at * 1000, null, 0, null, state) as number;
      if (state[0] > 0 && state[0] !== 15) throw new Error(`ICU history date failed: ${state[0]}`);
      const output = new Uint16Array(length + 1);
      state[0] = 0;
      const written = format(
        handle,
        created_at * 1000,
        output,
        output.length,
        null,
        state,
      ) as number;
      if (state[0] > 0) throw new Error(`ICU history date failed: ${state[0]}`);
      return Array.from(output.subarray(0, written), (unit) => String.fromCharCode(unit)).join('');
    },
    close() {
      close(handle);
    },
  };
}
