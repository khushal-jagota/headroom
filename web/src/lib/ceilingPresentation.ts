import { stageLabelFor, type Lifecycle } from "./lifecycle";

/** Saved permission remains legible even when it is below the offered choice floor. */
export function ceilingLabelFor(lifecycle: Lifecycle | null, ceiling: string | null): string {
  if (ceiling === null) return "Choose ceiling";
  if (ceiling === "none") return "No further";
  return stageLabelFor(lifecycle,ceiling);
}
