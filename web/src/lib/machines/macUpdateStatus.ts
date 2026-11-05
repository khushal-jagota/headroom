import type { MacStatus } from "../ticketMachines";

type Update = NonNullable<MacStatus["update"]>;

const labels: Record<Update["phase"], string> = {
  waiting: "Mac waits to update",
  downloading: "Mac downloads the update",
  ready_to_restart: "Mac is ready to update",
  current: "Mac is current",
  failed: "Mac update failed",
  reverted: "Mac returned to its previous version",
  unavailable: "Mac needs a manual update",
};

export const macUpdateLabel = (update: Update): string => labels[update.phase];

export function macUpdateNeedsNotice(update: MacStatus["update"]): boolean {
  return Boolean(update && update.phase !== "current");
}

export function macUpdateNeedsAttention(update: MacStatus["update"]): boolean {
  return Boolean(update && ["failed", "reverted", "unavailable"].includes(update.phase));
}
