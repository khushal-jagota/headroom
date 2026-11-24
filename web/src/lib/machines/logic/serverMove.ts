import { SERVER_MOVE_ACTIVE_PHASES, type Machine, type ServerMoveView } from "../contracts";

/** Whether a run is under way: the server pauses or switches, so no second move starts. */
export function serverMoveActive(move: ServerMoveView | null | undefined): boolean {
  return Boolean(move && SERVER_MOVE_ACTIVE_PHASES.has(move.phase));
}

/** The server can move only to an attached machine that is not its own and is reachable now. */
export function canMoveServerTo(machine: Machine, move: ServerMoveView | null | undefined): boolean {
  return !machine.is_home && machine.available && !serverMoveActive(move);
}

/** The run's phase in the owner's words. */
export function serverMovePhaseText(move: ServerMoveView): string {
  switch (move.phase) {
    case "preparing": return "Getting the server package";
    case "copying": return "Copying the server's state";
    case "paused": return "Paused: checking the new server";
    case "committing": return "Switching";
    case "moved": return `Moved to ${move.server_url}`;
    case "failed": return `The move failed: ${move.failure ?? "no reason was given."}`;
  }
}
