import { describe, expect, it } from "vitest";

import {
  eligibleOwnerReadSequence,
  type OwnerReadEligibility
} from "../src/lib/conversation/ownerRead";

const openAndReadable: OwnerReadEligibility = {
  conversationState: "opened",
  documentIsVisible: true,
  windowIsFocused: true,
  transcriptLatestSequence: 7,
  snapshot: { latestSequence: 7, ownerReadThroughSequence: 3 }
};

describe("Conversation owner read eligibility", () => {
  it("does not advance a closed pane", () => {
    expect(eligibleOwnerReadSequence({ ...openAndReadable, conversationState: "rest" })).toBeNull();
  });

  it("does not advance a hidden document", () => {
    expect(eligibleOwnerReadSequence({ ...openAndReadable, documentIsVisible: false })).toBeNull();
  });

  it("does not advance an unfocused window", () => {
    expect(eligibleOwnerReadSequence({ ...openAndReadable, windowIsFocused: false })).toBeNull();
  });

  it("does not use a snapshot position before its transcript rows arrive", () => {
    expect(eligibleOwnerReadSequence({
      ...openAndReadable,
      transcriptLatestSequence: 0,
      snapshot: { latestSequence: 7, ownerReadThroughSequence: 0 }
    })).toBeNull();
  });

  it.each([null, "peeked", "opened"] as const)(
    "advances an open, visible, focused %s pane only to the transcript position",
    (conversationState) => {
      expect(eligibleOwnerReadSequence({
        ...openAndReadable,
        conversationState,
        transcriptLatestSequence: 5,
        snapshot: { latestSequence: 9, ownerReadThroughSequence: 3 }
      })).toBe(5);
    }
  );
});
