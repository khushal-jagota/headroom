/** Click uses difflib.get_close_matches(word, commands), with its default 0.6 cutoff.
 * SequenceMatcher counts ordered longest contiguous matches, rather than edit distance.
 */
function similarity(candidate: string, word: string): number {
  const a = [...candidate];
  const b = [...word];
  if (a.length + b.length === 0) return 1;
  const positions = new Map<string, number[]>();
  for (let index = 0; index < b.length; index++) {
    const list = positions.get(b[index]) ?? [];
    list.push(index);
    positions.set(b[index], list);
  }
  // SequenceMatcher's automatic popular-element filter applies to its second input.
  if (b.length >= 200) {
    const threshold = Math.floor(b.length / 100) + 1;
    for (const [key, indices] of positions) if (indices.length > threshold) positions.delete(key);
  }
  const ranges = [[0, a.length, 0, b.length]];
  let matched = 0;
  while (ranges.length) {
    const [alo, ahi, blo, bhi] = ranges.pop()!;
    let bestA = alo;
    let bestB = blo;
    let bestSize = 0;
    let previous = new Map<number, number>();
    for (let i = alo; i < ahi; i++) {
      const current = new Map<number, number>();
      for (const j of positions.get(a[i]) ?? []) {
        if (j < blo) continue;
        if (j >= bhi) break;
        const size = (previous.get(j - 1) ?? 0) + 1;
        current.set(j, size);
        if (size > bestSize) {
          bestA = i - size + 1;
          bestB = j - size + 1;
          bestSize = size;
        }
      }
      previous = current;
    }
    while (bestA > alo && bestB > blo && a[bestA - 1] === b[bestB - 1]) {
      bestA--;
      bestB--;
      bestSize++;
    }
    while (
      bestA + bestSize < ahi &&
      bestB + bestSize < bhi &&
      a[bestA + bestSize] === b[bestB + bestSize]
    )
      bestSize++;
    if (!bestSize) continue;
    matched += bestSize;
    if (alo < bestA && blo < bestB) ranges.push([alo, bestA, blo, bestB]);
    if (bestA + bestSize < ahi && bestB + bestSize < bhi)
      ranges.push([bestA + bestSize, ahi, bestB + bestSize, bhi]);
  }
  return (2 * matched) / (a.length + b.length);
}
export function close_matches(word: string, candidates: readonly string[]): string[] {
  return candidates
    .map((name) => ({ name, ratio: similarity(name, word) }))
    .filter((match) => match.ratio >= 0.6)
    .sort((a, b) => b.ratio - a.ratio || (a.name < b.name ? 1 : a.name > b.name ? -1 : 0))
    .slice(0, 3)
    .map((match) => match.name);
}
