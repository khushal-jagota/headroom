export type DiffLineKind = 'context' | 'delete' | 'add';

export interface DiffLine {
  readonly kind: DiffLineKind;
  readonly text: string;
  readonly oldLine: number | null;
  readonly newLine: number | null;
}

export interface LineDiffOptions {
  readonly oldStartLine?: number | null;
  readonly newStartLine?: number | null;
  readonly emptyNewTextMeansNoLines?: boolean;
  readonly normalizedSnapshotFragments?: boolean;
}

interface EditOperation {
  kind: DiffLineKind;
  text: string;
}

function backtrack(
  trace: ReadonlyArray<ReadonlyMap<number, number>>,
  oldLines: readonly string[],
  newLines: readonly string[],
): EditOperation[] {
  let x = oldLines.length;
  let y = newLines.length;
  const reversed: EditOperation[] = [];
  for (let distance = trace.length - 1; distance >= 0; distance -= 1) {
    const diagonal = x - y;
    const frontier = trace[distance];
    const deletionX = frontier.get(diagonal - 1) ?? Number.NEGATIVE_INFINITY;
    const additionX = frontier.get(diagonal + 1) ?? Number.NEGATIVE_INFINITY;
    const previousDiagonal = diagonal === -distance
      || (diagonal !== distance && deletionX < additionX)
      ? diagonal + 1
      : diagonal - 1;
    const previousX = frontier.get(previousDiagonal) ?? 0;
    const previousY = previousX - previousDiagonal;
    while (x > previousX && y > previousY) {
      reversed.push({ kind: 'context', text: oldLines[x - 1] });
      x -= 1;
      y -= 1;
    }
    if (distance === 0) break;
    if (x === previousX) {
      y -= 1;
      reversed.push({ kind: 'add', text: newLines[y] });
    } else {
      x -= 1;
      reversed.push({ kind: 'delete', text: oldLines[x] });
    }
  }
  return reversed.reverse();
}

function shortestEditScript(oldLines: readonly string[], newLines: readonly string[]): EditOperation[] {
  const maximumDistance = oldLines.length + newLines.length;
  const furthestX = new Map<number, number>([[1, 0]]);
  const trace: Array<Map<number, number>> = [];
  for (let distance = 0; distance <= maximumDistance; distance += 1) {
    trace.push(new Map(furthestX));
    for (let diagonal = -distance; diagonal <= distance; diagonal += 2) {
      const deletionX = furthestX.get(diagonal - 1) ?? Number.NEGATIVE_INFINITY;
      const additionX = furthestX.get(diagonal + 1) ?? Number.NEGATIVE_INFINITY;
      let x = diagonal === -distance || (diagonal !== distance && deletionX < additionX)
        ? additionX
        : deletionX + 1;
      if (!Number.isFinite(x)) x = 0;
      let y = x - diagonal;
      while (x < oldLines.length && y < newLines.length && oldLines[x] === newLines[y]) {
        x += 1;
        y += 1;
      }
      furthestX.set(diagonal, x);
      if (x >= oldLines.length && y >= newLines.length) return backtrack(trace, oldLines, newLines);
    }
  }
  return [];
}

export function lineDiff(
  oldText: string | null | undefined,
  newText: string,
  options: LineDiffOptions = {},
): DiffLine[] {
  const split = options.normalizedSnapshotFragments ? snapshotLines : ordinaryLines;
  const newLines = options.emptyNewTextMeansNoLines && newText === '' ? [] : split(newText);
  const operations = oldText == null
    ? newLines.map((text): EditOperation => ({ kind: 'add', text }))
    : shortestEditScript(split(oldText), newLines);
  let oldLine = options.oldStartLine ?? null;
  let newLine = options.newStartLine ?? null;
  const ordinaryOrigins = options.oldStartLine === undefined && options.newStartLine === undefined;
  if (ordinaryOrigins) {
    oldLine = 1;
    newLine = 1;
  }
  return operations.map((operation) => {
    if (operation.kind === 'context') {
      const line = { ...operation, oldLine, newLine };
      if (oldLine !== null) oldLine += 1;
      if (newLine !== null) newLine += 1;
      return line;
    }
    if (operation.kind === 'delete') {
      const line = { ...operation, oldLine, newLine: null };
      if (oldLine !== null) oldLine += 1;
      return line;
    }
    const line = { ...operation, oldLine: null, newLine };
    if (newLine !== null) newLine += 1;
    return line;
  });
}

function ordinaryLines(text: string): string[] {
  return text.split('\n');
}

function snapshotLines(text: string): string[] {
  if (text === '') return [];
  const lines = text.split('\n');
  if (lines.at(-1) === '') lines.pop();
  return lines;
}
