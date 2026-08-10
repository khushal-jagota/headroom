export type AnyRecord = Record<string, any>;

export type DeploymentStatusState =
  | "idle"
  | "preparing"
  | "restarting"
  | "back_up"
  | "problem"
  | "unknown";

export type DeploymentOutcome = "succeeded" | "failed" | "rolled_back";

export type DeploymentStatus = {
  state: DeploymentStatusState;
  deployed_sha: string | null;
  target_sha: string | null;
  outcome: DeploymentOutcome | null;
  detail: string | null;
  valid_until: string | null;
};

export type VpsMetricState = "healthy" | "warning" | "critical" | "unavailable";

export type VpsPercentageMetric = {
  used_percent: number | null;
  state: VpsMetricState;
  unavailable_reason: string | null;
};

export type VpsBackupMetric = {
  age_seconds: number | null;
  state: VpsMetricState;
  unavailable_reason: string | null;
};

export type VpsDeploymentSummary = {
  outcome: DeploymentOutcome | null;
  detail: string | null;
};

export type VpsStatusSummary = {
  deployed_sha: string | null;
  deployment: VpsDeploymentSummary;
  cpu: VpsPercentageMetric;
  ram: VpsPercentageMetric;
  disk: VpsPercentageMetric;
  backup: VpsBackupMetric;
};

// The served Worker-type manifest response (GET /api/worker-types).
import type { WorkerTypeManifest } from "./lifecycle";
export type { WorkerTypesResponse } from "./lifecycle";

export type SprintSummary = {
  id: string;
  name: string;
};

export type SprintsResponse = {
  sprints: SprintSummary[];
};

export type Priority = "P0" | "P1" | "P2" | "P3";

export type SprintItemKind = "normal" | "other";

export type SprintItemSummary = AnyRecord & {
  id: string;
  title: string;
  project_id: string;
  project: string;
  sprint_id: string | null;
  kind: SprintItemKind;
};

export type SprintItemsResponse = {
  items: SprintItemSummary[];
};

export type ProjectSummary = {
  id: string;
  name: string;
  summary: string;
  priority: Priority | null;
  created_at: number;
  updated_at: number;
};

export type ProjectsResponse = {
  projects: ProjectSummary[];
};

export type ScheduleCadence =
  | "every_planning_day"
  | "current_sprint_day_four"
  | "current_sprint_final_day";

export type SchedulePlacementMode = "current_sprint" | "backlog" | "sprint_item";

export type ScheduledTask = {
  id: string;
  enabled: boolean;
  cadence: ScheduleCadence;
  local_time: string;
  title: string;
  worker_type: string;
  kickoff_note: string;
  priority: Priority;
  deadline: string | null;
  project_id: string | null;
  placement_mode: SchedulePlacementMode;
  sprint_item_id: string | null;
  employee_backend: string | null;
  employee_launch_model: string | null;
  blocked_by_ticket_ids: string[];
  created_at: number;
  updated_at: number;
};

export type SchedulesResponse = {
  schedules: ScheduledTask[];
};

export type TicketField = {
  value?: string | null;
  user_note?: string | null;
  proposal?: {
    body: string;
    proposed_by: string;
  } | null;
};

export type BlockedByTicket = {
  ticket_id: string;
  title: string;
  stage: string;
  href: string;
};

export type BlockerSummary = {
  blocked_by: BlockedByTicket[];
};

export type StageOwnershipMode = "worker" | "user" | "paired";

export type ManagedSkill = {
  name: string;
  description: string;
  markdown_body: string;
};
export type SkillsHomeResponse = { skills: ManagedSkill[] };

export type WorkerManagementSummary = {
  worker_type: string;
  label: string;
  specialist_skill_name: string;
  suggested_next_ceiling: string;
  stage_ownership_defaults: Record<string, StageOwnershipMode>;
  launch_defaults: EmployeeConfigurationSnapshot;
};

export type WorkersResponse = {
  workers: WorkerManagementSummary[];
  chief_of_staff: ChiefManagementSettings;
};

export type ChiefManagementSettings = {
  employee_id: string;
  label: string;
  skill: ManagedSkill;
  launch_defaults: EmployeeConfigurationSnapshot;
  conversation_id: string | null;
  needs_me: boolean;
  agent_working: boolean;
  latest_turn_ended_sequence: number;
};

export type WorkerManagementSettings = {
  worker_type: string;
  suggested_next_ceiling: string;
  stage_ownership_defaults: Record<string, StageOwnershipMode>;
  specialist_skill: ManagedSkill;
  launch_defaults: EmployeeConfigurationSnapshot;
  candidate_specialist_skill?: ManagedSkill;
};

export type WorkerManagementDetail = {
  manifest: WorkerTypeManifest;
  settings: WorkerManagementSettings;
};

export type ResolvedTicketPriorityAnchors = {
  sprint_item: {
    id: string;
    title: string;
    priority: string;
  } | null;
  project: {
    id: string;
    name: string;
    priority: string | null;
  } | null;
};

export type TicketDetail = {
  id: string;
  title: string;
  worker_type: string;
  employee_backend: string;
  employee_launch_model: string | null;
  employee_launch_reasoning_effort: string | null;
  employee_configuration_editable: boolean;
  stage: string;
  ceiling: string;
  at_cap: string;
  suggested_next_ceiling: string;
  priority: string;
  deadline?: string | null;
  project_id?: string | null;
  project?: string | null;
  effective_sprint_id?: string | null;
  sprint_item_id?: string | null;
  resolved_priority_anchors: ResolvedTicketPriorityAnchors;
  ticket_status?: string;
  backend_error: string | null;
  stage_ownership_overrides: Record<string, StageOwnershipMode>;
  default_stage_ownership_mode: StageOwnershipMode | null;
  effective_stage_ownership_mode: StageOwnershipMode | null;
  conversation_id: string | null;
  day_ids?: string[];
  blocked?: boolean;
  blocker_summary?: BlockerSummary;
  recap?: string | null;
  verdict: TicketVerdict | null;
  fields: Record<string, TicketField>;
};

export type TicketVerdict = {
  rating: number | null;
  text: string | null;
};

export type EmployeeConfigurationSnapshot = {
  employee_backend: string;
  employee_launch_model: string | null;
  employee_launch_reasoning_effort: string | null;
};

export type TicketDeletionResponse = {
  ok: true;
  ticket_id: string;
  title: string;
  day_ids: string[];
  sprint_item_ids: string[];
  sprint_ids: string[];
  linked_entity_ids: string[];
};

export type CurrentSprint = AnyRecord & {
  date_start: string;
  date_end: string;
};

export type CurrentSprintResponse = {
  planning_date: string;
  sprint: CurrentSprint | null;
  groups: Record<string, AnyRecord[]>;
};

export type ReviewProposalItem = {
  review_item_type: "proposal";
  ticket_id: string;
  field: string;
  title: string;
  waiting_since: number;
};

export type ReviewNeedsUserItem = {
  review_item_type: "needs_user";
  ticket_id: string;
  title: string;
  waiting_since: number;
};

export type ReviewItem = ReviewProposalItem | ReviewNeedsUserItem;

export type ReviewResponse = {
  items: ReviewItem[];
  running_worker_count: number;
};

export type BoardResponse = {
  columns: Array<{
    stage: string;
    cards: AnyRecord[];
  }>;
};

export type DayTicket = AnyRecord & {
  id: string;
  title: string;
  stage: string;
  ticket_status: string;
  conversation_id: string | null;
  is_done?: boolean;
  waiting_to_closeout?: boolean;
  gating_field?: string | null;
  agent_working?: boolean;
  needs_me?: boolean;
  latest_turn_ended_sequence?: number;
};

export type DayResponse = {
  id: string;
  focus?: string | null;
  brief_take?: string | null;
  watchout?: string | null;
  if_today_lands?: string | null;
  midday_reconciliation?: string | null;
  tickets: DayTicket[];
};

export type BacklogResponse = SprintItemsResponse;

export type IdeasResponse = {
  ideas: AnyRecord[];
};

export type NotificationTypeSetting = {
  id: string;
  label: string;
  description: string;
  enabled: boolean;
};

export type NotificationSubjectSettings = {
  key: string;
  label: string;
  types: NotificationTypeSetting[];
};

export type NotificationSettingsResponse = {
  subjects: NotificationSubjectSettings[];
  vapid_public_key: string;
};
