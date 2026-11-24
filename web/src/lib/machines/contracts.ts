export const ORIGINAL_MAC_MACHINE_ID = "ee17b608-a449-4966-a808-a791ba7df1c7";
export const MACHINE_ICONS = ["server", "laptop", "desktop", "cloud"] as const;
export const MACHINE_COLORS = ["jade", "orchid", "sand", "cyan", "rose", "plum", "lime", "stone"] as const;
export type MachineIcon = typeof MACHINE_ICONS[number];
export type MachineColor = typeof MACHINE_COLORS[number];
export interface Machine {
  machine_id: string;
  name: string;
  icon: MachineIcon;
  color: MachineColor;
  is_home: boolean;
  available: boolean;
  running_workers: number | null;
  waiting_tickets: number;
}
export interface MachinesResponse { machines: Machine[] }

/* Moving the server to another machine. These mirror `server/server_move/contracts.ts`. */
export type ServerMovePhase = "preparing" | "copying" | "paused" | "committing" | "moved" | "failed";
export const SERVER_MOVE_ACTIVE_PHASES: ReadonlySet<ServerMovePhase> = new Set(["preparing", "copying", "paused", "committing"]);
/** The owner's request: `POST /api/machines/:machine_id/move`. */
export interface ServerMoveRequest {
  readonly server_url: string;
  readonly port: number;
}
/** One run as the owner sees it. `failure` is a plain sentence when phase is failed, otherwise null. */
export interface ServerMoveView {
  readonly move_id: string;
  readonly target_machine_id: string;
  readonly server_url: string;
  readonly port: number;
  readonly phase: ServerMovePhase;
  readonly failure: string | null;
  readonly started_at_ms: number;
  readonly updated_at_ms: number;
}
/** `GET /api/server-move`: the latest run. */
export interface ServerMoveResponse { move: ServerMoveView | null }
