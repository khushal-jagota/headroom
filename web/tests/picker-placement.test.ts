import { describe, expect, it } from "vitest";
import { pickerRoom, placePickerPanel, type PickerBox } from "../src/lib/pickerPlacement";

const anchor = (top: number, bottom: number, left = 900): PickerBox => ({
  left, top, right: left + 88, bottom
});

describe("picker placement", () => {
  it("opens upward from a footer and keeps a margin inside the viewport", () => {
    const room = pickerRoom({ viewportWidth: 1100, viewportHeight: 520 });
    const placed = placePickerPanel({
      anchor: anchor(460, 492, 940),
      panel: { width: 404, height: 220 },
      boundary: room,
      prefer: "above"
    });
    expect(placed.side).toBe("above");
    expect(placed.top).toBeGreaterThanOrEqual(8);
    expect(placed.top + placed.maxHeight).toBeLessThanOrEqual(492);
    expect(placed.left).toBeGreaterThanOrEqual(8);
    expect(placed.left + Math.min(404, placed.maxWidth)).toBeLessThanOrEqual(1092);
  });

  it("flips above a trigger that sits on the composer", () => {
    const room = pickerRoom({ viewportWidth: 1280, viewportHeight: 640, dockTop: 536 });
    const placed = placePickerPanel({
      anchor: anchor(480, 520, 980),
      panel: { width: 404, height: 220 },
      boundary: room,
      prefer: "below"
    });
    expect(placed.side).toBe("above");
    expect(placed.top + placed.maxHeight).toBeLessThanOrEqual(528);
    expect(placed.left + 404).toBeLessThanOrEqual(1272);
  });

  it("keeps a downward opening when the trigger has room below", () => {
    const room = pickerRoom({ viewportWidth: 1280, viewportHeight: 900 });
    const placed = placePickerPanel({
      anchor: anchor(120, 156, 80),
      panel: { width: 296, height: 180 },
      boundary: room,
      prefer: "below"
    });
    expect(placed).toMatchObject({ side: "below", top: 160, left: 80, maxHeight: 180 });
  });

  it("shrinks to the visible scroll container", () => {
    const room = pickerRoom({
      viewportWidth: 1400,
      viewportHeight: 900,
      container: { left: 280, top: 40, right: 720, bottom: 860 }
    });
    const placed = placePickerPanel({
      anchor: anchor(180, 210, 640),
      panel: { width: 404, height: 200 },
      boundary: room,
      prefer: "below"
    });
    expect(placed.left + Math.min(404, placed.maxWidth)).toBeLessThanOrEqual(720);
    expect(placed.left).toBeGreaterThanOrEqual(280);
    expect(placed.maxWidth).toBeLessThanOrEqual(440);
  });
});
