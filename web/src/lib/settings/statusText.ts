import type { Machine } from "../machines/contracts";
import type { BackendSnapshot } from "../conversation/wire";

/** A machine row's status, which search lists as the row's value too. */
export function machineStatus(machine: Machine): { away: boolean; rest: string; text: string } {
  const running = machine.running_workers === null ? "Running unknown" : `${machine.running_workers} ${machine.running_workers === 1 ? "Worker" : "Workers"} running`;
  const waiting = machine.available ? "" : ` · ${machine.waiting_tickets} ${machine.waiting_tickets === 1 ? "Ticket waits" : "Tickets waiting"}`;
  const rest = `${running}${waiting}`;
  return { away: !machine.available, rest, text: `${machine.available ? "Available" : "Away"} · ${rest}` };
}

/** A backend row's description: "<installed> · <latest> available", "· Up to date",
 *  "· Checking…", and what an installation Panels cannot update needs. */
export function backendStatus(
  snapshot: BackendSnapshot | undefined,
  check: { refreshing: boolean; refreshError: string | null }
): string {
  const parts = [snapshot?.version ?? (snapshot?.installed === false ? "Not installed" : "Unknown")];
  if (check.refreshing) parts.push("Checking…");
  else if (snapshot?.installed) {
    const advisory = snapshot.update_advisory;
    const latest = check.refreshError ? null : advisory?.latest_version ?? null;
    let status = latest === null ? "Latest version unavailable." : advisory?.update_available ? `${latest} available` : "Up to date";
    if (!advisory?.update_command) {
      status = `${status.endsWith(".") ? status : `${status}.`} ${advisory?.detail ?? "This installation needs a manual update."}`;
    }
    parts.push(status);
  }
  if (snapshot?.identity?.status === "unauthenticated") parts.push("Signed out");
  return parts.join(" · ");
}
