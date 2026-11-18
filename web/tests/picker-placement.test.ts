import { describe, expect, it } from "vitest";
import { placePickerPanel } from "../src/lib/pickerPlacement";

describe("picker panel placement", () => {
  it("caps a downward panel a margin above the composer", () => {
    // Beneath the trigger, down to 8px above the composer, is shorter than the panel.
    // Above is shorter still, so it stays down and shrinks to the room it has.
    expect(placePickerPanel({
      trigger: { top: 200, bottom: 232, left: 48 },
      panel: { width: 296, height: 280 },
      viewport: { width: 1100, height: 560 },
      composerTop: 470,
      preferBelow: true
    })).toEqual({ left: 48, top: 236, height: 226, down: true });
  });

  it("flips above a footer trigger in a short window", () => {
    expect(placePickerPanel({
      trigger: { top: 500, bottom: 532, left: 760 },
      panel: { width: 296, height: 240 },
      viewport: { width: 1100, height: 560 },
      composerTop: null,
      preferBelow: false
    })).toEqual({ left: 760, top: 256, height: 240, down: false });
  });

  it("keeps a wide panel inside the viewport", () => {
    const placed = placePickerPanel({
      trigger: { top: 80, bottom: 112, left: 900 },
      panel: { width: 296, height: 40 },
      viewport: { width: 1100, height: 700 },
      composerTop: 640,
      preferBelow: true
    });
    expect(placed).toEqual({ left: 796, top: 116, height: 40, down: true });
  });

  it("uses the preferred side when both fit", () => {
    const input = {
      trigger: { top: 80, bottom: 112, left: 40 },
      panel: { width: 200, height: 40 },
      viewport: { width: 800, height: 700 },
      composerTop: 640
    };
    expect(placePickerPanel({ ...input, preferBelow: true })?.down).toBe(true);
    expect(placePickerPanel({ ...input, preferBelow: false })?.top).toBe(36);
  });

  it("does not place a trigger that is covered by the composer", () => {
    expect(placePickerPanel({
      trigger: { top: 500, bottom: 530, left: 40 },
      panel: { width: 200, height: 80 },
      viewport: { width: 800, height: 700 },
      composerTop: 480,
      preferBelow: true
    })).toBeNull();
  });
});
