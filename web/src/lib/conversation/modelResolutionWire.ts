/** Shared factual projection of model_resolution/contracts.py. No admission authority. */
import type { TicketLocale } from "../ticketMachines";
import type { ConversationBackendKey } from "./wire";

/** Advisory metadata read. Writes and admission independently revalidate. */
export type TicketBackendScope = {
  ticket_id: string;
  locale?: TicketLocale;
  employee_backend?: ConversationBackendKey;
  employee_launch_model?: string;
  /** Omitted preserves saved effort; null explicitly requests provider default. */
  employee_launch_reasoning_effort?: string | null;
};

/** Existing creation-draft authority; it never creates a Ticket. */
export type DraftBackendScope = {
  ticket_id?: never;
  worker_type: string;
  locale?: TicketLocale;
  employee_backend?: ConversationBackendKey;
  employee_launch_model?: string;
  employee_launch_reasoning_effort?: string | null;
  title?: string;
  brief?: string;
  deadline?: string | null;
  project?: string | null;
  project_id?: string | null;
  manager?: import("./wire").Principal;
  blocked_by_ticket_ids?: string[];
  ceiling?: string | null;
  ceiling_holder?: import("./wire").Principal;
};

export type BackendScopeRequest = TicketBackendScope | DraftBackendScope;
