import type { ConversationTaskProgress } from "./taskProgress";

/** The plan step a running turn is on, for its live line in Focus. Focus shows no tool
 *  activity, so the step the agent itself named is what says the work is moving. The pane
 *  provides it; null where the live line should not carry it (Full has its own work rows,
 *  and the status slot's pill carries the plan there). */
export type LivePlan = { progress: ConversationTaskProgress; moving: boolean } | null;

export const LIVE_PLAN = Symbol("conversation-live-plan");
