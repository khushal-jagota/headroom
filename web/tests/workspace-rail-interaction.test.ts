import { afterEach, describe, expect, it, vi } from "vitest";
import {
  clampRailWidth, createRailHoverController, railWidthForKey, readRailWidth,
  saveRailWidth, RAIL_WIDTH_STORAGE_KEY
} from "../src/lib/workspaceRailInteraction";

afterEach(() => vi.useRealTimers());

describe("approved rail interaction", () => {
  it("clamps pointer and keyboard sizes and preserves the local preference", () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); },
      removeItem: (key: string) => { values.delete(key); }
    };
    expect(readRailWidth(storage)).toBeNull();
    saveRailWidth(storage, 700);
    expect(readRailWidth(storage)).toBe(520);
    expect(clampRailWidth(10)).toBe(232);
    expect(railWidthForKey(300, "ArrowLeft")).toBe(292);
    expect(railWidthForKey(300, "ArrowRight", true)).toBe(324);
    expect(railWidthForKey(518, "ArrowRight")).toBe(520);
    expect(railWidthForKey(300, "Enter")).toBeNull();
    saveRailWidth(storage, null);
    expect(readRailWidth(storage)).toBeNull();
    for (const invalid of ["NaN", "Infinity", "", " "]) {
      values.set(RAIL_WIDTH_STORAGE_KEY, invalid);
      expect(readRailWidth(storage)).toBeNull();
    }
    const unavailable = {
      getItem: () => { throw new Error("disabled"); },
      setItem: () => { throw new Error("disabled"); },
      removeItem: () => { throw new Error("disabled"); }
    };
    expect(readRailWidth(unavailable)).toBeNull();
    expect(() => saveRailWidth(unavailable, 300)).not.toThrow();
    expect(() => saveRailWidth(unavailable, null)).not.toThrow();
  });

  it("opens after 200ms, swaps immediately and allows a 90ms crossing grace", () => {
    vi.useFakeTimers();
    const show = vi.fn();
    const hide = vi.fn();
    const hover = createRailHoverController<string>(show, hide);
    hover.enter("first");
    vi.advanceTimersByTime(199);
    expect(show).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(show).toHaveBeenLastCalledWith("first", false);
    hover.leave();
    vi.advanceTimersByTime(89);
    hover.enter("second");
    expect(show).toHaveBeenLastCalledWith("second", true);
    vi.advanceTimersByTime(1);
    expect(hide).not.toHaveBeenCalled();
    hover.leave();
    hover.keepOpen();
    vi.advanceTimersByTime(90);
    expect(hide).not.toHaveBeenCalled();
    hover.leave();
    vi.advanceTimersByTime(90);
    expect(hide).toHaveBeenCalledTimes(1);
    hover.enter("third");
    vi.advanceTimersByTime(200);
    expect(show).toHaveBeenLastCalledWith("third", false);
    hover.dispose();
  });

  it("cancels pending opens and closes when disposed", () => {
    vi.useFakeTimers();
    const show = vi.fn();
    const hide = vi.fn();
    const hover = createRailHoverController(show, hide);
    hover.enter("first");
    hover.leave();
    vi.advanceTimersByTime(300);
    expect(show).not.toHaveBeenCalled();
    expect(hide).not.toHaveBeenCalled();
    hover.enter("second");
    hover.dispose();
    vi.runAllTimers();
    expect(show).not.toHaveBeenCalled();
  });
});
