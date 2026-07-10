const MONTHS_LONG = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December"
];

const MONTHS_SHORT = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec"
];

const WEEKDAYS_LONG = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday"
];

// "July 5" — full month name + day of month (DayRoute's date line).
export function monthDayLabel(date: Date): string {
  return `${MONTHS_LONG[date.getMonth()]} ${date.getDate()}`;
}

// "Saturday" — full weekday name (DayRoute's date line).
export function weekdayLabel(date: Date): string {
  return WEEKDAYS_LONG[date.getDay()];
}

// Relative age of a unix timestamp: "" (unparseable), "today", "<n>d" within a
// week, else a short "Mon D" date (IdeasRoute's created-at column).
export function relativeDayLabel(seconds: unknown): string {
  const secs = Number(seconds);
  if (!Number.isFinite(secs)) return "";
  const days = Math.floor((Date.now() / 1000 - secs) / 86400);
  if (days <= 0) return "today";
  if (days < 7) return `${days}d`;
  const date = new Date(secs * 1000);
  return `${MONTHS_SHORT[date.getMonth()]} ${date.getDate()}`;
}
