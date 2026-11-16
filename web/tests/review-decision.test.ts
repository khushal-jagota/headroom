import { get } from "svelte/store";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { decisionRefusals, decisionIdentity, recallReviewDecision, recoverReviewDecision, recoverSavedReviewDecisions, savedReviewDecisions, submitReviewDecision, validateDecisionResponse, type ReviewDecision } from "../src/lib/review/decision";
import type { SavedDecision } from "../src/lib/review/contracts";
import { queryClient } from "../src/lib/queryClient";
import { fetchJson } from "../src/lib/api";

vi.mock("../src/lib/api", async importOriginal => {
  const original = await importOriginal<typeof import("../src/lib/api")>();
  return { ...original, fetchJson: vi.fn() };
});
vi.mock("../src/lib/queryClient", () => ({ queryClient: { invalidateQueries: vi.fn().mockResolvedValue(undefined) } }));
let stored: Map<string, string>;
let session: Map<string, string>;
const transport = vi.mocked(fetchJson);
function storage(values: Map<string, string>) {
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
    key: (index: number) => [...values.keys()][index] ?? null,
    get length() { return values.size; }
  };
}
beforeEach(() => {
  stored = new Map(); session = new Map();
  vi.stubGlobal("window", { localStorage: storage(stored), sessionStorage: storage(session) });
  vi.stubGlobal("navigator", { locks: { request: async (_key: string, action: () => Promise<unknown>) => action() } });
  transport.mockReset();
});
afterEach(() => vi.unstubAllGlobals());
const identity = decisionIdentity("ticket", 2);
function decision(message = "  Keep the scope.\n\nRetain Unicode: café.  "): ReviewDecision {
  return { action: "approve", body: {
    expected_proposal_revision: 2, message, next_ceiling: "needs_implementation",
    next_holder: { kind: "owner", id: "owner" }, edited_body: "An edited proposal"
  } };
}
const absent = { receipt_contract: 1, receipt: null };
function receipt(saved: SavedDecision, outcome: "applied" | "refused" = "applied") {
  return { receipt_contract: 1, receipt: {
    operation_id: saved.operationId, ticket_id: saved.ticketId,
    decision: saved.command.decision, field: saved.command.field,
    expected_proposal_revision: saved.command.expected_proposal_revision,
    outcome, transition: outcome === "applied" ? {
      stage: "needs_implementation", ceiling: "needs_implementation", ceiling_holder: { kind: "owner", id: "owner" },
      ticket_status: "agent", pending_proposal_revision: 3
    } : null,
    error: outcome === "refused" ? { code: "validation", message: "This changed, review again", detail: {} } : null,
    created_at: 123
  } };
}
async function pending() {
  transport.mockResolvedValueOnce(absent).mockRejectedValueOnce(new Error("lost answer"));
  await expect(submitReviewDecision("ticket", "what_changes", decision())).rejects.toThrow("lost answer");
  return recallReviewDecision(identity)!;
}

it("looks up the exact operation without cache after lost POST and accepts its receipt after the Ticket advances", async () => {
  const original = await pending();
  transport.mockResolvedValueOnce(receipt(original));
  await recoverReviewDecision(original);
  expect(transport).toHaveBeenCalledTimes(3);
  expect(transport.mock.calls[2]).toEqual([`/api/tickets/ticket/decision-operations/${original.operationId}`, { cache: "no-store", signal: expect.any(AbortSignal) }]);
  expect(recallReviewDecision(identity)).toBeNull();
});

it("retains closed-tab recovery in durable storage and replays only its frozen command after supported absence", async () => {
  const original = await pending();
  // A new tab has no session data but retains this origin's local storage.
  vi.stubGlobal("window", { localStorage: storage(stored), sessionStorage: storage(new Map()) });
  expect(savedReviewDecisions()).toEqual([original]);
  transport.mockResolvedValueOnce(absent).mockResolvedValueOnce(receipt(original));
  await recoverReviewDecision(savedReviewDecisions()[0]);
  expect(transport.mock.calls[3][1]).toMatchObject({ method: "POST", body: original.command });
  expect(transport.mock.calls[3][1]?.body).toEqual(original.command);
  expect(original.command.message).toBe(decision().body.message);
});

it("never substitutes a changed note or opposite action while a decision is unknown", async () => {
  await pending();
  await expect(submitReviewDecision("ticket", "what_changes", decision("changed"))).rejects.toThrow("previous decision");
  await expect(submitReviewDecision("ticket", "what_changes", { ...decision(), action: "send_back" })).rejects.toThrow("previous decision");
  expect(transport).toHaveBeenCalledTimes(2);
});

it("does not treat HTTP errors, unsupported contracts, malformed receipts or POST-null as absence", async () => {
  const original = await pending();
  for (const value of [{}, { receipt_contract: 2, receipt: null }, { ...receipt(original), receipt: { ...receipt(original).receipt, operation_id: "someone-else" } }]) {
    transport.mockResolvedValueOnce(value);
    await expect(recoverReviewDecision(original)).rejects.toThrow("unconfirmed");
  }
  transport.mockRejectedValueOnce(Object.assign(new Error("HTTP 404"), { status: 404 }));
  await expect(recoverReviewDecision(original)).rejects.toThrow("HTTP 404");
  expect(transport.mock.calls.filter(call => call[1]?.method === "POST")).toHaveLength(1);
  transport.mockResolvedValueOnce(absent).mockResolvedValueOnce(absent);
  await expect(recoverReviewDecision(original)).rejects.toThrow("unconfirmed");
  expect(recallReviewDecision(identity)).toEqual(original);
});

it("surfaces a terminal changed-proposal refusal and permits a distinct corrected operation", async () => {
  const original = await pending();
  transport.mockResolvedValueOnce(receipt(original, "refused"));
  vi.mocked(queryClient.invalidateQueries).mockImplementationOnce(async () => {
    // Refresh can remove the old proposal card. Its refusal already lives above it.
    expect(get(decisionRefusals)).toContainEqual(expect.objectContaining({
      saved: original, error: expect.objectContaining({ message: "Approval did not apply: This changed, review again" })
    }));
  });
  await expect(recoverReviewDecision(original)).rejects.toThrow("This changed, review again");
  expect(recallReviewDecision(identity)).toBeNull();
  transport.mockResolvedValueOnce(absent).mockRejectedValueOnce(new Error("next unknown"));
  await expect(submitReviewDecision("ticket", "what_changes", decision("corrected"))).rejects.toThrow();
  expect(recallReviewDecision(identity)?.operationId).not.toBe(original.operationId);
});

it("re-checks saved decisions quietly: an unanswered one stays saved, a refused one reaches its proposal", async () => {
  const original = await pending();
  transport.mockRejectedValueOnce(new Error("still no answer"));
  await expect(recoverSavedReviewDecisions()).resolves.toBeUndefined();
  expect(recallReviewDecision(identity)).toEqual(original);
  expect(get(decisionRefusals)).toEqual([]);
  // Every lookup and send carries a time limit, so a request that never answers cannot hold a decision.
  expect(transport.mock.calls.at(-1)?.[1]?.signal).toBeInstanceOf(AbortSignal);
  transport.mockResolvedValueOnce(receipt(original, "refused"));
  await recoverSavedReviewDecisions();
  expect(recallReviewDecision(identity)).toBeNull();
  expect(get(decisionRefusals)).toHaveLength(1);
});

it("requires receipt identity and exclusive terminal outcomes", async () => {
  const original = await pending();
  for (const changes of [{ ticket_id: "other" }, { decision: "send_back" }, { field: "brief" }, { expected_proposal_revision: 3 }, { transition: null }, { error: { code: "oops", message: "bad" } }]) {
    expect(() => validateDecisionResponse({ receipt_contract: 1, receipt: { ...receipt(original).receipt, ...changes } }, original, false)).toThrow("unconfirmed");
  }
});

it("fails before any request when storage is corrupt, unavailable, legacy, or tab coordination is unavailable", async () => {
  window.localStorage.setItem = () => { throw new Error("unavailable"); };
  await expect(submitReviewDecision("ticket", "what_changes", decision())).rejects.toThrow("Browser storage");
  stored.set(`panels:decision-operation:v1:${identity}`, "corrupt");
  await expect(submitReviewDecision("ticket", "what_changes", decision())).rejects.toThrow("Browser storage");
  stored.clear();
  session.set(`panels:review-decision:${identity}`, JSON.stringify(decision()));
  await expect(submitReviewDecision("ticket", "what_changes", decision())).rejects.toThrow("older route");
  session.clear();
  vi.stubGlobal("navigator", {});
  await expect(submitReviewDecision("ticket", "what_changes", decision())).rejects.toThrow("across tabs");
  expect(transport).not.toHaveBeenCalled();
});

it("keeps a terminal receipt authoritative when refresh fails and freezes mutable choices before lookup", async () => {
  const incoming = decision();
  transport.mockImplementationOnce(async () => {
    incoming.body.next_holder!.id = "changed-after-save";
    return absent;
  }).mockImplementationOnce(async (_path, options) => {
    const saved = recallReviewDecision(identity)!;
    expect(options?.body).toEqual(saved.command);
    expect(saved.command.next_holder?.id).toBe("owner");
    vi.mocked(queryClient.invalidateQueries).mockRejectedValueOnce(new Error("refresh failed"));
    return receipt(saved);
  });
  await expect(submitReviewDecision("ticket", "what_changes", incoming)).resolves.toMatchObject({ outcome: "applied" });
  expect(recallReviewDecision(identity)).toBeNull();
});
