/** A derived physical order for Intl comparisons. Public anchors retain semantic tuples. */
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import type { Membership, Position, WindowPosition } from './contracts.ts';
import { compact } from '../logic/json.ts';
import { NavigationUnavailable } from '../logic/errors.ts';
import { comparePositions } from './ordering.ts';
export type OrderedGroup = readonly [section: string, profile: string, locale: string];
interface OrderedMember extends Membership { position: number }
function tuple(row: Membership): Position { return [row.rank, row.title_key, row.id_key, row.kind, row.id]; }
export function prepareOrdered(tx: TransactionSql): void {
  tx.run('CREATE TABLE IF NOT EXISTS navigation_view_ordered (section_key TEXT NOT NULL,profile TEXT NOT NULL,locale TEXT NOT NULL,position INTEGER NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,rank INTEGER NOT NULL,title_key BLOB NOT NULL,id_key BLOB NOT NULL,PRIMARY KEY(section_key,profile,locale,position),UNIQUE(section_key,profile,locale,kind,id))');
}
/** Only a changed membership or comparator operand requires replacement of its group. */
export function reorderGroup(tx: TransactionSql, group: OrderedGroup): void {
  const rows = tx.all<Membership>('SELECT * FROM navigation_view_memberships WHERE section_key=? AND profile=? AND locale=?', [...group]);
  rows.sort((left, right) => comparePositions(group[2], tuple(left), tuple(right)));
  tx.run('DELETE FROM navigation_view_ordered WHERE section_key=? AND profile=? AND locale=?', [...group]);
  for (let position = 0; position < rows.length; position++) {
    const row = rows[position];
    tx.run('INSERT INTO navigation_view_ordered VALUES(?,?,?,?,?,?,?,?,?)', [...group, position, row.kind, row.id, row.rank, row.title_key, row.id_key]);
  }
}
function lowerBound(values: readonly number[], target: number, after = false): number {
  let low = 0, high = values.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (values[middle] < target || after && values[middle] === target) low = middle + 1;
    else high = middle;
  }
  return low;
}
export interface OrderedRows {
  row(index: number): WindowPosition;
  /** First row strictly greater than the boundary, or greater/equal for before pages. */
  seek(boundary: WindowPosition, after: boolean): number;
  index(row: WindowPosition): number;
}
/** Point lookups and binary search bound page reads independently of archive size. */
export function orderedRows(sql: ReadSql, group: OrderedGroup, total: number, review: boolean, priority: ReadonlySet<string>): OrderedRows {
  const priorityPositions: number[] = [];
  if (review) for (const encoded of priority) {
    const [kind, id] = JSON.parse(encoded) as [string, string];
    const member = sql.get<{position: number}>('SELECT position FROM navigation_view_ordered WHERE section_key=? AND profile=? AND locale=? AND kind=? AND id=?', [...group, kind, id]);
    if (member) priorityPositions.push(member.position);
  }
  priorityPositions.sort((a, b) => a - b);
  const ordinaryCount = total - priorityPositions.length;
  const cache = new Map<number, WindowPosition>();
  const indexes = new Map<string, number>();
  function physicalPosition(logical: number): number {
    if (!review || !priorityPositions.length) return logical;
    if (logical >= ordinaryCount) return priorityPositions[logical - ordinaryCount];
    // Select the logical-th non-priority position without visits to skipped rows.
    let low = 0, high = total;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      const ordinaryThrough = middle + 1 - lowerBound(priorityPositions, middle, true);
      if (ordinaryThrough > logical) high = middle;
      else low = middle + 1;
    }
    return low;
  }
  function row(index: number): WindowPosition {
    const old = cache.get(index);
    if (old) return old;
    const member = sql.get<OrderedMember>('SELECT kind,id,rank,title_key,id_key FROM navigation_view_ordered WHERE section_key=? AND profile=? AND locale=? AND position=?', [...group, physicalPosition(index)]);
    if (!member) throw new NavigationUnavailable('navigation ordered projection is incomplete');
    const base = tuple(member);
    const position: WindowPosition = review ? [Number(index >= ordinaryCount), ...base] : base;
    cache.set(index, position);
    indexes.set(compact([member.kind, member.id]), index);
    return position;
  }
  return {
    row,
    seek(boundary, after) {
      let low = 0, high = total;
      while (low < high) {
        const middle = Math.floor((low + high) / 2);
        const comparison = comparePositions(group[2], row(middle), boundary);
        if (comparison < 0 || after && comparison === 0) low = middle + 1;
        else high = middle;
      }
      return low;
    },
    index(position) {
      const index = indexes.get(compact(position.slice(-2)));
      if (index === undefined) throw new Error('navigation continuation was not admitted');
      return index;
    },
  };
}
