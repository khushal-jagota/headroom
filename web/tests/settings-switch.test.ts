import { describe, expect, it } from "vitest";
import { createOptimisticSwitch, toggleOptimistically } from "../src/lib/settings/optimisticSwitch";

// A Settings switch moves before its save returns. These are the ways it can end up
// showing a value that the server does not hold.
describe("a switch that saves at once", () => {
  const failure = "Could not save. Panels did not change this notification.";

  it("moves at once and stays when the save succeeds", async () => {
    const state = createOptimisticSwitch(false);
    let release!: () => void;
    const saving = toggleOptimistically(state, true, () => new Promise<void>(resolve => (release = resolve)), failure);
    expect(state.shown).toBe(true);
    release();
    await saving;
    expect(state).toMatchObject({ shown: true, error: null });
  });

  it("moves back and says why when the save fails", async () => {
    const state = createOptimisticSwitch(true);
    await toggleOptimistically(state, false, () => Promise.reject(new Error("offline")), failure);
    expect(state).toMatchObject({ shown: true, error: `${failure} offline` });
    await toggleOptimistically(state, false, () => Promise.resolve(), failure);
    expect(state).toMatchObject({ shown: false, error: null });
  });

  it("does not let an older failed press undo a newer one", async () => {
    const state = createOptimisticSwitch(false);
    let rejectFirst!: (error: Error) => void;
    const first = toggleOptimistically(state, true, () => new Promise((_, reject) => (rejectFirst = reject)), failure);
    const second = toggleOptimistically(state, false, () => Promise.resolve(), failure);
    await second;
    rejectFirst(new Error("late"));
    await first;
    expect(state).toMatchObject({ shown: false, error: null });
  });
});
