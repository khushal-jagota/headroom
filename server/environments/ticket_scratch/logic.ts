import type { TicketStates } from './contracts.ts';
import { path_is_within } from '../../core/path_containment.ts';
import { SETTLE_SECONDS } from './configuration.ts';

const TICKET_ID = /t_[0-9abcdefghjkmnpqrstuvwxyz]{8}(?![a-z0-9])/;
export function ticket_id_of_name(name: string): string | null {
  const match = TICKET_ID.exec(name);
  return match?.index === 0 ? match[0] : null;
}
export function ticket_id_of_branch(branch: string | null): string | null {
  return TICKET_ID.exec(branch ?? '')?.[0] ?? null;
}
export function ticket_id_of_path(path: string): string | null {
  for (const part of path.split('/')) {
    const owner = ticket_id_of_name(part);
    if (owner !== null) return owner;
  }
  return null;
}
export function finished(ticket_id: string, states: TicketStates, now: number): boolean {
  const state = states.get(ticket_id);
  return state === undefined || (state[0] === 'done' && state[1] <= now - SETTLE_SECONDS);
}
/** Stable native lexical order for cleanup reports. */
export function compare_strings(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}
export function compare_paths(left: string, right: string): number {
  const a = left.split('/'),
    b = right.split('/');
  for (let index = 0; index < Math.min(a.length, b.length); index++) {
    const order = compare_strings(a[index]!, b[index]!);
    if (order !== 0) return order;
  }
  return a.length - b.length;
}
/** Release callers retain this argument order until their shared-helper migration. */
export function inside(path: string, root: string): boolean {
  return path_is_within(root, path);
}
