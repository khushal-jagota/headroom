import { describe, expect, it } from "vitest";

import { conversationSignalPresentation } from "../src/lib/conversationSignalPresentation";

const resting = {
  conversation_id: "conv-chief",
  needs_me: false,
  agent_working: false,
  unread_position: 0
};

describe("conversation signal presentation", () => {
  it("presents a conversation with nothing unread as nothing waiting", () => {
    expect(conversationSignalPresentation(resting, {})).toEqual({
      state: "upcoming",
      ariaLabel: "Nothing waiting"
    });
  });

  it("presents a running turn", () => {
    expect(
      conversationSignalPresentation({ ...resting, agent_working: true }, {})
    ).toEqual({ state: "current-running", ariaLabel: "Agent working" });
  });

  it("gives a needs-you ask precedence over the running turn", () => {
    expect(
      conversationSignalPresentation(
        { ...resting, needs_me: true, agent_working: true },
        {}
      )
    ).toEqual({ state: "needs-me", ariaLabel: "Needs you" });
  });

  it("presents an unread position past this browser's watermark as unseen", () => {
    expect(
      conversationSignalPresentation(
        { ...resting, unread_position: 9 },
        { "conv-chief": 8 }
      )
    ).toEqual({
      state: "current-awaiting-approval",
      ariaLabel: "Unseen agent reply"
    });
  });

  it("presents an unread position at this browser's watermark as seen", () => {
    expect(
      conversationSignalPresentation(
        { ...resting, unread_position: 9 },
        { "conv-chief": 9 }
      )
    ).toEqual({ state: "reply-seen", ariaLabel: "Agent reply seen" });
  });

  it("gives an unseen needs-you position precedence over the running turn", () => {
    expect(
      conversationSignalPresentation(
        { ...resting, needs_me_position: 4, agent_working: true },
        { "conv-chief": 3 }
      )
    ).toEqual({ state: "needs-me", ariaLabel: "Needs you" });
  });

  it("gives an unseen needs-you position precedence over an unseen reply", () => {
    expect(
      conversationSignalPresentation(
        { ...resting, needs_me_position: 4, unread_position: 20 },
        { "conv-chief": 3 }
      )
    ).toEqual({ state: "needs-me", ariaLabel: "Needs you" });
  });

  it("presents a reply behind a seen needs-you position as unseen", () => {
    expect(
      conversationSignalPresentation(
        { ...resting, needs_me_position: 4, unread_position: 20 },
        { "conv-chief": 4 }
      )
    ).toEqual({
      state: "current-awaiting-approval",
      ariaLabel: "Unseen agent reply"
    });
  });

  it("presents a watermark past both positions as seen", () => {
    expect(
      conversationSignalPresentation(
        { ...resting, needs_me_position: 4, unread_position: 20 },
        { "conv-chief": 20 }
      )
    ).toEqual({ state: "reply-seen", ariaLabel: "Agent reply seen" });
  });

  it("says nothing waiting when a row has no conversation to hold a position", () => {
    expect(
      conversationSignalPresentation(
        { ...resting, conversation_id: null, needs_me_position: 4, unread_position: 20 },
        {}
      )
    ).toEqual({ state: "upcoming", ariaLabel: "Nothing waiting" });
  });
});
