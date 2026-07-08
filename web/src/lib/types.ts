export type AnyRecord = Record<string, any>;

export type GatewayStatus = {
  available: boolean;
};

export type SprintSummary = {
  id: string;
  name: string;
};

export type SprintsResponse = {
  sprints: SprintSummary[];
};

export type TicketField = {
  value?: string | null;
  notes?: string | null;
  proposal?: {
    body: string;
    proposed_by: string;
  } | null;
};

export type TicketDetail = {
  id: string;
  title: string;
  state: string;
  ceiling: string;
  at_cap: string;
  priority: string;
  deadline?: string | null;
  project?: string | null;
  sprint_id?: string | null;
  effective_sprint_id?: string | null;
  sprint_item_id?: string | null;
  ticket_status?: string;
  blocked?: boolean;
  recap?: string | null;
  fields: Record<string, TicketField>;
};

export type CurrentSprintResponse = {
  sprint: AnyRecord | null;
  groups: Record<string, AnyRecord[]>;
  loose_tickets: AnyRecord[];
};

export type QueueEntry = {
  entity_id: string;
  entity_type: "ticket" | "item";
  kind: string;
  title: string;
};

export type QueuesResponse = {
  approvals: QueueEntry[];
};

export type BoardResponse = {
  columns: Array<{
    state: string;
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
