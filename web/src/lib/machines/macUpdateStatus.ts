import type { MacStatus } from "../ticketMachines";

type Update = NonNullable<MacStatus["update"]>;

const labels: Record<Update["phase"], string> = {
  waiting: "waits to update",
  downloading: "downloads the update",
  ready_to_restart: "is ready to update",
  current: "is current",
  failed: "update failed",
  reverted: "returned to its previous version",
  unavailable: "needs a manual update",
};

export const macUpdateLabel = (update: Update, name = "Mac"): string => `${name} ${labels[update.phase]}`;

export function macUpdateNeedsAttention(update: MacStatus["update"]): boolean {
  return Boolean(update && ["failed", "reverted", "unavailable"].includes(update.phase));
}
