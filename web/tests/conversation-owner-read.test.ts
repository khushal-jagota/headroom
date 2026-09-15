import { describe, expect, it } from "vitest";

import {
  eligibleOwnerReadSequence,
  watchOwnerReadAttention,
  type OwnerReadAttention,
  type OwnerReadDocumentAttentionTarget,
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

  it("does not trust cached focus after focus moves into an iframe", () => {
    const documentTarget = new FakeAttentionDocument();
    const cachedAttention = { windowIsFocused: true };
    documentTarget.focused = false;

    expect(eligibleOwnerReadSequence({
      ...openAndReadable,
      windowIsFocused: cachedAttention.windowIsFocused && documentTarget.hasFocus()
    })).toBeNull();
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

class FakeAttentionTarget {
  private readonly listeners = new Map<string, Set<() => void>>();

  addEventListener(type: string, listener: () => void): void {
    const listeners = this.listeners.get(type) ?? new Set<() => void>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: () => void): void {
    this.listeners.get(type)?.delete(listener);
  }

  dispatch(type: string): void {
    for (const listener of this.listeners.get(type) ?? []) listener();
  }
}

class FakeAttentionDocument extends FakeAttentionTarget
  implements OwnerReadDocumentAttentionTarget {
  visibilityState = "visible";
  focused = true;

  hasFocus(): boolean {
    return this.focused;
  }
}

describe("Conversation owner read attention", () => {
  it("publishes focus and visibility changes until its listener is removed", () => {
    const documentTarget = new FakeAttentionDocument();
    const windowTarget = new FakeAttentionTarget();
    const received: OwnerReadAttention[] = [];
    const stop = watchOwnerReadAttention(
      documentTarget,
      windowTarget,
      (attention) => received.push(attention)
    );

    documentTarget.focused = false;
    windowTarget.dispatch("blur");
    documentTarget.visibilityState = "hidden";
    documentTarget.dispatch("visibilitychange");
    documentTarget.visibilityState = "visible";
    documentTarget.focused = true;
    windowTarget.dispatch("focus");

    expect(received).toEqual([
      { documentIsVisible: true, windowIsFocused: true },
      { documentIsVisible: true, windowIsFocused: false },
      { documentIsVisible: false, windowIsFocused: false },
      { documentIsVisible: true, windowIsFocused: true }
    ]);

    stop();
    documentTarget.focused = false;
    windowTarget.dispatch("blur");
    expect(received).toHaveLength(4);
  });
});
