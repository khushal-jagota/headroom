import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { decisionId, decisionIdentity, recallReviewDecision, submitReviewDecision, type ReviewDecision, type ReviewDecisionBody } from "../src/lib/review/decision";

let stored: Map<string, string>;
beforeEach(() => {
  stored = new Map();
  vi.stubGlobal("window", { sessionStorage: {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value),
    removeItem: (key: string) => stored.delete(key)
  } });
});
afterEach(() => vi.unstubAllGlobals());
const identity = decisionIdentity("ticket", 2);
function decision(message = "  Keep the scope.\n\nRetain Unicode: café.  "): ReviewDecision {
  return { action: "approve", body: {
    decision_id: decisionId(identity, "approve"), expected_proposal_revision: 2,
    message, next_ceiling: "needs_implementation", next_holder: { kind: "owner", id: "owner" }, edited_body: "An edited proposal"
  } };
}

it("recovers the exact decision after a lost response and keeps the identity across remounts", async () => {
  const original = decision();
  const send = vi.fn<(body: ReviewDecisionBody) => Promise<unknown>>()
    .mockRejectedValueOnce(new Error("lost response"))
    .mockResolvedValueOnce({});
  await expect(submitReviewDecision(identity, original, send)).rejects.toThrow("lost response");
  const recovered = recallReviewDecision(decisionIdentity("ticket", 2));
  expect(recovered).toEqual(original);
  await submitReviewDecision(identity, recovered!, send);
  expect(send.mock.calls[1][0]).toEqual(send.mock.calls[0][0]);
  expect(send.mock.calls[1][0].message).toBe(original.body.message);
  expect(recallReviewDecision(identity)).toBeNull();
  expect(recallReviewDecision(decisionIdentity("ticket", 3))).toBeNull();
});

it("never substitutes a changed note or opposite action for an ambiguous decision", async () => {
  const send = vi.fn<(body: ReviewDecisionBody) => Promise<unknown>>().mockRejectedValue(new Error("lost response"));
  await expect(submitReviewDecision(identity, decision(), send)).rejects.toThrow();
  await expect(submitReviewDecision(identity, decision("changed"), send)).rejects.toThrow("original note");
  const rejected: ReviewDecision = { action: "send_back", body: { ...decision().body, decision_id: decisionId(identity, "send_back") } };
  await expect(submitReviewDecision(identity, rejected, send)).rejects.toThrow("original note");
  expect(send).toHaveBeenCalledTimes(1);
});

it("permits correction after a proven initial HTTP rejection but retains ambiguous recovery", async () => {
  const rejected = Object.assign(new Error("rejected"), { status: 409 });
  const send = vi.fn<(body: ReviewDecisionBody) => Promise<unknown>>()
    .mockRejectedValueOnce(rejected)
    .mockRejectedValueOnce(new Error("lost response"))
    .mockRejectedValueOnce(rejected);
  await expect(submitReviewDecision(identity, decision(), send)).rejects.toThrow();
  expect(recallReviewDecision(identity)).toBeNull();
  await expect(submitReviewDecision(identity, decision("corrected"), send)).rejects.toThrow();
  await expect(submitReviewDecision(identity, decision("corrected"), send)).rejects.toThrow();
  expect(recallReviewDecision(identity)?.body.message).toBe("corrected");
});

it("does not dispatch a decision when its exact request cannot be saved or recovered", async () => {
  const send = vi.fn<(body: ReviewDecisionBody) => Promise<unknown>>();
  window.sessionStorage.setItem = () => { throw new Error("storage unavailable"); };
  await expect(submitReviewDecision(identity, decision(), send)).rejects.toThrow("Browser storage");
  stored.set(`panels:review-decision:${identity}`, "corrupt request");
  await expect(submitReviewDecision(identity, decision(), send)).rejects.toThrow("Browser storage");
  expect(send).not.toHaveBeenCalled();
});
