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
  date_start: string;
  date_end: string;
};

export type SprintsResponse = {
  sprints: SprintSummary[];
};

export type Priority = "P0" | "P1" | "P2" | "P3";

export type AgentState = "working" | "idle" | "errored";

// `awaiting_approval` is a proposal the owner holds; `awaiting_agent_approval` is one
// parked on a supervisor or another Ticket. The server splits them, so the same word
// means the same thing on every screen and no screen has to guess from the status.
export type WorkAttention = {
  awaiting_reply: boolean;
  awaiting_answer: boolean;
  awaiting_approval: boolean;
  awaiting_agent_approval: boolean;
  assigned: boolean;
  agent_state: AgentState;
};

export type ListPageFacts = {
  match_count: number;
  return_count: number;
  limit: number;
  offset: number;
  omitted_before: number;
  omitted_after: number;
  complete: boolean;
  next_offset: number | null;
};

export type TicketSummary = WorkAttention & {
  id: string;
  title: string;
  worker_type: string;
  stage: string;
  ticket_status: string;
  priority: Priority;
  project_id: string | null;
  project: string | null;
  sprint_item_id: string | null;
  sprint_item: string | null;
  sprint_id: string | null;
  effective_sprint_id: string | null;
  recap_preview: string;
};

export type TicketSummariesResponse = {
  tickets: TicketSummary[];
  page: ListPageFacts;
};

export type OutcomeSummary = WorkAttention & {
  id: string;
  title: string;
  priority: Priority;
  deadline: string | null;
  project_id: string;
  project: string;
  created_at: number;
  updated_at: number;
};

export type SprintItemListSummary = OutcomeSummary;

export type SprintItemSummariesResponse = {
  items: SprintItemListSummary[];
  page: ListPageFacts;
};

export type SprintItemKind = "normal";

export type SprintItemSummary = OutcomeSummary & {
  kind: SprintItemKind;
  committed_sprints: SprintSummary[];
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

export type SchedulePlacementMode = "current_sprint" | "backlog";

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
  sprint_id: string | null;
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

export type TicketFieldValues = Record<string, string>;

export type PendingTicketProposal = {
  field: string;
  body: string;
  proposed_by: string;
  created_at: number;
};


export type Principal = {
  kind: "owner" | "chief" | "sprint_item" | "ticket";
  id: string;
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

export type StageOwnershipMode = "worker" | "user";

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
  owner_read_through_sequence: number;
};

export type WorkerManagementSettings = {
  worker_type: string;
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

export type TicketConversationHistoryEntry = {
  conversation_id: string;
  created_at: number;
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
  ceiling_holder: Principal;
  priority: string;
  deadline?: string | null;
  project_id?: string | null;
  project?: string | null;
  sprint_id?: string | null;
  effective_sprint_id?: string | null;
  sprint_item_id?: string | null;
  resolved_priority_anchors: ResolvedTicketPriorityAnchors;
  ticket_status?: string;
  conversation_id: string | null;
  conversation_history: TicketConversationHistoryEntry[];
  day_ids?: string[];
  blocked?: boolean;
  blocker_summary?: BlockerSummary;
  recap?: string | null;
  guidance: string;
  field_values: TicketFieldValues;
  pending_proposal: PendingTicketProposal | null;
  awaiting_reply?: boolean;
  awaiting_answer?: boolean;
  awaiting_approval?: boolean;
  awaiting_agent_approval?: boolean;
  assigned?: boolean;
  agent_state?: AgentState;
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
  linked_ticket_ids: string[];
};

export type SprintWireBody = {
  id: string;
  name: string;
  date_start: string;
  date_end: string;
  primary_bet: string;
  kickoff: string;
  checkpoint: string;
  review: string;
  created_at: number;
  updated_at: number;
};

export type CurrentSprint = SprintWireBody;

export type SprintTicketSummary = WorkAttention & {
  id: string;
  title: string;
  stage: string;
  priority: Priority;
  ticket_status: string;
  project_id: string | null;
  sprint_item_id: string | null;
  waiting_to_closeout: boolean;
};

export type SprintOutcomeGroup = {
  outcome: OutcomeSummary;
  committed: boolean;
  tickets: SprintTicketSummary[];
};

export type SprintTrackingBody = {
  planning_date: string;
  sprint: SprintWireBody | null;
  outcome_groups: SprintOutcomeGroup[];
  unclassified_tickets: SprintTicketSummary[];
};

export type CurrentSprintResponse = SprintTrackingBody;

export type CarryOutcomeBody = {
  target_sprint_id: string;
  ticket_ids: string[];
};

export type CarryOutcomeResult = {
  source_sprint_id: string;
  target_sprint_id: string;
  outcome_id: string;
  ticket_ids: string[];
};

export type SprintItemWorkspaceTicket = WorkAttention & {
  id: string;
  title: string;
  stage: string;
  priority: Priority;
  activity_at: number;
  ticket_status: string;
  waiting_to_closeout: boolean;
  has_pending_proposal: boolean;
  gating_field: string | null;
  blocked: boolean;
  worker_type: string;
  day_ids: string[];
  sprint_id: string | null;
  sprint_name: string | null;
};

export type SprintItemWorkspace = SprintItemSummary & {
  body: string;
  priority: Priority;
  deadline: string | null;
  supervisor: {
    agent_key: string;
    conversation_id: string | null;
    launch_configuration: EmployeeConfigurationSnapshot;
  };
  planning_day_id: string;
  today_ticket_ids: string[];
  tickets: SprintItemWorkspaceTicket[];
  ticket_rollup: WorkAttention;
  artifacts: SprintItemArtifact[];
  conversation_history: TicketConversationHistoryEntry[];
};

// A folder of files is one artifact. `opens` is the file a reader gets when they pick it:
// the file itself, or the index of a folder that has one. A folder with no index opens
// nothing of its own and carries what is directly inside it instead.
export type SprintItemArtifact = {
  name: string;
  opens: string | null;
  modified_at: number;
  children: SprintItemArtifact[];
};

export type ReviewProposalItem = {
  review_item_type: "proposal";
  ticket_id: string;
  field: string;
  title: string;
  waiting_since: number;
};

export type ReviewItem = ReviewProposalItem;

export type ReviewResponse = {
  items: ReviewItem[];
  running_worker_count: number;
};

export type BoardResponse = {
  columns: Array<{
    stage: string;
    cards: BoardCard[];
  }>;
  sprint_items: BoardSprintItem[];
};

// A Sprint Item's own identity and its supervisor's conversation. No card answers for
// the Item's own worker, so the mark on an Item title comes from here.
export type BoardSprintItem = {
  id: string;
  created_at: number;
  conversation_id: string | null;
  awaiting_reply: boolean;
  awaiting_answer: boolean;
  awaiting_approval: boolean;
  awaiting_agent_approval: boolean;
  assigned: boolean;
  agent_state: AgentState;
  ticket_rollup: WorkAttention;
};

export type BoardCard = WorkAttention & {
  id: string;
  title: string;
  priority: Priority;
  deadline: string | null;
  project_id: string | null;
  project: string | null;
  group_project_id: string | null;
  group_project: string | null;
  activity_at: number;
  has_pending_proposal: boolean;
  ticket_status: string;
  worker_type: string;
  employee_backend: string;
  stage: string;
  stage_label: string;
  gating_field: string | null;
  gating_field_label: string | null;
  is_done: boolean;
  blocked: boolean;
  conversation_id: string | null;
  waiting_to_closeout: boolean;
  sprint_item_id: string | null;
  sprint_item_title: string | null;
  sprint_item_priority: Priority | null;
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
  awaiting_reply?: boolean;
  awaiting_answer?: boolean;
  awaiting_approval?: boolean;
  awaiting_agent_approval?: boolean;
  assigned?: boolean;
  agent_state?: AgentState;
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
