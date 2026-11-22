import { describe, expect, it } from "vitest";
import { AskEntranceTracker, type AskEntranceContext } from "../src/lib/conversation/askEntrance";

describe("waiting ask entrances", () => {
  it("restores a delayed initial status in place, then animates a genuinely new request", () => {
    const tracker = new AskEntranceTracker();
    const context: AskEntranceContext = { source: {}, ready: false, visible: true, mode: "rest", requestKey: null };
    expect(tracker.update(context)).toBe(false);
    expect(tracker.update({ ...context, ready: true, requestKey: "question:existing" })).toBe(false);
    expect(tracker.update({ ...context, ready: true, requestKey: null })).toBe(false);
    expect(tracker.update({ ...context, ready: true, requestKey: "permission:new" })).toBe(true);
    // The question body can arrive after its status identity without losing the live entrance.
    expect(tracker.update({ ...context, ready: true, requestKey: "permission:new" })).toBe(true);
  });

  it("does not replay an entrance after a tab, dock, or reader change", () => {
    const tracker = new AskEntranceTracker();
    let context: AskEntranceContext = { source: {}, ready: true, visible: true, mode: "opened", requestKey: null };
    tracker.update(context);
    context = { ...context, requestKey: "key:new" };
    expect(tracker.update(context)).toBe(true);
    expect(tracker.update({ ...context, visible: false })).toBe(false);
    expect(tracker.update(context)).toBe(false);
    expect(tracker.update({ ...context, mode: "rest" })).toBe(false);
    expect(tracker.update(context)).toBe(false);
    expect(tracker.update({ ...context, source: {} })).toBe(false);
  });
});
