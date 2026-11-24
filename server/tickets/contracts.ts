import { PlannerError, type Principal } from '../core/contracts.ts';
import type { NumericJSON } from '../core/http/numeric_json.ts';
import type { TicketWorkflowDefinition, StageOwnershipMode } from '../worker_types/contracts.ts';
export type { StageOwnershipMode };
export const TITLE_MAX_CHARS = 200;
export const NO_FURTHER = 'none';
export class StoredTicketIntegrityError extends PlannerError {
  constructor(ticket_id: string, reason: string) {
    super('validation', 'Ticket integrity failure', { ticket_id, reason });
  }
}
export type DecisionRevision = number | bigint;
export type DecisionHolder = Principal & { kind: 'owner' | 'chief' | 'manager' };
export interface DecisionCommand {
  decision: 'approve' | 'send_back';
  field: string;
  expected_proposal_revision: DecisionRevision;
  message?: string | null;
  edited_body?: string | null;
  next_ceiling?: string | null;
  next_holder?: DecisionHolder | null;
  /** Pictures and files sent with the note, as conversation message pieces. */
  attachments?: readonly Record<string, unknown>[];
}
export interface DecisionTransition {
  stage: string;
  ceiling: string;
  ceiling_holder: DecisionHolder;
  ticket_status: TicketStatus;
  pending_proposal_revision: number;
}
export interface DecisionReceipt {
  operation_id: string;
  ticket_id: string;
  decision: DecisionCommand['decision'];
  field: string;
  expected_proposal_revision: DecisionRevision;
  outcome: 'applied' | 'refused';
  transition: DecisionTransition | null;
  error: { code: string; message: string; detail: Record<string, unknown> } | null;
  created_at: number;
}
export interface DecisionOperationResponse {
  receipt_contract: 1;
  receipt: NumericJSON<DecisionReceipt> | null;
}
export type NextCeiling = string;
export type TicketLocale = 'vps' | 'macos';
export type WorkerStepClaim = 'none' | 'out' | 'errored';
export type TicketStatus = 'empty' | 'blocked' | 'agent' | 'awaiting_approval' | 'errored';
/** What a Ticket needs next, and from whom. The order is the list sort order. */
export const TICKET_NEEDS = [
  'owner_approval',
  'owner_answer',
  'owner_stage',
  'owner_reply',
  'agent_approval',
  'working',
  'ready',
  'blocked',
  'errored',
  'backlog',
  'done',
] as const;
export type TicketNeed = (typeof TICKET_NEEDS)[number];
/** The Ticket list's scope: `live` is neither backlogged nor terminal. */
export const TICKET_LIST_SCOPES = ['live', 'backlog', 'done', 'all'] as const;
export type TicketListScope = (typeof TICKET_LIST_SCOPES)[number];
export type TicketApprovalMethod = 'explicit_accept' | 'auto_accept' | 'unknown';
export interface TicketFieldApproval {
  method: TicketApprovalMethod;
  principal: Principal | null;
  display_label: string;
}
export type TicketFieldValues = Record<string, string>;
export type TicketFieldApprovals = Record<string, TicketFieldApproval>;
export interface PendingTicketProposal {
  field: string;
  body: string;
  proposed_by: string;
  created_at: number;
}
export interface EmployeeLaunchConfiguration {
  employee_backend: string;
  employee_launch_model: string | null;
  employee_launch_reasoning_effort: string | null;
}
export interface ResolvedTicketCreationContext {
  machine_id: string;
  locale: TicketLocale;
  launch_configuration: EmployeeLaunchConfiguration;
  project_id: string | null;
}
export interface Ticket extends EmployeeLaunchConfiguration {
  id: string;
  title: string;
  worker_type: string | null;
  workflow: TicketWorkflowDefinition;
  stage: string;
  deadline: string | null;
  project_id: string | null;
  project_name: string | null;
  manager_id: string | null;
  manager_kind: string;
  manager_title: string | null;
  recap: string;
  recap_updated_at: number | null;
  recap_stage_changed: boolean;
  ceiling: string;
  ceiling_holder: Principal;
  ticket_status: TicketStatus;
  worker_step_claim: WorkerStepClaim;
  worker_step_claim_revision: number;
  conversation_id: string | null;
  field_values: TicketFieldValues;
  field_approvals: TicketFieldApprovals;
  pending_proposal: PendingTicketProposal | null;
  created_at: number;
  updated_at: number;
  locale: TicketLocale;
  machine_id: string;
  is_backlogged: boolean;
  pending_proposal_revision: number;
}
export interface TicketEdit {
  title?: string;
  deadline?: string | null;
  project_id?: string | null;
  manager_id?: string | null;
  manager?: Principal;
  recap?: string;
  field_values?: TicketFieldValues;
  ceiling?: string;
  ceiling_holder?: Principal;
}
export interface ReviewTransferNotice {
  ticket_id: string;
  ticket_title: string;
  previous_holder: Principal;
  new_holder: Principal;
  new_holder_name: string;
  notify_previous_holder: boolean;
  mover_name: string | null;
}
export interface TicketDeletion {
  ticket_id: string;
  title: string;
  manager_ids: string[];
  linked_ticket_ids: string[];
}
export interface TicketListFilters {
  manager: Principal | null;
  scope: TicketListScope;
  needs: readonly TicketNeed[];
  stages: readonly string[];
  excluded_stages: readonly string[];
  worker_types: readonly string[];
  search: string | null;
}
/** The flag filters the iPhone app and older `panels` CLIs send instead of `scope` and `needs`. */
export interface LegacyTicketListFilters {
  manager: Principal | null;
  ticket_statuses: readonly string[];
  excluded_ticket_statuses: readonly string[];
  is_backlogged: boolean | null;
  include_terminal: boolean;
  active: boolean;
  stages: readonly string[];
  excluded_stages: readonly string[];
  worker_types: readonly string[];
  search: string | null;
}
