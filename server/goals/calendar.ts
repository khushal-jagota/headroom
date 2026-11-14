import { inspect } from 'node:util';
import { RealClock } from '../core/clock.ts';
import type { Clock } from '../core/clock.ts';
import { calendar_date, local_date, local_midnight, offset_date } from '../measures/logic/dates.ts';
interface CalendarDate extends Date {
  calendar_iso?: string;
  calendar_zone?: string;
}
export function server_zone(): string {
  return process.env.TZ?.replace(/^:/, '') || 'localtime';
}
export function server_calendar(now: Date): Date {
  const copy = new Date(now) as CalendarDate;
  copy.calendar_zone = server_zone();
  return copy;
}
export function server_date(now: number): string {
  return local_date(now, server_zone());
}
export function with_calendar_iso(now: Date, calendar_iso?: string): Date {
  if (calendar_iso === undefined) return now;
  const copy = new Date(now) as CalendarDate;
  copy.calendar_iso = calendar_iso;
  return copy;
}
export function calendar_now(clock: Clock): Date {
  if (clock instanceof RealClock) return server_calendar(clock.now());
  return with_calendar_iso(clock.now(), clock.isoformat());
}
export function calendar_parts(now: Date, zone?: string): [string, number] {
  const date = now as CalendarDate,
    instant = now.getTime() / 1000;
  if (zone !== undefined || date.calendar_zone !== undefined) {
    const selected = zone ?? date.calendar_zone!,
      today = local_date(instant, selected);
    return [today, local_midnight(offset_date(today, 1), selected)];
  }
  const iso = date.calendar_iso ?? now.toISOString();
  const today = iso.slice(0, 10),
    offset = /(Z|[+-]\d{2}:\d{2})$/.exec(iso)?.[1] ?? 'Z';
  return [today, Date.parse(offset_date(today, 1) + 'T00:00:00' + offset) / 1000];
}

/** Goal validation messages are part of the public HTTP response. */
export function validate_iso_calendar_date(value: unknown, canonical_message: string): void {
  try {
    calendar_date(value);
  } catch (error) {
    if (
      typeof value !== 'string' ||
      (error instanceof Error && error.message === 'measure date must use YYYY-MM-DD')
    )
      throw new TypeError(canonical_message);
    const date = /^(?:(\d{4})-(\d{2})-(\d{2})|(\d{4})(\d{2})(\d{2}))$/.exec(value);
    if (date) {
      if (Number(date[1] ?? date[4]) === 0) throw new TypeError('year 0 is out of range');
      if (Number(date[2] ?? date[5]) < 1 || Number(date[2] ?? date[5]) > 12)
        throw new TypeError('month must be in 1..12');
      throw new TypeError('day is out of range for month');
    }
    throw new TypeError(`Invalid isoformat string: ${inspect(value)}`);
  }
}
