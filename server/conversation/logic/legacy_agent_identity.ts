import type { LocalChildCustodyRow } from '../local_child_custody_contracts.ts';
import type { LocalHostBootIdentity } from '../local_child_contracts.ts';

/** Same host/boot/incarnation admission is used before startup signals a direct child. */
export function legacy_direct_identity_matches(
  row: Pick<LocalChildCustodyRow, 'pid' | 'birth' | 'boot' | 'host'>,
  current: LocalHostBootIdentity,
  process: { pid: number; birth: string; zombie: boolean } | undefined,
): boolean {
  return (
    row.host === current.host &&
    row.boot === current.boot &&
    process !== undefined &&
    !process.zombie &&
    process.pid === row.pid &&
    process.birth === row.birth
  );
}
