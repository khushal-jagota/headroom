import { SCHEDULE_CATCHUP_SECONDS } from '../configuration.ts';

/** Resolve local calendar occurrences through the host's timezone, including DST. */
export function latest_due_slot(local_time: string, now: Date): Date | null {
  const [hour, minute] = local_time.split(':').map(Number);
  for (const days_ago of [0, 1]) {
    const candidate = new Date(now);
    candidate.setDate(candidate.getDate() - days_ago);
    const year = candidate.getFullYear(),
      month = candidate.getMonth(),
      day = candidate.getDate();
    candidate.setHours(hour!, minute!, 0, 0);
    if (
      candidate.getFullYear() !== year ||
      candidate.getMonth() !== month ||
      candidate.getDate() !== day ||
      candidate.getHours() !== hour ||
      candidate.getMinutes() !== minute
    )
      continue;
    const age = (now.getTime() - candidate.getTime()) / 1000;
    if (age >= 0 && age <= SCHEDULE_CATCHUP_SECONDS) return candidate;
  }
  return null;
}

export function source_slot(local: Date): [string, string] {
  const n = (value: number, width = 2) => String(value).padStart(width, '0');
  return [
    `${n(local.getFullYear(), 4)}-${n(local.getMonth() + 1)}-${n(local.getDate())}`,
    `${n(local.getHours())}:${n(local.getMinutes())}`,
  ];
}
