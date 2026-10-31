/** Shared factual projection of model_resolution/contracts.py. No admission authority. */
import type { TicketLocale } from "../ticketMachines";
import type { ConversationBackendKey } from "./wire";

export type SelectionKind = "concrete" | "provider_alias" | "panels_alias" | "legacy_unclassified";
export type ValidationState = "compatible" | "incompatible" | "unresolved";
export type ValidationReason =
  | "supported" | "model_incompatible" | "effort_incompatible" | "catalog_unavailable"
  | "offline" | "timeout" | "context_changed" | "legacy_unscoped" | "identity_unproved"
  | "configuration_scope_unknown";
export type CorrectiveAction =
  | "none" | "refresh_catalog" | "reconnect_destination" | "choose_model"
  | "verify_launch_context" | "classify_selection";

export type ModelSelection = {
  destination: TicketLocale;
  backend_key: ConversationBackendKey;
  submitted_token: string;
  selection_kind: SelectionKind;
  reasoning_effort: string | null;
  selection_revision: string;
};

export type CatalogWorkspaceBinding = {
  workspace_folder: string;
  workspace_path_policy: "legacy_home_mapping" | "native_absolute";
  workspace_home_relative: string | null;
};

export type ModelExecutorIdentity = {
  protocol_version: number;
  application_revision: string;
  spool_format_version: number;
  executor_incarnation: string;
  connection_generation: number;
};

export type LaunchContext = {
  context_ref: string;
  destination: TicketLocale;
  backend_key: ConversationBackendKey;
  executor_identity: ModelExecutorIdentity | null;
  workspace: CatalogWorkspaceBinding;
  localized_workspace_folder: string;
  executable_identity: string | null;
  executable_version: string | null;
  provider_scope: string | null;
  account_generation: { account_id: string; last_refresh: string } | null;
};

export type CatalogEvidenceReference = {
  observation_id: string;
  observation_revision: string;
  context: LaunchContext;
  observed_at: number;
  attempt_id: string;
  clock_uncertainty_seconds: number | null;
};

export type CatalogUnavailable = {
  kind: "unavailable";
  backend_key: ConversationBackendKey;
  destination: TicketLocale;
  attempted_context: LaunchContext | null;
  reason: ValidationReason;
  corrective_action: CorrectiveAction;
  detail: string | null;
  observed_at: number;
  retained_evidence: CatalogEvidenceReference | null;
};

export type CanonicalModelValidation = {
  selection: ModelSelection;
  state: ValidationState;
  reason: ValidationReason;
  corrective_action: CorrectiveAction;
  launch_token: string | null;
};

/** Advisory metadata read. Writes and admission independently revalidate. */
export type TicketBackendScope = {
  ticket_id: string;
  locale?: TicketLocale;
  employee_backend?: ConversationBackendKey;
  employee_launch_model?: string;
  /** Omitted preserves saved effort; null explicitly requests provider default. */
  employee_launch_reasoning_effort?: string | null;
  selection_kind?: SelectionKind;
};

/** Existing creation-draft authority; it never creates a Ticket. */
export type DraftBackendScope = {
  ticket_id?: never;
  worker_type: string;
  locale?: TicketLocale;
  employee_backend?: ConversationBackendKey;
  employee_launch_model?: string;
  employee_launch_reasoning_effort?: string | null;
  selection_kind?: SelectionKind;
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
