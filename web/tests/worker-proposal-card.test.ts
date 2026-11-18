import { describe, expect, it, vi } from "vitest";
import { decisionIdentity } from "../src/lib/review/decision";
import type { SavedDecision } from "../src/lib/review/contracts";
import type { PendingTicketProposal } from "../src/lib/types";
import {
  buildProposalApproveDecision,
  buildProposalSendBackDecision,
  proposalContentIdentity,
  syncProposalDraftState,
  type ProposalDraftState
} from "../src/lib/review/proposalState";

function initialState(overrides: Partial<ProposalDraftState> = {}): ProposalDraftState {
  return {
    lastIdentity: null,
    lastRecalledKey: null,
    draft: "",
    revisionText: "",
    resolved: false,
    error: null,
    ...overrides
  };
}

const baseProposal: PendingTicketProposal = {
  field: "what_changes",
  body: "Original proposal body",
  proposed_by: "worker",
  created_at: 1_700_000_000,
  revision: 4
};

function proposalWith(overrides: Partial<PendingTicketProposal>): PendingTicketProposal {
  return { ...baseProposal, ...overrides };
}

describe("WorkerProposalCard draft retention and revision tracking", () => {
  it("keeps proposal content identity unchanged across revision bumps and changes on any content change", () => {
    const base = proposalContentIdentity(baseProposal);
    expect(proposalContentIdentity(proposalWith({ revision: 5 }))).toBe(base);
    expect(proposalContentIdentity(proposalWith({ field: "plan" }))).not.toBe(base);
    expect(proposalContentIdentity(proposalWith({ created_at: baseProposal.created_at + 1 }))).not.toBe(base);
    expect(proposalContentIdentity(proposalWith({ proposed_by: "chief" }))).not.toBe(base);
    expect(proposalContentIdentity(proposalWith({ body: "Revised body" }))).not.toBe(base);
  });

  it("keeps unsaved drafts and submits the latest revision after a holder change", () => {
    const recall = vi.fn((_key: string): SavedDecision | null => null);
    const identity = proposalContentIdentity(baseProposal);

    const mounted = syncProposalDraftState(
      initialState({ revisionText: "Carried note" }),
      {
        identity,
        decisionKey: decisionIdentity("t_1", 4),
        body: baseProposal.body,
        initialEditedBody: "Edited before open"
      },
      recall
    );
    expect(mounted.draft).toBe("Edited before open");
    expect(mounted.revisionText).toBe("Carried note");

    // Simulate user typing a note and editing the body before holder transfers to owner (revision 4 -> 5).
    const withUnsavedEdits: ProposalDraftState = {
      ...mounted,
      draft: "Edited body after review",
      revisionText: "Keep this draft note across holder change"
    };

    const afterHolderTransfer = syncProposalDraftState(
      withUnsavedEdits,
      {
        identity: proposalContentIdentity(proposalWith({ revision: 5 })),
        decisionKey: decisionIdentity("t_1", 5),
        body: baseProposal.body
      },
      recall
    );

    expect(recall).toHaveBeenCalledTimes(2);
    expect(recall).toHaveBeenLastCalledWith(decisionIdentity("t_1", 5));
    expect(afterHolderTransfer.draft).toBe("Edited body after review");
    expect(afterHolderTransfer.revisionText).toBe("Keep this draft note across holder change");
    expect(afterHolderTransfer.resolved).toBe(false);
    expect(afterHolderTransfer.error).toBeNull();

    const approvePayload = buildProposalApproveDecision({
      proposal: { body: baseProposal.body, revision: 5 },
      editedBody: afterHolderTransfer.draft,
      message: afterHolderTransfer.revisionText,
      finalApproval: false,
      nextCeiling: "needs_implementation",
      nextHolder: { kind: "owner", id: "owner" }
    });
    expect(approvePayload).toEqual({
      action: "approve",
      body: {
        message: "Keep this draft note across holder change",
        next_ceiling: "needs_implementation",
        next_holder: { kind: "owner", id: "owner" },
        expected_proposal_revision: 5,
        edited_body: "Edited body after review"
      }
    });

    const sendBackPayload = buildProposalSendBackDecision({
      proposal: { revision: 5 },
      message: afterHolderTransfer.revisionText
    });
    expect(sendBackPayload).toEqual({
      action: "send_back",
      body: {
        message: "Keep this draft note across holder change",
        expected_proposal_revision: 5
      }
    });
  });

  it("applies recallReviewDecision only when a saved decision exists and resets on real replacement", () => {
    const savedForRev5: SavedDecision = {
      version: 1,
      ticketId: "t_1",
      operationId: "op_5",
      command: {
        decision: "approve",
        field: "what_changes",
        expected_proposal_revision: 5,
        message: "Recovered message",
        edited_body: "Recovered body"
      }
    };
    const recall = vi.fn((key: string): SavedDecision | null =>
      key === decisionIdentity("t_1", 5) ? savedForRev5 : null
    );

    const mounted = syncProposalDraftState(
      initialState(),
      {
        identity: proposalContentIdentity(baseProposal),
        decisionKey: decisionIdentity("t_1", 4),
        body: baseProposal.body
      },
      recall
    );
    const recovered = syncProposalDraftState(
      { ...mounted, draft: "Unsaved draft", revisionText: "Unsaved note" },
      {
        identity: proposalContentIdentity(proposalWith({ revision: 5 })),
        decisionKey: decisionIdentity("t_1", 5),
        body: baseProposal.body
      },
      recall
    );
    expect(recovered.revisionText).toBe("Recovered message");
    expect(recovered.draft).toBe("Recovered body");

    // Replacing the proposal with new content clears drafts even when revision bumps again.
    const replacedBody = "A brand new proposal";
    const replaced = syncProposalDraftState(
      { ...recovered, resolved: true, error: new Error("stale") },
      {
        identity: proposalContentIdentity(proposalWith({ body: replacedBody, revision: 6 })),
        decisionKey: decisionIdentity("t_1", 6),
        body: replacedBody
      },
      recall
    );
    expect(replaced.draft).toBe(replacedBody);
    expect(replaced.revisionText).toBe("");
    expect(replaced.resolved).toBe(false);
    expect(replaced.error).toBeNull();
  });
});
