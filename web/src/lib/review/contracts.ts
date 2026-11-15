import type { Principal } from "../types";

/** Mirrors planner.tickets.contracts: decision operations own the transition receipt. */
export type DecisionCommand = {
  decision: "approve" | "send_back";
  field: string;
  expected_proposal_revision: number;
  message?: string | null;
  edited_body?: string | null;
  next_ceiling?: string | null;
  next_holder?: Principal | null;
};
export type DecisionReceipt = {
  operation_id: string;
  ticket_id: string;
  decision: DecisionCommand["decision"];
  field: string;
  expected_proposal_revision: number;
  outcome: "applied" | "refused";
  transition: {
    stage: string;
    ceiling: string;
    ceiling_holder: Principal;
    ticket_status: string;
    pending_proposal_revision: number;
  } | null;
  error: { code: string; message: string; detail: unknown } | null;
  created_at: number;
};
export type SavedDecision = {
  version: 1;
  ticketId: string;
  operationId: string;
  command: DecisionCommand;
};
