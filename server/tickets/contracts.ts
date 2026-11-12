import type { Principal } from '../core/contracts.ts';
import type { TicketWorkflowDefinition, StageOwnershipMode } from '../worker_types/contracts.ts';
export type { StageOwnershipMode };
export const TITLE_MAX_CHARS = 200;
export const NO_FURTHER = 'none';
export type NextCeiling = string;
export type TicketLocale = 'vps' | 'macos';
export type WorkerStepClaim = 'none' | 'out' | 'errored';
export type TicketStatus = 'empty' | 'blocked' | 'agent' | 'awaiting_approval' | 'errored';
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
    manager?: Principal | null;
    stages?: readonly string[];
    excluded_stages?: readonly string[];
    ticket_statuses?: readonly TicketStatus[];
    excluded_ticket_statuses?: readonly TicketStatus[];
    include_terminal?: boolean;
    search?: string | null;
    is_backlogged?: boolean | null;
    worker_types?: readonly string[];
}
export interface WorkAttention {
    awaiting_reply: boolean;
    awaiting_answer: boolean;
    awaiting_approval: boolean;
    awaiting_agent_approval: boolean;
    assigned: boolean;
    agent_state: string;
}
