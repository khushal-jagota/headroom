/** Stable execution destinations and their public display identity. */
export const MACHINE_ICONS = ['server', 'laptop', 'desktop', 'cloud'] as const;
export const MACHINE_COLORS = [
  'jade',
  'orchid',
  'sand',
  'cyan',
  'rose',
  'plum',
  'lime',
  'stone',
] as const;
export type MachineIcon = (typeof MACHINE_ICONS)[number];
export type MachineColor = (typeof MACHINE_COLORS)[number];
export interface Machine {
  machine_id: string;
  name: string;
  icon: MachineIcon;
  color: MachineColor;
  is_home: boolean;
}
export interface MachineView extends Machine {
  available: boolean;
  running_workers: number | null;
  waiting_tickets: number;
}
export interface MachineCredentialGrant {
  machine_id: string;
  credential: string;
  issued_at_ms: number;
}
