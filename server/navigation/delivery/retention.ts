import type { TransactionSql } from '../../core/database/contracts.ts';
import { DELIVERY } from './configuration.ts';
/** Prune complete prefixes and preserve the last excluded revision in the same transaction. */
export function prune(tx: TransactionSql): number {
  const current = tx.get<{ epoch: string; revision: number }>(
    'SELECT epoch,revision FROM navigation_meta WHERE singleton=1',
  );
  if (!current) throw new Error('navigation metadata is absent');
  const stored = tx.get<{ epoch: string; floor_revision: number }>(
    'SELECT epoch,floor_revision FROM navigation_delivery_retention WHERE singleton=1',
  );
  let floor = stored?.epoch === current.epoch ? Number(stored.floor_revision) : 0;
  const sizes = tx.get<{ count: number; size: number }>(
    'SELECT COUNT(*) AS count,COALESCE(SUM(length(CAST(body AS BLOB))),0) AS size FROM navigation_journal',
  )!;
  let { count, size } = sizes;
  let removed = 0;
  while (count > DELIVERY.retainedBatchCount || size > DELIVERY.retainedBytes) {
    const entries = tx.all<{ revision: number; bytes: number }>(
      'SELECT revision,length(CAST(body AS BLOB)) AS bytes FROM navigation_journal WHERE revision>? ORDER BY revision LIMIT 256',
      [floor],
    );
    if (!entries.length) break;
    for (const entry of entries) {
      floor = entry.revision;
      count -= 1;
      size -= entry.bytes;
      removed += 1;
      if (count <= DELIVERY.retainedBatchCount && size <= DELIVERY.retainedBytes) break;
    }
    tx.run('DELETE FROM navigation_journal WHERE revision<=?', [floor]);
  }
  if (!count) floor = Math.max(floor, current.revision);
  tx.run(
    'INSERT INTO navigation_delivery_retention(singleton,epoch,floor_revision) VALUES(1,?,?) ON CONFLICT(singleton) DO UPDATE SET epoch=excluded.epoch,floor_revision=excluded.floor_revision',
    [current.epoch, floor],
  );
  return removed;
}
