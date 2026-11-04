export type Point = [x: number, value: number];
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

/** The line through every value, sampled densely on x from 0 to 1 in data units. Monotone cubic tangents keep a smooth curve from swinging past the data. */
export function curveFor(values: number[], smooth: boolean): Point[] {
  if (values.some(value => !Number.isFinite(value))) {
    const result: Point[] = []; let start = 0;
    while (start < values.length) {
      if (!Number.isFinite(values[start])) { result.push([start / Math.max(1, values.length - 1), NaN]); start++; continue; }
      let end = start + 1;
      while (end < values.length && Number.isFinite(values[end])) end++;
      if (end - start === 1) result.push([start / Math.max(1, values.length - 1), values[start]]);
      else for (const [x, y] of curveFor(values.slice(start, end), smooth)) result.push([(start + x * (end - start - 1)) / (values.length - 1), y]);
      start = end;
    }
    return result;
  }
  if (values.length === 0) return [];
  if (values.length === 1) return [[0, values[0]!], [1, values[0]!]];
  const last = values.length - 1, step = 1 / last;
  if (!smooth) return values.map((value, index) => [index * step, value]);
  const slopes = values.slice(1).map((value, index) => (value - values[index]!) / step);
  const tangents = values.map((_, index) => index === 0 || index === last ? 0 : (Math.sign(slopes[index - 1]!) + Math.sign(slopes[index]!)) * Math.min(Math.abs(slopes[index - 1]!), Math.abs(slopes[index]!), Math.abs(slopes[index - 1]! + slopes[index]!) / 4) || 0);
  if (last > 1) { tangents[0] = (3 * slopes[0]! - tangents[1]!) / 2; tangents[last] = (3 * slopes[last - 1]! - tangents[last - 1]!) / 2; }
  else { tangents[0] = slopes[0]!; tangents[1] = slopes[0]!; }
  const samples = Math.max(1, Math.min(16, Math.round(192 / last)));
  const points: Point[] = [];
  for (let index = 0; index < last; index++) {
    const low = Math.min(values[index]!, values[index + 1]!), high = Math.max(values[index]!, values[index + 1]!);
    for (let sample = 0; sample < samples; sample++) {
      const t = sample / samples, t2 = t * t, t3 = t2 * t;
      const value = (2 * t3 - 3 * t2 + 1) * values[index]! + (t3 - 2 * t2 + t) * step * tangents[index]! + (3 * t2 - 2 * t3) * values[index + 1]! + (t3 - t2) * step * tangents[index + 1]!;
      points.push([(index + t) * step, clamp(value, low, high)]);
    }
  }
  points.push([1, values[last]!]);
  return points;
}

/** Value of a sampled line at x, straight between samples. */
export function valueAt(shape: Point[], x: number) {
  if (!shape.length) return NaN;
  let low = 0, high = shape.length - 1;
  if (x <= shape[low]![0]) return shape[low]![1];
  if (x >= shape[high]![0]) return shape[high]![1];
  while (high - low > 1) { const middle = (low + high) >> 1; if (shape[middle]![0] < x) low = middle; else high = middle; }
  if (shape[low]![0] === x) return shape[low]![1];
  if (shape[high]![0] === x) return shape[high]![1];
  const [x0, y0] = shape[low]!, [x1, y1] = shape[high]!;
  return x1 === x0 ? y1 : y0 + ((y1 - y0) * (x - x0)) / (x1 - x0);
}

/** Sample each phase separately. A nonfinite marker also stops crosshair interpolation. */
export function segmentedCurve(values: number[], positions: number[], breaks: boolean[], smooth: boolean): Point[] {
  if (values.length === 1) return curveFor(values, smooth);
  const result: Point[] = [];
  let start = 0;
  const append = (end: number) => {
    const run = curveFor(values.slice(start, end), smooth);
    const last = end - start - 1;
    for (const [x, y] of run) {
      if (last === 0) { if (!result.length || result.at(-1)![0] !== positions[start]) result.push([positions[start], y]); continue; }
      const at = x * last, index = Math.min(last - 1, Math.floor(at)), fraction = at - index;
      result.push([positions[start + index] + fraction * (positions[start + index + 1] - positions[start + index]), y]);
    }
  };
  for (let index = 1; index < values.length; index++) {
    if (!breaks[index]) continue;
    append(index);
    result.push([(positions[index - 1] + positions[index]) / 2, NaN]);
    start = index;
  }
  if (values.length) append(values.length);
  return result;
}

/** Morph only the next shape's finite segments, including every explicit gap marker. */
export function morphShape(from: Point[], target: Point[], xs: number[], progress: number): Point[] {
  return xs.map(x => {
    const next = valueAt(target, x), previous = valueAt(from, x);
    return [x, Number.isFinite(next) ? Number.isFinite(previous) ? previous + (next - previous) * progress : next : NaN];
  });
}

/** Backend comparison samples own formula colors. Missing comparisons are neutral. */
export function comparisonAt(samples: Array<[number, boolean | null]>, x: number): boolean | null {
  if (!samples.length) return null;
  let low = 0, high = samples.length - 1;
  while (high - low > 1) { const middle = (low + high) >> 1; if (samples[middle][0] < x) low = middle; else high = middle; }
  return Math.abs(samples[high][0] - x) < Math.abs(samples[low][0] - x) ? samples[high][1] : samples[low][1];
}
