export const PANELS_CODEX_FILE_EDIT_METADATA_KEY =
  'https://panels.local/acp/codex-file-edit/v1';

export interface PanelsFileEditMetadata {
  readonly operation: 'add' | 'update' | 'delete';
  readonly detailState: 'complete' | 'truncated' | 'omitted';
  readonly oldStartLine: number | null;
  readonly newStartLine: number | null;
  readonly oldLineCount: number | null;
  readonly newLineCount: number | null;
}

function nullablePositiveInteger(value: unknown): value is number | null {
  return value === null || (Number.isInteger(value) && Number(value) > 0);
}

function nullableNonNegativeInteger(value: unknown): value is number | null {
  return value === null || (Number.isInteger(value) && Number(value) >= 0);
}

export function panelsFileEditMetadata(value: unknown): PanelsFileEditMetadata | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  const candidates = Object.entries(value)
    .map(([key, candidate]) => ({ ordinal: metadataKeyOrdinal(key), candidate }))
    .filter((item): item is { ordinal: bigint; candidate: unknown } => item.ordinal !== null)
    .sort((left, right) => left.ordinal > right.ordinal ? -1 : left.ordinal < right.ordinal ? 1 : 0);
  for (const { candidate } of candidates) {
    if (candidate === null || typeof candidate !== 'object' || Array.isArray(candidate)) continue;
    const metadata = candidate as Record<string, unknown>;
    if (!['add', 'update', 'delete'].includes(String(metadata.operation))
      || !['complete', 'truncated', 'omitted'].includes(String(metadata.detailState))
      || !nullablePositiveInteger(metadata.oldStartLine)
      || !nullablePositiveInteger(metadata.newStartLine)
      || !nullableNonNegativeInteger(metadata.oldLineCount)
      || !nullableNonNegativeInteger(metadata.newLineCount)) continue;
    return metadata as unknown as PanelsFileEditMetadata;
  }
  return null;
}

function metadataKeyOrdinal(key: string): bigint | null {
  if (key === PANELS_CODEX_FILE_EDIT_METADATA_KEY) return 1n;
  const prefix = `${PANELS_CODEX_FILE_EDIT_METADATA_KEY}#`;
  if (!key.startsWith(prefix)) return null;
  const suffix = key.slice(prefix.length);
  if (!/^(?:[2-9]|[1-9][0-9]+)$/.test(suffix)) return null;
  return BigInt(suffix);
}
