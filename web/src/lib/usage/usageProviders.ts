/** Adapted from T3 mobile usageProviders.ts. See T3-LICENSE. */
import type { UsageProviderKind } from "@t3tools/contracts";

export const PROVIDER_ORDER: readonly UsageProviderKind[] = ["codex", "claude", "grok"];
export const PROVIDER_LABEL: Record<UsageProviderKind, string> = {
  claude: "Claude Code",
  codex: "Codex",
  grok: "Grok Build",
};

/** React appearance context becomes the Panels document theme. */
export function providerColors(scheme: "dark" | "light"): Record<UsageProviderKind, string> {
  return {
    claude: "#d97757",
    codex: scheme === "dark" ? "#e6e6e6" : "#3c3c43",
    grok: scheme === "dark" ? "#a1a1aa" : "#52525b",
  };
}

// Panels tokens.css currently defines one dark appearance.
export const PROVIDER_COLOR = providerColors("dark");

// NativeWind red-500/amber-500 from UsageLimitsSection.tsx:70–74.
export const ALLOWANCE_COLOR = { low: "#ef4444", warning: "#f59e0b" };
