/** Admission leases end at durable recipient custody, before native work begins. */
import { AsyncLocalStorage } from 'node:async_hooks';
const leases = new AsyncLocalStorage<readonly (() => void)[]>();
export function with_admission_lease<T>(
  release: () => void,
  operation: () => Promise<T>,
): Promise<T> {
  let held = true;
  const once = () => {
    if (!held) return;
    held = false;
    release();
  };
  return leases.run([...(leases.getStore() ?? []), once], operation).finally(once);
}
export function release_admission_leases(): void {
  for (const release of [...(leases.getStore() ?? [])].reverse()) release();
}
