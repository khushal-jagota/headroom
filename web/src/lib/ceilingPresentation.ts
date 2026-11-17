import { ceilingOptionsFor, stageLabelFor, type Lifecycle } from "./lifecycle";

/** Saved permission remains legible even when it is below the offered choice floor. */
export function ceilingLabelFor(lifecycle: Lifecycle | null, ceiling: string | null): string {
  if (ceiling === null) return "Choose ceiling";
  if (ceiling === "none") return "No further";
  return stageLabelFor(lifecycle,ceiling);
}

export type CeilingChoice = { value: string; label: string; passed: boolean };

/**
 * Stages the approval may still reach, plus earlier stages shown as already done.
 * The floor is the stage being approved. Anything before it has gone by.
 */
export function approvalCeilingChoices(lifecycle: Lifecycle | null, pendingStage: string): CeilingChoice[] {
  if (!lifecycle) return [];
  const floor = lifecycle.stageOrder.indexOf(pendingStage);
  const passed = floor <= 0 ? [] : lifecycle.stageOrder.slice(0, floor)
    .filter((stage) => lifecycle.ceilingRange.includes(stage))
    .map((stage) => ({ value: stage, label: stageLabelFor(lifecycle, stage), passed: true as const }));
  const open = ceilingOptionsFor(lifecycle, pendingStage)
    .map((option) => ({ value: option.value, label: option.label, passed: false }));
  return [...passed, ...open];
}

/** The quiet line on the left of the picker: where this choice stops. */
export function ceilingPartCopy(stageName: string | null): { label: string; detail: string | null } {
  if (stageName === null || stageName === "Choose ceiling") return { label: "Until", detail: "Choose" };
  if (stageName === "No further") return { label: "Until", detail: "No further" };
  return { label: "Until", detail: stageName };
}
