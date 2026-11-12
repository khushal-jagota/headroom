import { existsSync } from 'node:fs';
import { numeric_value } from '../../core/http/numeric_json.ts';
import { PlannerError } from '../../core/contracts.ts';
import type { CountPeriod, PeriodInterval } from '../contracts.ts';
export function invalid(message: string): PlannerError {
  return new PlannerError('validation', message);
}
export function calendar_date(value: unknown): Date {
  if (typeof value !== 'string') throw invalid('invalid measure date');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const compact = /^(\d{4})(\d{2})(\d{2})$/.exec(value);
    if (compact) {
      calendar_date(`${compact[1]}-${compact[2]}-${compact[3]}`);
      throw invalid('measure date must use YYYY-MM-DD');
    }
    const week = /^(\d{4})-?W(\d{2})(?:-?([1-7]))?$/.exec(value);
    if (week) {
      const year = Number(week[1]),
        index = Number(week[2]);
      const monday = (year: number): Date => {
        const d = new Date(0);
        d.setUTCFullYear(year, 0, 4);
        d.setUTCHours(0, 0, 0, 0);
        d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
        return d;
      };
      if (
        year >= 1 &&
        year <= 9999 &&
        index >= 1 &&
        index <= (monday(year + 1).getTime() - monday(year).getTime()) / 604800000
      )
        throw invalid('measure date must use YYYY-MM-DD');
    }
    throw invalid('invalid measure date');
  }
  const parsed = new Date(value + 'T00:00:00Z');
  if (
    !Number.isFinite(parsed.getTime()) ||
    value < '0001-01-01' ||
    value > '9999-12-31' ||
    parsed.toISOString().slice(0, 10) !== value
  )
    throw invalid('invalid measure date');
  return parsed;
}
export function timezone(value: string): string {
  try {
    if (
      typeof value !== 'string' ||
      value.startsWith('/') ||
      value.split('/').some((v) => v === '..' || v === '.' || !v) ||
      !existsSync('/usr/share/zoneinfo/' + value)
    )
      throw Error();
    const compatible = value.replace(/^(?:right|posix)\//, '');
    new Intl.DateTimeFormat('en', { timeZone: compatible }).format();
    return compatible;
  } catch {
    throw invalid('invalid measure timezone');
  }
}
export function local_date(now: number, zone: string): string {
  zone = timezone(zone);
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: zone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(new Date(now * 1000));
    const get = (key: string) => parts.find((p) => p.type === key)!.value;
    const result = `${get('year').padStart(4, '0')}-${get('month')}-${get('day')}`;
    calendar_date(result);
    return result;
  } catch {
    throw invalid('invalid measure instant');
  }
}
export function offset_date(day: string, offset: unknown): string {
  const d = calendar_date(day);
  d.setUTCDate(d.getUTCDate() + (numeric_value(offset) ?? NaN));
  if (!Number.isFinite(d.getTime()) || d.getUTCFullYear() < 1 || d.getUTCFullYear() > 9999)
    throw invalid('measure date offset is out of range');
  return d.toISOString().slice(0, 10);
}
export function date_range(first: string, last: string): string[] {
  const start = calendar_date(first).getTime(),
    end = calendar_date(last).getTime();
  if (start > end) throw invalid('measure date range is reversed');
  return Array.from({ length: (end - start) / 86400000 + 1 }, (_, i) => offset_date(first, i));
}
export function period_interval(day: string, period: CountPeriod): PeriodInterval {
  const d = calendar_date(day);
  let start = day;
  try {
    if (period === 'week') start = offset_date(day, -((d.getUTCDay() + 6) % 7));
    if (period === 'month') start = day.slice(0, 8) + '01';
    let end: string;
    if (period === 'month') {
      const e = calendar_date(start);
      e.setUTCMonth(e.getUTCMonth() + 1);
      if (e.getUTCFullYear() > 9999) throw Error();
      end = e.toISOString().slice(0, 10);
    } else end = offset_date(start, period === 'week' ? 7 : 1);
    return { start_date: start, end_date_exclusive: end };
  } catch {
    throw invalid('invalid period boundary');
  }
}
export function local_midnight(day: string, zone: string): number {
  let utc = calendar_date(day).getTime();
  const expected = utc;
  for (let i = 0; i < 4; i++) {
    const p = new Intl.DateTimeFormat('en-US', {
      timeZone: timezone(zone),
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(new Date(utc));
    const n = (k: string) => Number(p.find((v) => v.type === k)!.value);
    const represented = new Date(0);
    represented.setUTCFullYear(n('year'), n('month') - 1, n('day'));
    represented.setUTCHours(n('hour'), n('minute'), n('second'), 0);
    const delta = expected - represented.getTime();
    utc += delta;
    if (!delta) break;
  }
  return utc / 1000;
}
