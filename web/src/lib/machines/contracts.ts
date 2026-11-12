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
