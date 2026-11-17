/** Adapted from T3 mobile usageProviders.ts. See T3-LICENSE.
 *
 * Usage sources are named and coloured by the server's drivers. Grok is a usage source,
 * not a driver, so it keeps its own name and colour here. Panels tokens.css defines one
 * dark appearance, so these are the dark colours.
 */
import type { UsageProviderKind } from "@t3tools/contracts";
import type { DriverPresentation } from "../drivers/contracts";
import { driverFor } from "../drivers/presentation";

export const NON_DRIVER_USAGE_SOURCES: Readonly<Record<string, { label: string; color: string }>> = {
  grok: { label: "Grok Build", color: "#a1a1aa" },
};

/** A source nobody names shows itself in the neutral colour. */
const UNKNOWN_USAGE_SOURCE_COLOR = "#a1a1aa";

/** Stacking order, bottom first: the drivers in the server's order, then the other sources. */
export function usageSourceOrder(drivers: readonly DriverPresentation[] | undefined): readonly UsageProviderKind[] {
  return [...(drivers ?? []).map((driver) => driver.driver_kind), ...Object.keys(NON_DRIVER_USAGE_SOURCES)];
}

export function usageSourceLabel(key: UsageProviderKind, drivers: readonly DriverPresentation[] | undefined): string {
  return driverFor(key, drivers)?.label ?? NON_DRIVER_USAGE_SOURCES[key]?.label ?? key;
}

export function usageSourceColor(key: UsageProviderKind, drivers: readonly DriverPresentation[] | undefined): string {
  return driverFor(key, drivers)?.accent_color ?? NON_DRIVER_USAGE_SOURCES[key]?.color ?? UNKNOWN_USAGE_SOURCE_COLOR;
}

// NativeWind red-500/amber-500 from UsageLimitsSection.tsx:70–74.
export const ALLOWANCE_COLOR = { low: "#ef4444", warning: "#f59e0b" };
