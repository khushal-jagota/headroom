import { describe, expect, it } from "vitest";

import {
  armedChangeFor,
  deliveryModeIsOffered,
  deliveryOptionsFor,
  effortOptionsFor,
  fateSentence,
  hasArmedChange,
  modelDetail,
  preselectedValue,
  sendBodyFor
} from "../src/lib/conversation/composer";
import type { RunValues } from "../src/lib/conversation/composer";
import type { OutgoingMessage } from "../src/lib/conversation/outgoing";

const message: OutgoingMessage = {
  messageId: "message-1",
  content: [{ piece: "text", text: "go" }],
  senderLabel: "owner",
  mode: "run_when_free",
  sentAtUnixMilliseconds: 1_700_000_000_123,
  knownFate: "nothing_yet"
};

const current: RunValues = { model: "opus", reasoningEffort: "high" };

describe("Conversation composer delivery", () => {
  it("offers steering only for a backend that supports it", () => {
    expect(deliveryOptionsFor("hermes").map((option) => option.mode))
      .toEqual(["run_when_free", "send_now", "steer"]);
    for (const backend of ["codex", "claude", null] as const) {
      expect(deliveryOptionsFor(backend).map((option) => option.mode))
        .toEqual(["run_when_free", "send_now"]);
      expect(deliveryModeIsOffered(backend, "steer")).toBe(false);
    }
    expect(deliveryModeIsOffered("hermes", "steer")).toBe(true);
  });

  it("arms only differing values on a delivery that can carry changes", () => {
    expect(armedChangeFor(
      current,
      { model: null, reasoningEffort: null },
      "run_when_free"
    )).toEqual({});
    expect(armedChangeFor(
      current,
      { model: "opus", reasoningEffort: "high" },
      "run_when_free"
    )).toEqual({});
    expect(armedChangeFor(
      current,
      { model: "sonnet", reasoningEffort: null },
      "run_when_free"
    )).toEqual({ model_change: "sonnet" });
    expect(armedChangeFor(
      current,
      { model: "sonnet", reasoningEffort: "low" },
      "send_now"
    )).toEqual({ model_change: "sonnet", reasoning_effort_change: "low" });
    expect(hasArmedChange(
      current,
      { model: "sonnet", reasoningEffort: null },
      "run_when_free"
    )).toBe(true);
  });

  it("does not carry a model or effort change when steering", () => {
    const picked = { model: "sonnet", reasoningEffort: "low" };
    expect(armedChangeFor(current, picked, "steer")).toEqual({});
    expect(hasArmedChange(current, picked, "steer")).toBe(false);
  });

  it("sends the exact already-drawn message and armed model", () => {
    expect(sendBodyFor({
      message,
      current,
      picked: { model: "sonnet", reasoningEffort: null },
      conversationId: "conversation-1"
    })).toEqual({
      conversation_id: "conversation-1",
      content: [{ piece: "text", text: "go" }],
      sender_label: "owner",
      mode: "run_when_free",
      sender_message_id: "message-1",
      sent_at_unix_milliseconds: 1_700_000_000_123,
      model: "sonnet"
    });
  });

  it("omits unpicked run values", () => {
    expect(sendBodyFor({
      message: { ...message, mode: "send_now" },
      current,
      picked: { model: null, reasoningEffort: null },
      conversationId: null
    })).toEqual({
      conversation_id: null,
      content: [{ piece: "text", text: "go" }],
      sender_label: "owner",
      mode: "send_now",
      sender_message_id: "message-1",
      sent_at_unix_milliseconds: 1_700_000_000_123
    });
  });

  it("names a backend only when the message creates a Conversation", () => {
    const picked = {
      model: null,
      reasoningEffort: null,
      backendKey: "claude" as const
    };
    expect(sendBodyFor({
      message,
      current: { model: null, reasoningEffort: null },
      picked,
      conversationId: null
    })).toMatchObject({ conversation_id: null, backend_key: "claude" });
    expect(sendBodyFor({
      message,
      current,
      picked: { ...picked, model: "sonnet" },
      conversationId: "conversation-1"
    })).toEqual({
      conversation_id: "conversation-1",
      content: [{ piece: "text", text: "go" }],
      sender_label: "owner",
      mode: "run_when_free",
      sender_message_id: "message-1",
      sent_at_unix_milliseconds: 1_700_000_000_123,
      model: "sonnet"
    });
  });

  it("preselects the concrete value already in force", () => {
    expect(preselectedValue("sonnet", "opus")).toBe("sonnet");
    expect(preselectedValue(null, "opus")).toBe("opus");
    expect(preselectedValue(null, null)).toBeNull();
    expect(preselectedValue(null, undefined)).toBeNull();
  });

  it("uses model-specific effort options when the catalog supplies them", () => {
    const models = [
      { model_id: "opus", reasoning_effort_options: ["low", "high"] },
      { model_id: "haiku", reasoning_effort_options: [] },
      { model_id: "quiet" }
    ];
    const backendOptions = ["low", "medium", "high"];

    expect(effortOptionsFor(models, "opus", backendOptions)).toEqual(["low", "high"]);
    expect(effortOptionsFor(models, "haiku", backendOptions)).toEqual([]);
    expect(effortOptionsFor(models, "quiet", backendOptions)).toEqual(backendOptions);
    expect(effortOptionsFor(models, "unknown", backendOptions)).toEqual(backendOptions);
    expect(effortOptionsFor(models, null, backendOptions)).toEqual(backendOptions);
  });

  it("shows model detail only when the catalog supplies it", () => {
    const models = [
      { model_id: "opus", detail: "opus → claude-opus-5" },
      { model_id: "plain" }
    ];
    expect(modelDetail(models, "opus")).toBe("opus → claude-opus-5");
    expect(modelDetail(models, "plain")).toBeNull();
    expect(modelDetail(models, "unknown")).toBeNull();
    expect(modelDetail(models, null)).toBeNull();
  });

  it("reports delivery fates in the composer's visible wording", () => {
    expect(fateSentence({ fate: "started" })).toBeNull();
    expect(fateSentence({ fate: "queued", queue_position: 2 })).toBe("queued · position 2");
    expect(fateSentence({ fate: "injected" })).toBe("steered into the running turn");
    expect(fateSentence({
      fate: "refused",
      refusal_reason: "backend_cannot_steer"
    })).toBe("not delivered · this backend cannot take text into a running turn");
  });
});
