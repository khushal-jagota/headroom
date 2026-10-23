import type { ConversationSignal } from "./conversationSignal";
export type AnyRecord = Record<string, any>;

export type DeploymentStatusState =
  | "idle"
  | "preparing"
  | "restarting"
  | "back_up"
  | "problem"
  | "unknown";

export type DeploymentOutcome = "succeeded" | "failed" | "rolled_back";

export type SuccessPresentation = {
  state: "connected" | "reconnecting" | "back_up";
  wakeAt: number | null;
};

export type DeploymentStatus = {
  state: DeploymentStatusState;
  deployed_sha: string | null;
  target_sha: string | null;
  outcome: DeploymentOutcome | null;
  detail: string | null;
  valid_until: string | null;
  success_display_until: string | null;
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

export type AgentState = "working" | "idle" | "errored";

// `awaiting_approval` is a proposal the owner holds; `awaiting_agent_approval` is one
// parked on a manager or another Ticket. The server splits them, so the same word
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
  is_backlogged: boolean;
  waiting_to_closeout: boolean;
  manager?: Principal;
  id: string;
  title: string;
  worker_type: string;
  stage: string;
  ticket_status: string;
  project_id: string | null;
  project: string | null;
  manager_id: string | null;
  manager_title: string | null;
  recap_preview: string;
};

export type TicketSummariesResponse = {
  tickets: TicketSummary[];
  page: ListPageFacts;
};

export type ManagerSummary = WorkAttention & {
  settled?: boolean;
  id: string;
  title: string;
  project_id: string;
  project: string;
  created_at: number;
  updated_at: number;
};

export type ManagerListSummary = ManagerSummary;

export type ManagerSummariesResponse = {
  managers: ManagerListSummary[];
  page: ListPageFacts;
};


export type ManagersResponse = {
  managers: ManagerSummary[];
};

export type ProjectSummary = {
  id: string;
  name: string;
  summary: string;
  created_at: number;
  updated_at: number;
};

export type ProjectsResponse = {
  projects: ProjectSummary[];
};

export type ScheduleCadence =
  | "daily"
  | "every_planning_day"
  | "current_sprint_day_four"
  | "current_sprint_final_day";

export type ScheduledTask = {
  manager_kind?: "chief" | "manager";
  manager?: Principal;
  id: string;
  enabled: boolean;
  cadence: ScheduleCadence;
  local_time: string;
  title: string;
  worker_type: string;
  kickoff_note: string;
  deadline: string | null;
  project_id: string | null;
  manager_id: string | null;
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
  revision: number;
};

export type Principal = {
  kind: "owner" | "chief" | "manager" | "ticket";
  id: string;
};

export type ManagerPrincipal = Principal & { kind: "chief" | "manager" };
export type ManagerChildFilter = "active" | "backlogged" | "done";

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

// The stored Worker type record, exactly as GET /api/worker-types/{type} returns it
// and POST /api/worker-types takes it back. The write door replaces the whole record
// and refuses unknown keys, so an edit reads this, changes one value, and posts it
// back unchanged otherwise.
export type WorkerTypeRecordStage = {
  id: string;
  label: string;
  gating_field: string | null;
  is_terminal: boolean;
  ownership_mode: StageOwnershipMode | null;
};

export type WorkerTypeRecord = {
  worker_type: string;
  label: string;
  stages: WorkerTypeRecordStage[];
  fields: Array<{ id: string; label: string }>;
  profile: {
    specialist_skill: string;
    default_model: string;
    default_reasoning_effort: string | null;
    toolset_profile: string;
    default_backend: string;
  };
};

export type ManagedSkill = {
  name: string;
  description: string;
  markdown_body: string;
};
export type ManagedSkillPatch = Partial<Pick<ManagedSkill, "description" | "markdown_body">>;
export type ManagedSkillField = keyof ManagedSkillPatch;
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
  conversation_signal?: ConversationSignal;
  employee_id: string;
  label: string;
  recap: string;
  skill: ManagedSkill;
  launch_defaults: EmployeeConfigurationSnapshot;
  conversation_id: string | null;
  needs_me: boolean;
  awaiting_reply: boolean;
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

export type TicketConversationHistoryEntry = {
  conversation_id: string;
  created_at: number;
};

/** Backend TicketFieldApproval projection. display_label uses the readable actor title. */
export type TicketFieldApproval = {
  method: "explicit_accept" | "auto_accept" | "unknown";
  principal: Principal | null;
  display_label: string;
};

export type TicketDetail = {
  conversation_signal?: ConversationSignal;
  is_backlogged?: boolean;
  record_sha256?: string;
  artifacts?: ManagerArtifact[];
  manager?: Principal;
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
  deadline?: string | null;
  project_id?: string | null;
  project?: string | null;
  manager_id?: string | null;
  manager_title: string | null;
  ticket_status?: string;
  conversation_id: string | null;
  conversation_history?: TicketConversationHistoryEntry[];
  blocked?: boolean;
  blocker_summary?: BlockerSummary;
  recap?: string | null;
  field_values: TicketFieldValues;
  field_approvals?: Record<string, TicketFieldApproval>;
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
  manager_ids: string[];
  linked_ticket_ids: string[];
};

export type ManagerWorkspaceTicket = WorkAttention & {
  conversation_signal?: ConversationSignal;
  is_backlogged: boolean;
  conversation_id?: string | null;
  id: string;
  title: string;
  recap: string;
  recap_preview: string;
  stage: string;
  activity_at: number;
  ticket_status: string;
  waiting_to_closeout: boolean;
  has_pending_proposal: boolean;
  gating_field: string | null;
  blocked: boolean;
  worker_type: string;
};

export type ManagerWorkspace = ManagerSummary & {
  conversation_signal?: ConversationSignal;
  goal: string;
  recap: string;
  manager: {
    agent_key: string;
    conversation_id: string | null;
    launch_configuration: EmployeeConfigurationSnapshot;
  };
  tickets: ManagerWorkspaceTicket[];
  ticket_rollup: WorkAttention;
  artifacts: ManagerArtifact[];
  conversation_history: TicketConversationHistoryEntry[];
};

// A folder of files is one artifact. `opens` is the file a reader gets when they pick it:
// the file itself, or the index of a folder that has one. A folder with no index opens
// nothing of its own and carries what is directly inside it instead.
export type ManagerArtifact = {
  name: string;
  opens: string | null;
  modified_at: number;
  children: ManagerArtifact[];
};

export type ReviewProposalItem = {
  review_item_type: "proposal";
  ticket_id: string;
  field: string;
  title: string;
  waiting_since: number;
};

export type NavigationReviewProposalItem = ReviewProposalItem & {
  proposal_field: string;
  proposal_revision: number;
};

export type ReviewMessageItem = {
  review_item_type: "message";
  conversation_id: string;
  address_id: string;
  owner_kind: "ticket" | "manager" | "chief";
  owner_id: string;
  title: string;
  waiting_since: number;
  recap: string;
  recap_reference: import("./navigation/contracts").ViewContentReference | null;
};

export type NavigationReviewItem = NavigationReviewProposalItem | ReviewMessageItem;
export type ReviewItem = ReviewProposalItem | ReviewMessageItem;

export type ReviewResponse = {
  items: ReviewItem[];
  running_worker_count: number;
};

export type BoardResponse = {
  columns: Array<{
    stage: string;
    cards: BoardCard[];
  }>;
  managers: BoardManager[];
};

// A Manager's own identity and its manager's conversation. No card answers for
// the Item's own worker, so the mark on an Item title comes from here.
export type BoardManager = {
  updated_at: number;
  conversation_signal?: ConversationSignal;
  settled?: boolean;
  project_id?: string;
  project?: string;
  id: string;
  title: string;
  recap: string;
  recap_preview: string;
  tickets: ManagerWorkspaceTicket[];
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
  is_backlogged?: boolean;
  conversation_signal?: ConversationSignal;
  manager?: Principal;
  id: string;
  title: string;
  recap: string;
  recap_preview: string;
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
  manager_id: string | null;
  manager_title: string | null;
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

// One row names one durable unread conversation, independent of settlement.
export type WorkspaceMessageConversation = {
  conversationSignal?: Partial<ConversationSignal>;
  kind: "chief" | "manager" | "ticket";
  id: string;
  title: string;
  conversationId: string;
  parentId: string | null;
  parentTitle: string | null;
  settled: boolean;
};

/** Exact detail-only reply, independent of shared navigation facts. */
export type ManagerWorkspaceContent = Pick<ManagerWorkspace, "goal" | "recap">;
