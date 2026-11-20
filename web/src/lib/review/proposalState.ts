import type { PendingTicketProposal, Principal } from "../types";
import type { SavedDecision } from "./contracts";
import type { SentMessagePiece } from "../conversation/wire";
import type { ReviewDecision } from "./decision";

/** Stable identity of a proposal's content, independent of holder-transfer revision bumps. */
export function proposalContentIdentity(
  proposal: Pick<PendingTicketProposal, "field" | "created_at" | "proposed_by" | "body">
): string {
  return JSON.stringify([proposal.field, proposal.created_at, proposal.proposed_by, proposal.body]);
}

export type ProposalDraftState = {
  lastIdentity: string | null;
  lastRecalledKey: string | null;
  draft: string;
  revisionText: string;
  resolved: boolean;
  error: unknown;
};

export type ProposalDraftSyncInput = {
  identity: string;
  decisionKey: string;
  body: string;
  initialEditedBody?: string;
};

/** Synchronize proposal card draft state across identity changes and revision bumps.
 *
 *  A holder change increments `proposal.revision` without changing `identity`, so unsaved
 *  `draft` and `revisionText` stay intact unless a saved decision exists for `decisionKey`.
 */
export function syncProposalDraftState(
  current: ProposalDraftState,
  next: ProposalDraftSyncInput,
  recall: (decisionKey: string) => SavedDecision | null
): ProposalDraftState {
  let { lastIdentity, lastRecalledKey, draft, revisionText, resolved, error } = current;
  if (next.identity !== lastIdentity) {
    const first = lastIdentity === null;
    lastIdentity = next.identity;
    lastRecalledKey = null;
    draft = first ? next.initialEditedBody ?? next.body : next.body;
    if (!first) revisionText = "";
    resolved = false;
    error = null;
  }
  if (next.decisionKey !== lastRecalledKey) {
    lastRecalledKey = next.decisionKey;
    try {
      const pending = recall(next.decisionKey);
      if (pending) {
        revisionText = pending.command.message ?? "";
        draft = pending.command.edited_body ?? next.body;
      }
    } catch (recoveryError) {
      error = recoveryError;
    }
  }
  return { lastIdentity, lastRecalledKey, draft, revisionText, resolved, error };
}

/** Build the canonical approve payload from the card's current revision and preserved drafts. */
export function buildProposalApproveDecision(input: {
  proposal: Pick<PendingTicketProposal, "body" | "revision">;
  editedBody: string;
  message: string;
  attachments?: SentMessagePiece[];
  finalApproval: boolean;
  nextCeiling?: string;
  nextHolder: Principal;
}): ReviewDecision {
  return {
    action: "approve",
    body: {
      message: input.message || null,
      ...(input.attachments?.length ? { attachments: input.attachments } : {}),
      ...(!input.finalApproval ? { next_ceiling: input.nextCeiling, next_holder: input.nextHolder } : {}),
      expected_proposal_revision: input.proposal.revision,
      ...(input.editedBody !== input.proposal.body ? { edited_body: input.editedBody } : {})
    }
  };
}

/** Build the canonical send-back payload from the card's current revision and preserved note. */
export function buildProposalSendBackDecision(input: {
  proposal: Pick<PendingTicketProposal, "revision">;
  message: string;
  attachments?: SentMessagePiece[];
}): ReviewDecision {
  return {
    action: "send_back",
    body: {
      message: input.message,
      ...(input.attachments?.length ? { attachments: input.attachments } : {}),
      expected_proposal_revision: input.proposal.revision
    }
  };
}
