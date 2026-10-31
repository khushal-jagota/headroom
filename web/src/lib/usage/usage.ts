/** Query transport adaptation of T3 packages/client-runtime/src/state/usage.ts:12–64. */
import { refreshUsageRates } from "../conversation/recordedUsageWire";

/** Refresh rates, then invalidate/rescan even when the rate request fails. */
export async function refreshUsage(refreshSummary: () => Promise<unknown>): Promise<void> {
  // T3 runAtomCommand returns Failure instead of throwing. Its refresh still
  // invalidates the summary query when rates cannot load.
  await refreshUsageRates().catch(() => undefined);
  await refreshSummary();
}
