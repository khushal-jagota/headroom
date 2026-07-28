import { describe, expect, it } from "vitest";

import { readableConversationDetail } from "../src/lib/conversation/conversationDetail";

describe("Conversation detail presentation", () => {
  it("lays out structured objects and arrays", () => {
    expect(readableConversationDetail('{"a":1,"b":[2,3]}')).toBe(
      '{\n  "a": 1,\n  "b": [\n    2,\n    3\n  ]\n}'
    );
    expect(readableConversationDetail("[1,2]")).toBe("[\n  1,\n  2\n]");
  });

  it("leaves prose and malformed structured text unchanged", () => {
    expect(readableConversationDetail("ls -la /tmp")).toBe("ls -la /tmp");
    expect(readableConversationDetail("{not actually json")).toBe("{not actually json");
  });

  it("draws no detail for an absent or empty value", () => {
    expect(readableConversationDetail("")).toBeNull();
    expect(readableConversationDetail("   ")).toBeNull();
    expect(readableConversationDetail(null)).toBeNull();
    expect(readableConversationDetail(undefined)).toBeNull();
  });
});
