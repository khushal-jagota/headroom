import { compact } from '../logic/json.ts';
import type { SortOperand, WindowPosition } from './contracts.ts';
const collators = new Map<string, Intl.Collator>();
const operands = new WeakMap<Buffer, SortOperand>();
function collator(locale: string, base: boolean): Intl.Collator {
  const key = `${locale}:${base}`;
  let value = collators.get(key);
  if (!value) { value = new Intl.Collator(locale, { sensitivity: base ? 'base' : 'variant' }); collators.set(key, value); }
  return value;
}
export function binaryCompare(left: string, right: string): number { return Buffer.compare(Buffer.from(left), Buffer.from(right)); }
export function sortKey(text: string, mode: SortOperand['mode'] = 'base', prefix: number | null = null): Buffer {
  return Buffer.from(compact({ text, mode, prefix } satisfies SortOperand));
}
function operand(value: Buffer): SortOperand {
  const cached = operands.get(value);
  if (cached) return cached;
  const parsed: unknown = JSON.parse(value.toString());
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('invalid navigation anchor');
  const row = parsed as SortOperand;
  if (!['binary', 'base', 'variant'].includes(row.mode) || typeof row.text !== 'string' || row.prefix !== null && !Number.isSafeInteger(row.prefix)
      || compact(row) !== compact({ mode: row.mode, text: row.text, prefix: row.prefix })) throw new Error('invalid navigation anchor');
  operands.set(value, row);
  return row;
}
function compareKey(locale: string, left: Buffer, right: Buffer): number {
  const a = operand(left), b = operand(right);
  if (a.mode !== b.mode || (a.prefix === null) !== (b.prefix === null)) throw new Error('invalid navigation anchor');
  if (a.prefix !== null && b.prefix !== null && a.prefix !== b.prefix) return a.prefix < b.prefix ? -1 : 1;
  return a.mode === 'binary' ? binaryCompare(a.text, b.text) : collator(locale, a.mode === 'base').compare(a.text, b.text);
}
export function comparePositions(locale: string, left: WindowPosition, right: WindowPosition): number {
  if (left.length !== right.length) throw new Error('invalid navigation anchor arity');
  const offset = left.length - 5;
  for (let index = 0; index <= offset; index++) {
    const a = left[index] as number, b = right[index] as number;
    if (a !== b) return a < b ? -1 : 1;
  }
  return compareKey(locale, left[offset + 1] as Buffer, right[offset + 1] as Buffer)
    || compareKey(locale, left[offset + 2] as Buffer, right[offset + 2] as Buffer)
    || binaryCompare(left[offset + 3] as string, right[offset + 3] as string)
    || binaryCompare(left[offset + 4] as string, right[offset + 4] as string);
}
