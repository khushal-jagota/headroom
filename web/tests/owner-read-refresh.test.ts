import { describe, expect, it } from "vitest";
import { ownerReadCoversNewMessage } from "../src/lib/conversation/ownerRead";
import type { ConversationEvent } from "../src/lib/conversation/wire";

function message(sequence: number): ConversationEvent {
  return { conversation_id: "a", sequence, kind: "message_to_owner", created_at: 1,
    payload: { text: "Reply", sender_label: "Worker",
      sender: { kind: "ticket", id: "t_a" }, recipient: { kind: "owner", id: "owner" } } };
}
const tool: ConversationEvent = { conversation_id: "a", sequence: 5, created_at: 1,
  kind: "tool_call_started", payload: { tool_call_id: "tool", title: "Read",
    tool_kind: "read", detail: null } };

describe("confirmed owner-read refresh interval", () => {
  it("includes the accepted upper boundary and excludes the previous read boundary", () => {
    expect(ownerReadCoversNewMessage([message(4)], 3, 4)).toBe(true);
    expect(ownerReadCoversNewMessage([message(4)], 4, 5)).toBe(false);
  });
  it("does not credit later messages beyond the accepted watermark", () => {
    expect(ownerReadCoversNewMessage([message(6)], 0, 5)).toBe(false);
  });
  it("does not refresh for tools, empty intervals, or an older acknowledgment", () => {
    expect(ownerReadCoversNewMessage([tool], 0, 5)).toBe(false);
    expect(ownerReadCoversNewMessage([], 0, 5)).toBe(false);
    expect(ownerReadCoversNewMessage([message(4)], 5, 4)).toBe(false);
  });
  it("finds owner messages among raw runtime rows", () => {
    expect(ownerReadCoversNewMessage([message(4), tool], 3, 5)).toBe(true);
    expect(ownerReadCoversNewMessage([message(4), tool], 4, 5)).toBe(false);
  });
});
