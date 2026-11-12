/** CPython 3.12+'s compensated float sum, including its nonfinite result rule. */
export function python_sum(values: Iterable<number>): number {
  let high = 0,
    low = 0;
  for (const value of values) {
    const total = high + value;
    low += Math.abs(high) >= Math.abs(value) ? high - total + value : value - total + high;
    high = total;
  }
  return low !== 0 && Number.isFinite(low) ? high + low : high;
}
