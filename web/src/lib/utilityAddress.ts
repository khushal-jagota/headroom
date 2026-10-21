import { parseConfigSelection, type ConfigSelection } from "./shellRouting";
import { parseUsageSelection, type UsageTab } from "./usageAddress";

export type WorkspaceUtility = "usage" | "settings" | "ideas" | "feedback";

export const WORKSPACE_UTILITIES: readonly WorkspaceUtility[] = [
  "usage",
  "settings",
  "ideas",
  "feedback"
];

export function isWorkspaceUtility(value: string): value is WorkspaceUtility {
  return (WORKSPACE_UTILITIES as readonly string[]).includes(value);
}

/** An address for a position inside a screen, built from wherever that screen is mounted.
 *
 * A screen cannot be handed a string prefix to concatenate, because the pane's root can
 * end in `?view=items` and a query has to stay last. It is handed this instead, so the
 * root it lives under decides the whole address and the screen only says where it wants
 * to go.
 */
export type UtilityHref = (segments: string[]) => string;

/** The builder a standalone page uses: its own route, with the position appended. */
export function prefixHref(prefix: string): UtilityHref {
  return segments => (segments.length === 0 ? prefix : `${prefix}/${segments.join("/")}`);
}

/** The builder a screen nested inside another screen uses. */
export function nestedHref(href: UtilityHref, under: string[]): UtilityHref {
  return segments => href([...under, ...segments]);
}

export type UsagePlacement = { tab: UsageTab } & ConfigSelection;
export type SettingsPlacement = { section: string } & ConfigSelection;

export type UtilityPlacement =
  | { utility: "usage"; usage: UsagePlacement }
  | { utility: "settings"; settings: SettingsPlacement }
  | { utility: "ideas" }
  | { utility: "feedback" };

/** The one reading of a utility screen's own path.
 *
 * The standalone page and the Workspace pane root the same path at different places, so
 * both ask here rather than keeping a copy of the rules. Returns null for a path the
 * screen has no position for, which both roots answer with their unknown address.
 */
export function parseUtilityPath(
  utility: WorkspaceUtility,
  path: readonly string[]
): UtilityPlacement | null {
  if (utility === "usage") {
    const usage = parseUsageSelection(path);
    return usage === null ? null : { utility, usage };
  }
  if (utility === "settings") {
    const section = path[0] || "config";
    if (section !== "config") {
      if (!["notifications", "scheduled-tasks"].includes(section) || path.length > 1) return null;
      return { utility, settings: { section, roleKind: "index" } };
    }
    const config = parseConfigSelection(path.slice(1));
    return config === null ? null : { utility, settings: { section, ...config } };
  }
  return path.length === 0 ? { utility } : null;
}
