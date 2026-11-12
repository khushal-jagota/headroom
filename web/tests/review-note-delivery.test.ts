import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { deliverReviewNote, recallReviewNote, type ReviewNoteDelivery } from "../src/lib/review/noteDelivery";
import type { DeliveredMessage } from "../src/lib/conversation/wire";

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

function options(identity: string, send: (note: ReviewNoteDelivery) => Promise<DeliveredMessage>) {
  return { identity, conversationId: "conversation", text: "Use the new rules", send, onChange: () => undefined };
}

it("retires a proven refused message so the next attempt can deliver a fresh identity", async () => {
  const send = vi.fn<(note: ReviewNoteDelivery) => Promise<DeliveredMessage>>()
    .mockResolvedValueOnce({ conversation_id: "conversation", fate: "refused", refusal_reason: "write_to_backend_failed" })
    .mockResolvedValueOnce({ conversation_id: "conversation", fate: "started" });
  await expect(deliverReviewNote(options("proposal", send))).rejects.toThrow();
  expect(recallReviewNote("proposal")).toBeNull();
  await deliverReviewNote(options("proposal", send));
  expect(send.mock.calls[1][0].message.messageId).not.toBe(send.mock.calls[0][0].message.messageId);
  expect(recallReviewNote("proposal")?.status).toBe("delivered");
});

it("does not transfer uncertainty or a delivered receipt to a replacement proposal", async () => {
  const send = vi.fn<(note: ReviewNoteDelivery) => Promise<DeliveredMessage>>()
    .mockResolvedValueOnce({ conversation_id: "conversation", fate: "uncertain" })
    .mockResolvedValueOnce({ conversation_id: "conversation", fate: "started" });
  await expect(deliverReviewNote(options("proposal:1", send))).rejects.toThrow();
  await deliverReviewNote({ ...options("proposal:2", send), text: "" });
  expect(send).toHaveBeenCalledTimes(1);
  await deliverReviewNote(options("proposal:2", send));
  expect(send.mock.calls[1][0].message.messageId).not.toBe(send.mock.calls[0][0].message.messageId);
  expect(recallReviewNote("proposal:1")?.status).toBe("pending");
});

it("does not send when the original identity cannot be saved or recovered", async () => {
  const send = vi.fn<(note: ReviewNoteDelivery) => Promise<DeliveredMessage>>();
  window.sessionStorage.setItem = () => { throw new Error("storage unavailable"); };
  await expect(deliverReviewNote(options("proposal", send))).rejects.toThrow("Browser storage");
  expect(send).not.toHaveBeenCalled();
  stored.set("panels:review-note:proposal", "corrupt receipt");
  await expect(deliverReviewNote({ ...options("proposal", send), text: "" })).rejects.toThrow("Browser storage");
  expect(send).not.toHaveBeenCalled();
});

it("retires an initial HTTP rejection but retains an identity when recovery is rejected", async () => {
  const rejected = Object.assign(new Error("rejected"), { status: 409 });
  const send = vi.fn<(note: ReviewNoteDelivery) => Promise<DeliveredMessage>>()
    .mockRejectedValueOnce(rejected)
    .mockRejectedValueOnce(new Error("lost response"))
    .mockRejectedValueOnce(rejected);
  await expect(deliverReviewNote(options("proposal", send))).rejects.toThrow();
  expect(recallReviewNote("proposal")).toBeNull();
  await expect(deliverReviewNote(options("proposal", send))).rejects.toThrow();
  const originalId = recallReviewNote("proposal")?.message.messageId;
  await expect(deliverReviewNote(options("proposal", send))).rejects.toThrow();
  expect(recallReviewNote("proposal")?.message.messageId).toBe(originalId);
  expect(send.mock.calls[2][0].message.messageId).toBe(originalId);
});
