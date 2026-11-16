/** Export-only proof: real second writer commits at every public backup progress yield. */
import BetterSqlite3 from 'better-sqlite3';
import { createDatabase, createReadAccess, onlineBackup } from '../index.ts';

export async function backup_with_interleaved_commits(
  source: string,
  destination: string,
): Promise<unknown> {
  const writer = createDatabase(source);
  const original = BetterSqlite3.prototype.backup;
  let commits = 0;
  // The public JS API permits undefined to keep its current rate, although its
  // declaration narrows progress to number. Preserve that actual callback contract.
  type Backup = (
    this: BetterSqlite3.Database,
    path: string,
    options?: { progress?: (info: BetterSqlite3.BackupMetadata) => number | undefined },
  ) => Promise<BetterSqlite3.BackupMetadata>;
  const backup = original as Backup;
  BetterSqlite3.prototype.backup = function (path, options) {
    const progress = options?.progress;
    return backup.call(this, path, {
      progress(info) {
        const rate = progress?.(info);
        writer.write((tx) => {
          tx.run('UPDATE snapshot_counter SET value=value+1');
          tx.run(
            'INSERT INTO snapshot_audit(sequence,value) SELECT value,value FROM snapshot_counter',
          );
        });
        if (++commits >= 5) throw new Error('backup did not finish after five interleaved commits');
        return rate;
      },
    });
  };
  try {
    await onlineBackup(source, destination);
    const reader = createReadAccess(destination);
    try {
      return reader.read((sql) => ({
        commits,
        integrity: sql.get('SELECT integrity_check FROM pragma_integrity_check'),
        counter: sql.get<{ value: number }>('SELECT value FROM snapshot_counter')!.value,
        audit: sql.get(
          'SELECT count(*) AS count,max(sequence) AS maximum,sum(sequence != value) AS inconsistent FROM snapshot_audit',
        ),
      }));
    } finally {
      reader.close();
    }
  } finally {
    BetterSqlite3.prototype.backup = original;
    writer.close();
  }
}
