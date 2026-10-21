import { parseConfigSelection, type ConfigSelection } from "./shellRouting";

export type UsageTab = "tokens" | "cost" | "limits" | "config";
export type UsageSelection = { tab: UsageTab } & ConfigSelection;

/** Parse the suffix after #/backends, including a reloadable Config detail. */
export function parseUsageSelection(segments: readonly string[]): UsageSelection | null {
  const tab = segments[0] || "limits";
  if (tab === "config") {
    const config = parseConfigSelection(segments.slice(1));
    return config === null ? null : { tab, ...config };
  }
  if (["tokens", "cost", "limits"].includes(tab) && segments.length <= 1) {
    return { tab: tab as UsageTab, roleKind: "index" };
  }
  return null;
}
