import type { ManagedSkill } from '../skills/contracts.ts';
import type { WorkerTypeManifest } from '../worker_types/contracts.ts';
export interface ManagedWorkerLaunchDefaults {
  employee_backend: string;
  employee_launch_model: string;
  employee_launch_reasoning_effort: string | null;
}
export interface ManagedWorkerSettings {
  worker_type: string;
  specialist_skill: ManagedSkill;
  launch_defaults: ManagedWorkerLaunchDefaults;
  candidate_specialist_skill?: ManagedSkill;
}
export interface ManagedChiefSettings {
  employee_id: string;
  label: string;
  recap: string;
  skill: ManagedSkill;
  launch_defaults: ManagedWorkerLaunchDefaults;
}
export interface WorkerManagementSummary {
  worker_type: string;
  label: string;
  specialist_skill_name: string;
  launch_defaults: ManagedWorkerLaunchDefaults;
}
export interface WorkerManagementDetail {
  manifest: WorkerTypeManifest;
  settings: ManagedWorkerSettings;
}
