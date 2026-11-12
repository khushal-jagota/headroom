/** History labels use Node's bundled locale data through Intl. */
export interface HistoryFormatter {
  format(created_at: number): string;
  close(): void;
}

/** ICU accepts custom GMT offsets even when Intl rejects their zone names. */
function custom_gmt_offset(zone: string): number | null {
  const match = /^GMT([+-])([0-9:]+)$/.exec(zone);
  if (match === null) return null;
  const digits = match[2];
  let parts: string[];
  if (digits.includes(':')) {
    if (!/^\d{1,2}:\d{2}(?::\d{2})?$/.test(digits)) return null;
    parts = digits.split(':');
  } else {
    if (!/^\d{1,6}$/.test(digits)) return null;
    parts =
      digits.length <= 2
        ? [digits]
        : digits.length <= 4
          ? [digits.slice(0, -2), digits.slice(-2)]
          : [digits.slice(0, -4), digits.slice(-4, -2), digits.slice(-2)];
  }
  const [hours, minutes = 0, seconds = 0] = parts.map(Number);
  if (hours > 23 || minutes > 59 || seconds > 59) return null;
  return (match[1] === '-' ? -1 : 1) * (hours * 3600 + minutes * 60 + seconds) * 1000;
}

export function history_formatter(locale: string, timezone: string): HistoryFormatter {
  let selectedLocale = 'en-US';
  if (locale !== '') {
    try {
      if (Intl.DateTimeFormat.supportedLocalesOf(locale).length !== 0) selectedLocale = locale;
    } catch {
      /* PyICU falls back for an unknown locale rather than rejecting the read. */
    }
  }
  let selectedZone = timezone;
  let shift = 0;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: selectedZone });
  } catch {
    shift = custom_gmt_offset(timezone) ?? 0;
    selectedZone = 'UTC';
  }
  const formatter = new Intl.DateTimeFormat(selectedLocale, {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: selectedZone,
  });
  // An explicitly empty PyICU locale selects its root pattern, not the host locale.
  const root =
    locale === ''
      ? new Intl.DateTimeFormat('en-US', {
          timeZone: selectedZone,
          year: 'numeric',
          month: '2-digit',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
          hourCycle: 'h23',
        })
      : null;
  return {
    format(created_at) {
      const instant = created_at * 1000 + shift;
      if (root === null) return formatter.format(instant);
      const parts = Object.fromEntries(
        root.formatToParts(instant).map((part) => [part.type, part.value]),
      );
      return `${parts.year} M${parts.month} ${parts.day} ${parts.hour}:${parts.minute}`;
    },
    close() {},
  };
}
