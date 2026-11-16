import { PlannerError } from '../../core/contracts.ts';
import { ID_PREFIXES, SLUG_ALPHABET } from '../../core/ids.ts';

export const RECAP_MAX_CHARS = 80;
const prefixes = [
  ...Object.values(ID_PREFIXES),
  'block',
  'chart',
  'credential',
  'dataset',
  'goal',
  'measure',
  'source',
  'source_run',
];
const panels_id = new RegExp(
  `(?<![\\p{L}\\p{N}_])(?:(?:${prefixes.join('|')})_[${SLUG_ALPHABET}]{8}|claim_[${SLUG_ALPHABET}]{12})(?![\\p{L}\\p{N}_])`,
  'u',
);
const uuid =
  /(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])/i;
const hex_run = /(?<![0-9a-zA-Z])[0-9a-f]{7,40}(?![0-9a-zA-Z])/gi;

export function validate_recap(recap: string): void {
  const text = recap.trim(),
    length = Array.from(text).length;
  if (length > RECAP_MAX_CHARS)
    throw new PlannerError(
      'validation',
      `Recap is ${length} characters. Rewrite it in ${RECAP_MAX_CHARS} or fewer.`,
    );
  const found =
    panels_id.exec(text)?.[0] ??
    uuid.exec(text)?.[0] ??
    [...text.matchAll(hex_run)].find(([value]) => /[0-9]/.test(value) && /[a-f]/i.test(value))?.[0];
  if (found)
    throw new PlannerError(
      'validation',
      `Recap names an ID (${found}). Say what the work is in plain words and rewrite it.`,
    );
}

export const TICKET_RECAP_PREVIEW_CHARS = 160;
export function recap_preview(recap: string): string {
  const compact = recap.trim().replace(/\s+/gu, ' ');
  const points = Array.from(compact);
  return points.length <= TICKET_RECAP_PREVIEW_CHARS
    ? compact
    : points
        .slice(0, TICKET_RECAP_PREVIEW_CHARS - 1)
        .join('')
        .trimEnd() + '…';
}
