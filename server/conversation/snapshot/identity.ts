/** The identity answer every backend reader builds. */
import type { BackendIdentity } from './contracts.ts';
export function identity(
  status: BackendIdentity['status'],
  account_label: string | null = null,
  detail: string | null = null,
): BackendIdentity {
  return { status, account_label, detail, login_command: null };
}
