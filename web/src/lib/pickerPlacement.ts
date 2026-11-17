// Where a picker panel sits. The panel is measured in viewport coordinates and
// kept inside the room it was given: the visible page, inset, and any scroll
// container or composer dock the caller has already folded into that room.

export type PickerBox = {
  left: number;
  top: number;
  right: number;
  bottom: number;
};

export type PickerPlacement = {
  top: number;
  left: number;
  maxHeight: number;
  maxWidth: number;
  side: "above" | "below";
};

export function pickerRoom(input: {
  viewportWidth: number;
  viewportHeight: number;
  /** The visible box of the scroll container the trigger lives in. */
  container?: PickerBox | null;
  /** Top of a composer dock the panel should stay above, when the trigger is above it. */
  dockTop?: number | null;
  margin?: number;
}): PickerBox {
  const margin = input.margin ?? 8;
  let box: PickerBox = {
    left: margin,
    top: margin,
    right: Math.max(margin, input.viewportWidth - margin),
    bottom: Math.max(margin, input.viewportHeight - margin)
  };
  if (input.container) box = intersectBox(box, input.container);
  if (input.dockTop != null) box = { ...box, bottom: Math.min(box.bottom, input.dockTop - margin) };
  return normalizeBox(box);
}

export function placePickerPanel(input: {
  anchor: PickerBox;
  panel: { width: number; height: number };
  boundary: PickerBox;
  gap?: number;
  prefer?: "above" | "below";
}): PickerPlacement {
  const gap = input.gap ?? 4;
  const prefer = input.prefer ?? "above";
  const box = normalizeBox(input.boundary);
  const spaceAbove = input.anchor.top - gap - box.top;
  const spaceBelow = box.bottom - gap - input.anchor.bottom;
  const fits = (space: number) => space >= input.panel.height;
  let side: "above" | "below";
  if (prefer === "below" && fits(spaceBelow)) side = "below";
  else if (prefer === "above" && fits(spaceAbove)) side = "above";
  else if (spaceBelow === spaceAbove) side = prefer;
  else side = spaceBelow > spaceAbove ? "below" : "above";
  const available = Math.max(0, side === "below" ? spaceBelow : spaceAbove);
  const maxWidth = Math.max(0, box.right - box.left);
  const maxHeight = Math.min(Math.max(0, input.panel.height), available > 0 ? available : Math.max(0, box.bottom - box.top));
  const width = Math.min(Math.max(0, input.panel.width), maxWidth);
  let top = side === "below" ? input.anchor.bottom + gap : input.anchor.top - gap - maxHeight;
  if (top < box.top) top = box.top;
  if (top + maxHeight > box.bottom) top = Math.max(box.top, box.bottom - maxHeight);
  let left = input.anchor.left;
  if (left + width > box.right) left = box.right - width;
  if (left < box.left) left = box.left;
  return { top, left, maxHeight, maxWidth, side };
}

function intersectBox(a: PickerBox, b: PickerBox): PickerBox {
  return {
    left: Math.max(a.left, b.left),
    top: Math.max(a.top, b.top),
    right: Math.min(a.right, b.right),
    bottom: Math.min(a.bottom, b.bottom)
  };
}

function normalizeBox(box: PickerBox): PickerBox {
  return {
    left: Math.min(box.left, box.right),
    top: Math.min(box.top, box.bottom),
    right: Math.max(box.left, box.right),
    bottom: Math.max(box.top, box.bottom)
  };
}
