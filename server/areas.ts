import type { Area } from "./core/authority/register.ts";
/** Add one area registration here. Core has no area identity branches. */
export const areas: readonly Area[] = [];
import type { CommitStep } from "./core/database/index.ts";
/** Global order: signal publication, artifact links, navigation. */
export const commitSteps: readonly CommitStep[] = [];
