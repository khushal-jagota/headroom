export type AnyRecord = Record<string, any>;

// The served Worker-type manifest response (GET /api/worker-types). Re-exported here
// for the resource fetch typing; the derived Lifecycle + per-type manifest shapes are
// imported directly from lifecycle.ts at call sites.
export type { WorkerTypesResponse } from "./lifecycle";

export type GatewayStatus = {
  available: boolean;
};

export type ChatHistoryMessage = {
  role: string;
  text: string;
  created_at: number;
};

export type ChatHistoryResponse = {
  messages: ChatHistoryMessage[];
  session_key?: string | null;
};

export type ChatStateMessage = {
  id: number;
  role: "human" | "assistant" | "system" | "worker" | string;
  text: string;
  created_at: number;
  turn_id?: string | null;
};

export type ChatActivityEntry = {
  id: number;
  action_identity?: string | null;
  category: "thinking" | "tool" | "command" | string;
  label: string;
  lifecycle_state: "running" | "complete" | string;
  started_at: number;
  updated_at: number;
  completed_at?: number | null;
};

export type ChatTurn = {
  id: string;
  entity_id: string;
  origin: "human" | "worker" | "system" | string;
  mode: "message" | "command" | "worker_step" | string;
  status: "running" | "complete" | "errored" | "interrupted" | string;
  phase: "queued" | "thinking" | "doing" | "responding" | "settled" | string;
  activity_label?: string | null;
  activity_entries: ChatActivityEntry[];
  output_role: "assistant" | "system" | string;
  output_text: string;
  session_key?: string | null;
  error?: string | null;
  started_at: number;
  updated_at: number;
  completed_at?: number | null;
};

export type ChatStateResponse = {
  messages: ChatStateMessage[];
  active_turn?: ChatTurn | null;
  session_key?: string | null;
};

export type ChatImageUploadResponse = {
  reference: string;
  markdown: string;
};

export type StartChatTurnBody = {
  text: string;
  mode: "message" | "command";
  image_references?: string[];
};

export type SprintSummary = {
  id: string;
  name: string;
};

export type SprintsResponse = {
  sprints: SprintSummary[];
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
  active: boolean;
  href: string;
};

export type BlocksTarget = {
  target_id: string;
  target_kind: "ticket" | "sprint_item";
  title: string;
  active: boolean;
  href: string;
};

export type BlockerSummary = {
  blocked: boolean;
  blocked_by: BlockedByTicket[];
  blocks: BlocksTarget[];
};

export type Implementer =
  | "khushal"
  | "panels_worker"
  | "hermes_codex"
  | "hermes_claude";

export type TicketDetail = {
  id: string;
  title: string;
  worker_type: string;
  stage: string;
  ceiling: string;
  at_cap: string;
  priority: string;
  deadline?: string | null;
  project_id?: string | null;
  project?: string | null;
  sprint_id?: string | null;
  effective_sprint_id?: string | null;
  sprint_item_id?: string | null;
  ticket_status?: string;
  implementer: Implementer | null;
  chat_session_key?: string | null;
  day_ids?: string[];
  blocked?: boolean;
  blocker_summary?: BlockerSummary;
  recap?: string | null;
  fields: Record<string, TicketField>;
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

export type CurrentSprintResponse = {
  sprint: AnyRecord | null;
  groups: Record<string, AnyRecord[]>;
  loose_tickets: AnyRecord[];
};

export type ReviewTicketDecision = {
  ticket_id: string;
  field: string;
  title: string;
  waiting_since: number;
};

export type ReviewResponse = {
  ticket_decisions: ReviewTicketDecision[];
  running_worker_count: number;
};

export type BoardResponse = {
  columns: Array<{
    stage: string;
    cards: AnyRecord[];
  }>;
};

export type DayResponse = {
  id: string;
  focus?: string | null;
  brief_take?: string | null;
  watchout?: string | null;
  if_today_lands?: string | null;
};

export type BacklogResponse = {
  items: AnyRecord[];
};

export type IdeasResponse = {
  ideas: AnyRecord[];
};

export type CommandCatalog = {
  categories: Array<{ name: string; pairs: Array<[string, string]> }>;
  skills: Array<[string, string]>;
  canon: Record<string, string>;
  sub: Record<string, string[]>;
};
