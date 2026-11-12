import type { Config } from './config.ts';
export interface Clock {
  now(): Date;
  nowUnix(): number;
  isoformat(): string;
}
function parseNow(raw: string): {
  date: Date;
  iso: string;
} {
  const invalid = (): never => {
    throw new Error(`Invalid isoformat string: '${raw}'`);
  };
  const n = (value: number, width = 2): string => String(value).padStart(width, '0');
  let year: number, month: number, day: number, rest: string;
  const week = /^(\d{4})-?W(\d{2})(?:-?([1-7]))?/.exec(raw);
  const calendar = /^(?:(\d{4})-(\d{2})-(\d{2})|(\d{4})(\d{2})(\d{2}))/.exec(raw);
  if (week) {
    year = Number(week[1]);
    const monday = (year: number): Date => {
      const d = new Date(0);
      d.setUTCFullYear(year, 0, 4);
      d.setUTCHours(0, 0, 0, 0);
      d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
      return d;
    };
    const start = monday(year),
      weeks = (monday(year + 1).getTime() - start.getTime()) / (7 * 86400000);
    if (year < 1 || Number(week[2]) < 1 || Number(week[2]) > weeks) invalid();
    start.setUTCDate(start.getUTCDate() + 7 * (Number(week[2]) - 1) + Number(week[3] ?? 1) - 1);
    year = start.getUTCFullYear();
    month = start.getUTCMonth() + 1;
    day = start.getUTCDate();
    rest = raw.slice(week[0].length);
  } else if (calendar) {
    year = Number(calendar[1] ?? calendar[4]);
    month = Number(calendar[2] ?? calendar[5]);
    day = Number(calendar[3] ?? calendar[6]);
    rest = raw.slice(calendar[0].length);
  } else return invalid();
  const lastDay = new Date(0);
  lastDay.setUTCFullYear(year, month, 0);
  if (year < 1 || year > 9999 || month < 1 || month > 12 || day < 1 || day > lastDay.getUTCDate())
    invalid();
  let hour = 0,
    minute = 0,
    second = 0,
    micros = '',
    offset: string | undefined;
  if (rest) {
    // Python permits any single separator, including a Unicode code point.
    const separator = Array.from(rest)[0]!;
    const time =
      /^(\d{2})(?::?(\d{2}))?(?::?(\d{2}))?(?:[.,](\d+))?(Z|[+-]\d{2}(?::?\d{2})?)?$/.exec(
        rest.slice(separator.length),
      );
    if (!time) return invalid();
    hour = Number(time[1]);
    minute = Number(time[2] ?? 0);
    second = Number(time[3] ?? 0);
    if (hour > 23 || minute > 59 || second > 59) invalid();
    const fraction = time[4]?.slice(0, 6).padEnd(6, '0') ?? '';
    if (Number(fraction)) micros = '.' + fraction;
    if (time[5]) {
      if (time[5] === 'Z') offset = '+00:00';
      else {
        const zone = /^([+-])(\d{2})(?::?(\d{2}))?$/.exec(time[5])!;
        if (Number(zone[2]) > 23 || Number(zone[3] ?? 0) > 59) invalid();
        offset = `${zone[1]}${zone[2]}:${zone[3] ?? '00'}`;
        if (Number(zone[2]) === 0 && Number(zone[3] ?? 0) === 0) offset = '+00:00';
      }
    }
  }
  const local = `${n(year, 4)}-${n(month)}-${n(day)}T${n(hour)}:${n(minute)}:${n(second)}${micros}`;
  const date = new Date(local + (offset ?? ''));
  if (!Number.isFinite(date.getTime())) return invalid();
  const normalizedLocal = offset
    ? local
    : `${n(date.getFullYear(), 4)}-${n(date.getMonth() + 1)}-${n(date.getDate())}T${n(date.getHours())}:${n(date.getMinutes())}:${n(date.getSeconds())}${micros}`;
  return { date, iso: normalizedLocal + (offset ?? localOffset(date)) };
}
function localOffset(date: Date): string {
  const m = -date.getTimezoneOffset();
  return `${m < 0 ? '-' : '+'}${String(Math.floor(Math.abs(m) / 60)).padStart(2, '0')}:${String(Math.abs(m) % 60).padStart(2, '0')}`;
}
export class RealClock implements Clock {
  now(): Date {
    return new Date();
  }
  nowUnix(): number {
    return Math.floor(this.now().getTime() / 1000);
  }
  isoformat(): string {
    const date = this.now();
    const n = (value: number, width = 2): string => String(value).padStart(width, '0');
    const fraction = date.getMilliseconds() ? `.${n(date.getMilliseconds(), 3)}000` : '';
    return `${n(date.getFullYear(), 4)}-${n(date.getMonth() + 1)}-${n(date.getDate())}T${n(date.getHours())}:${n(date.getMinutes())}:${n(date.getSeconds())}${fraction}${localOffset(date)}`;
  }
}
export class TestClock implements Clock {
  #value: {
    date: Date;
    iso: string;
  };
  constructor(raw: string) {
    this.#value = parseNow(raw);
  }
  now(): Date {
    return new Date(this.#value.date);
  }
  nowUnix(): number {
    return Math.floor(this.#value.date.getTime() / 1000);
  }
  isoformat(): string {
    return this.#value.iso;
  }
  set(raw: string): void {
    this.#value = parseNow(raw);
  }
}
export function buildClock(config: Config): Clock {
  return config.test_mode && config.fake_now ? new TestClock(config.fake_now) : new RealClock();
}
