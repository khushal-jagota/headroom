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

// "Jul 10" — short-month day label, the date form the redesign speaks in.
export function shortMonthDayLabel(date: Date): string {
  return `${MONTHS_SHORT[date.getMonth()]} ${date.getDate()}`;
}

// "Saturday" — full weekday name (DayRoute's date line).
export function weekdayLabel(date: Date): string {
  return WEEKDAYS_LONG[date.getDay()];
}
