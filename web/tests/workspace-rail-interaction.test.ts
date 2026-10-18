import { afterEach, describe, expect, it, vi } from "vitest";
import {
  clampRailWidth, createRailHoverController, createRailPreviewInput, railWidthForKey, readRailWidth,
  saveRailWidth, RAIL_WIDTH_STORAGE_KEY
} from "../src/lib/workspaceRailInteraction";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

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
    hover.enter("keyboard", true);
    expect(show).toHaveBeenLastCalledWith("keyboard", true);
    hover.dispose();
  });

  it("gates pointer hover and cancels touch holds before they can open stale targets", () => {
    vi.useFakeTimers();
    const show = vi.fn();
    const hide = vi.fn();
    let fineHover = true;
    vi.stubGlobal("window", {matchMedia:() => ({matches:fineHover})});
    const target = {isConnected:true, matches:() => true} as unknown as HTMLElement;
    const pointer = (pointerType: string, clientX = 10, clientY = 10) =>
      ({pointerType, clientX, clientY, pointerId:1, currentTarget:target}) as unknown as PointerEvent;
    const input = createRailPreviewInput(show, hide);
    input.pointerEnter(pointer("touch"));
    expect(show).not.toHaveBeenCalled();
    fineHover = false;
    input.pointerEnter(pointer("mouse"));
    expect(show).not.toHaveBeenCalled();
    fineHover = true;
    input.pointerEnter(pointer("mouse"));
    expect(show).toHaveBeenLastCalledWith(target, false);
    show.mockClear();
    input.down(pointer("touch"));
    input.focus({currentTarget:target} as unknown as FocusEvent);
    vi.advanceTimersByTime(399);
    expect(show).not.toHaveBeenCalled();
    input.move(pointer("touch", 30));
    vi.advanceTimersByTime(1);
    expect(show).not.toHaveBeenCalled();
    input.down(pointer("touch")); input.end();
    vi.advanceTimersByTime(400);
    expect(show).not.toHaveBeenCalled();
    input.down(pointer("touch")); input.cancel();
    vi.advanceTimersByTime(400);
    expect(show).not.toHaveBeenCalled();
    input.down(pointer("touch"));
    vi.advanceTimersByTime(400);
    expect(show).toHaveBeenLastCalledWith(target, true);
    input.end();
    input.leave();
    const click = {preventDefault:vi.fn()} as unknown as MouseEvent;
    expect(input.click(click)).toBe(false);
    expect(click.preventDefault).toHaveBeenCalledOnce();
    // A canceled or abandoned completed hold must not eat the next selection.
    for (const reset of [() => input.cancel(), () => input.dispose(), () => input.down(pointer("mouse"))]) {
      input.down(pointer("touch"));
      vi.advanceTimersByTime(400);
      reset();
      expect(input.click()).toBe(true);
    }
    show.mockClear();
    input.down(pointer("touch"));
    (target as unknown as {isConnected:boolean}).isConnected = false;
    vi.advanceTimersByTime(400);
    expect(show).not.toHaveBeenCalled();
    input.dispose();
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
