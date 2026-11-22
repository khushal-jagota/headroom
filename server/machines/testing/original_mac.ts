/** Test-only: the original Mac rows that older homes hold and a fresh install never creates. */
import type { TransactionSql } from '../../core/database/contracts.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../../execution_identity.ts';

export function seed_original_mac_rows(tx: TransactionSql): void {
  tx.run(
    "INSERT INTO machines(machine_id,name,icon,color,is_home) VALUES(?, 'Mac','laptop','orchid',0)",
    [ORIGINAL_MAC_MACHINE_ID],
  );
  tx.run('INSERT INTO machine_executor_credentials(machine_id) VALUES(?)', [
    ORIGINAL_MAC_MACHINE_ID,
  ]);
}
