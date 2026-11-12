import { randomInt } from 'node:crypto';
export const ID_PREFIXES = {
  manager: 'si',
  ticket: 't',
  schedule: 'schedule',
  idea: 'idea',
  run: 'run',
} as const;
export const SLUG_ALPHABET = '0123456789abcdefghjkmnpqrstuvwxyz';
function slug(length: number): string {
  return Array.from({ length }, () => SLUG_ALPHABET[randomInt(SLUG_ALPHABET.length)]).join('');
}
export function newId(prefix: string): string {
  return `${prefix}_${slug(8)}`;
}
export function newClaim(): string {
  return `claim_${slug(12)}`;
}
