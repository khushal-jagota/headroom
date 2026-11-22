import { parseUsageSelection, type UsageSelection } from "./usageAddress";

/** The screens that can also open inside the Workspace pane. Settings has its own home. */
export type WorkspaceUtility = "usage";

export const WORKSPACE_UTILITIES: readonly WorkspaceUtility[] = ["usage"];

export function isWorkspaceUtility(value: string): value is WorkspaceUtility {
  return (WORKSPACE_UTILITIES as readonly string[]).includes(value);
}

/** An address for a position inside a screen, built from wherever that screen is mounted.
 *
 * A screen cannot be handed a string prefix to concatenate, because the pane's root can
 * end in `?view=managers` and a query has to stay last. It is handed this instead, so the
 * root it lives under decides the whole address and the screen only says where it wants
 * to go.
 */
export type UtilityHref = (segments: string[]) => string;

/** The builder a standalone page uses: its own route, with the position appended. */
export function prefixHref(prefix: string): UtilityHref {
  return segments => (segments.length === 0 ? prefix : `${prefix}/${segments.join("/")}`);
}

export type UtilityPlacement = { utility: "usage"; usage: UsageSelection };

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
  const usage = parseUsageSelection(path);
  return usage === null ? null : { utility, usage };
}
